"""
Brawl Stars Tournament Bot - PERFECTED VERSION WITH FIXED DELETION
"""

import os
import asyncio
import random
import logging
import aiosqlite
import requests
import json
import base64
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from functools import wraps

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, 
    KeyboardButton, ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters,
    CallbackQueryHandler, ConversationHandler
)

# -----------------------
# CONFIG
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = {7665378359, 6548564636}
DATABASE = "tournaments.db"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# -----------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Conversation states
(REG_TEAM_NAME, REG_LEADER_USERNAME, REG_WAIT_ROSTER) = range(3)

# =======================
# DECORATORS & UTILITIES
# =======================

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user and user.id not in ADMINS:
            if update.callback_query:
                await update.callback_query.answer("⛔ Admin only command", show_alert=True)
            else:
                await update.effective_message.reply_text("⛔ Admin-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def make_keyboard(items: List[Tuple[str, str]]):
    kb = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in items]
    return InlineKeyboardMarkup(kb)

# =======================
# BACKUP SYSTEM
# =======================

gist_id = None

def backup_database_sync():
    """Backup database to GitHub Gist"""
    if not GITHUB_TOKEN:
        logger.info("🔒 GitHub token not set, skipping backup")
        return
    
    try:
        if not os.path.exists(DATABASE):
            logger.info("📊 Database file not found, skipping backup")
            return
            
        with open(DATABASE, 'rb') as f:
            db_data = base64.b64encode(f.read()).decode()
        
        backup_data = {"database": db_data}
        headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Content-Type': 'application/json'}
        
        global gist_id
        if gist_id:
            url = f'https://api.github.com/gists/{gist_id}'
            data = {"files": {"bot_backup.json": {"content": json.dumps(backup_data)}}}
            response = requests.patch(url, headers=headers, json=data)
            if response.status_code == 200:
                logger.info("🔄 Database backup updated successfully")
            else:
                logger.warning(f"⚠️ Backup update failed: {response.status_code}")
        else:
            url = 'https://api.github.com/gists'
            data = {"public": False, "files": {"bot_backup.json": {"content": json.dumps(backup_data)}}}
            response = requests.post(url, headers=headers, json=data)
            if response.status_code == 201:
                gist_id = response.json()['id']
                logger.info("💾 New backup created successfully")
            else:
                logger.warning(f"⚠️ Backup creation failed: {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Backup error: {e}")

async def backup_database():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, backup_database_sync)

def restore_from_backup_sync():
    """Restore database from GitHub Gist"""
    if not GITHUB_TOKEN:
        logger.info("🔒 GitHub token not set, skipping restore")
        return
    
    try:
        headers = {'Authorization': f'token {GITHUB_TOKEN}'}
        response = requests.get('https://api.github.com/gists', headers=headers)
        if response.status_code == 200:
            gists = response.json()
            for gist in gists:
                if 'bot_backup.json' in gist['files']:
                    global gist_id
                    gist_id = gist['id']
                    gist_response = requests.get(f'https://api.github.com/gists/{gist_id}', headers=headers)
                    if gist_response.status_code == 200:
                        result = gist_response.json()
                        backup_data = json.loads(result['files']['bot_backup.json']['content'])
                        db_data = base64.b64decode(backup_data['database'])
                        with open(DATABASE, 'wb') as f:
                            f.write(db_data)
                        logger.info("🔄 Database restored from backup")
                        return
        logger.info("📭 No backup found to restore")
    except Exception as e:
        logger.error(f"❌ Restore error: {e}")

async def restore_from_backup():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, restore_from_backup_sync)

# =======================
# DATABASE FUNCTIONS
# =======================

async def init_db():
    """Initialize database with proper error handling"""
    try:
        await restore_from_backup()
        async with aiosqlite.connect(DATABASE) as db:
            await db.executescript("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                max_teams INTEGER NOT NULL,
                status TEXT DEFAULT 'registration',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                leader_username TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tournament_id) REFERENCES tournaments (id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS roster_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                telegram_file_id TEXT,
                FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS bracket_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_index INTEGER NOT NULL,
                match_index INTEGER NOT NULL,
                teamA_id INTEGER,
                teamB_id INTEGER,
                winner_team_id INTEGER,
                FOREIGN KEY (tournament_id) REFERENCES tournaments (id) ON DELETE CASCADE,
                FOREIGN KEY (teamA_id) REFERENCES teams (id) ON DELETE CASCADE,
                FOREIGN KEY (teamB_id) REFERENCES teams (id) ON DELETE CASCADE,
                FOREIGN KEY (winner_team_id) REFERENCES teams (id) ON DELETE CASCADE
            );
            """)
            await db.commit()
        logger.info("📊 Database initialized successfully")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")

async def db_execute(query: str, params: tuple = ()):
    """Execute database query with backup"""
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(query, params)
        await db.commit()
    await backup_database()

async def db_fetchone(query: str, params: tuple = ()):
    """Fetch single row from database"""
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(query, params)
        return await cur.fetchone()

async def db_fetchall(query: str, params: tuple = ()):
    """Fetch all rows from database"""
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(query, params)
        return await cur.fetchall()

async def count_registered(tid: int) -> int:
    """Count registered teams in tournament"""
    row = await db_fetchone("SELECT COUNT(*) FROM teams WHERE tournament_id = ?", (tid,))
    return row[0] if row else 0

async def get_tournament_max_teams(tid: int) -> int:
    """Get maximum teams for tournament"""
    row = await db_fetchone("SELECT max_teams FROM tournaments WHERE id = ?", (tid,))
    return row[0] if row else 0

async def is_tournament_full(tid: int) -> bool:
    """Check if tournament is full"""
    count = await count_registered(tid)
    max_teams = await get_tournament_max_teams(tid)
    return count >= max_teams

async def get_team_name(team_id: int) -> str:
    """Get team name by ID"""
    if not team_id:
        return "TBD"
    team = await db_fetchone("SELECT name FROM teams WHERE id = ?", (team_id,))
    return team[0] if team else "Unknown"

# =======================
# DELETION FUNCTIONS - FIXED
# =======================

async def delete_tournament_complete(tid: int) -> bool:
    """Completely delete tournament and all related data"""
    try:
        async with aiosqlite.connect(DATABASE) as db:
            # Enable foreign keys
            await db.execute("PRAGMA foreign_keys = ON")
            
            # Delete tournament (cascades to teams, roster_files, bracket_matches)
            await db.execute("DELETE FROM tournaments WHERE id = ?", (tid,))
            await db.commit()
            
        await backup_database()
        logger.info(f"✅ Tournament {tid} deleted successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error deleting tournament {tid}: {e}")
        return False

async def delete_team_complete(team_id: int) -> bool:
    """Completely delete team and all related data"""
    try:
        async with aiosqlite.connect(DATABASE) as db:
            # Enable foreign keys
            await db.execute("PRAGMA foreign_keys = ON")
            
            # Get tournament ID before deletion for status update
            tournament_row = await db_fetchone("SELECT tournament_id FROM teams WHERE id = ?", (team_id,))
            
            # Delete team (cascades to roster_files)
            await db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
            
            # Update tournament status if needed
            if tournament_row:
                tid = tournament_row[0]
                count = await count_registered(tid)
                max_teams = await get_tournament_max_teams(tid)
                
                if count < max_teams:
                    await db.execute("UPDATE tournaments SET status = 'registration' WHERE id = ?", (tid,))
            
            await db.commit()
            
        await backup_database()
        logger.info(f"✅ Team {team_id} deleted successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error deleting team {team_id}: {e}")
        return False

# =======================
# BOT HANDLERS - USER
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message and main menu keyboard"""
    user = update.effective_user
    
    # 🎉 ENHANCED GREETING MESSAGE - FIXED HTML
    greeting = f"""
✨ <b>WELCOME TO BRAWL STARS TOURNAMENT BOT!</b> ✨

🎮 <i>Hello {user.first_name}! Ready to dominate the tournament?</i> 🎮

<b>🏆 TOURNAMENT FEATURES:</b>
• 📋 Browse active tournaments
• ✅ Register your team with roster photos  
• 👀 View other teams and their rosters
• ⚔️ Follow live bracket progress
• 📊 Track your player statistics

<b>🎯 QUICK COMMANDS:</b>
/info ID - Tournament details
/myteams - Your registered teams  
/stats - Your player statistics
/search NAME - Find tournaments

<b>🚀 READY TO PLAY?</b>
Use the buttons below to get started! The arena awaits! ⚔️
    """
    
    # 🎯 FIXED KEYBOARD - ALWAYS SHOWS
    kb = [
        [KeyboardButton("📋 Tournaments"), KeyboardButton("🔎 View Teams")],
        [KeyboardButton("ℹ️ Help"), KeyboardButton("📊 My Stats")]
    ]
    
    # Add admin button only for admins
    if user.id in ADMINS:
        kb.append([KeyboardButton("🛠️ Admin Panel")])
    
    reply_markup = ReplyKeyboardMarkup(kb, resize_keyboard=True, is_persistent=True)
    
    await update.message.reply_text(
        greeting, 
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    text = """
🤖 <b>BRAWL STARS TOURNAMENT BOT - COMPLETE GUIDE</b> 🤖

<b>🎮 FOR PLAYERS:</b>
• Use <b>"📋 Tournaments"</b> to browse and register
• Use <b>"🔎 View Teams"</b> to see registered teams
• <b>/myteams</b> - View your registered teams
• <b>/stats</b> - View your player statistics  
• <b>/info ID</b> - Get tournament details
• <b>/search NAME</b> - Search tournaments

<b>🛠️ FOR ADMINS:</b>
• Use <b>"🛠️ Admin Panel"</b> for admin controls
• <b>/create NAME TEAMS</b> - Create tournament
• Manage brackets and record match results

<b>📞 NEED HELP?</b>
Contact the tournament organizers!

<b>🎯 PRO TIP:</b> Keep this keyboard visible for quick access!
    """
    await update.message.reply_text(text, parse_mode="HTML")

async def show_tournaments_keyboard():
    """Create tournaments keyboard"""
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    items = []
    for tid, name, max_teams, status in rows:
        count = await count_registered(tid)
        status_emojis = {
            'registration': '📝',
            'full': '🔒', 
            'in_progress': '⚔️',
            'finished': '🏁'
        }
        status_emoji = status_emojis.get(status, '❓')
        label = f"{name} ({count}/{max_teams}) {status_emoji}"
        items.append((label, f"view_t_{tid}"))
    return make_keyboard(items) if items else make_keyboard([("No tournaments available", "none")])

async def tournaments_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tournaments list"""
    kb = await show_tournaments_keyboard()
    await update.message.reply_text("🏆 Available tournaments:", reply_markup=kb)

async def show_tournament_detail(tid: int, user_id: int):
    """Show tournament details"""
    row = await db_fetchone("SELECT name, max_teams, status FROM tournaments WHERE id = ?", (tid,))
    if not row:
        return "❌ Tournament not found.", None
    
    name, max_teams, status = row
    count = await count_registered(tid)
    
    status_emojis = {
        'registration': '📝',
        'full': '🔒', 
        'in_progress': '⚔️',
        'finished': '🏁'
    }
    status_emoji = status_emojis.get(status, '❓')
    
    text = f"""🏆 <b>{name}</b>
📊 ID: {tid}
👥 Teams: {count}/{max_teams}
{status_emoji} Status: {status}"""

    kb = []
    if status == 'registration' and count < max_teams:
        kb.append([InlineKeyboardButton("✅ Register Team", callback_data=f"reg_{tid}")])
    kb.append([InlineKeyboardButton("👀 View Teams", callback_data=f"teams_{tid}")])
    
    if status in ['in_progress', 'finished']:
        kb.append([InlineKeyboardButton("⚔️ View Bracket", callback_data=f"bracket_{tid}")])
    
    if user_id in ADMINS:
        kb.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data=f"admin_{tid}")])
    
    return text, InlineKeyboardMarkup(kb)

# =======================
# NEW PLAYER FEATURES
# =======================

async def tournament_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed tournament information"""
    if not context.args:
        await update.message.reply_text("Usage: /info <tournament_id>\nExample: /info 1")
        return
    
    try:
        tid = int(context.args[0])
        row = await db_fetchone("SELECT name, max_teams, status FROM tournaments WHERE id = ?", (tid,))
        if not row:
            await update.message.reply_text("❌ Tournament not found.")
            return
        
        name, max_teams, status = row
        count = await count_registered(tid)
        
        # Get all teams
        teams = await db_fetchall("SELECT name, leader_username FROM teams WHERE tournament_id = ? ORDER BY name", (tid,))
        
        text = f"""🏆 <b>{name}</b>
📊 ID: {tid}
👥 Teams: {count}/{max_teams}
🎯 Status: {status}

📋 Registered Teams:
"""
        for i, (team_name, leader) in enumerate(teams, 1):
            text += f"{i}. {team_name} - @{leader or 'No username'}\n"
        
        # Add bracket if available
        if status in ['in_progress', 'finished']:
            bracket_text = await show_bracket_public(tid)
            text += f"\n{bracket_text}"
        
        await update.message.reply_text(text, parse_mode="HTML")
        
    except ValueError:
        await update.message.reply_text("❌ Tournament ID must be a number.")

async def my_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show teams where user is leader"""
    user = update.effective_user
    username = user.username
    
    if not username:
        await update.message.reply_text("❌ You need a Telegram username to use this feature.")
        return
    
    teams = await db_fetchall("""
        SELECT t.name, tour.name, tour.id, tour.status
        FROM teams t 
        JOIN tournaments tour ON t.tournament_id = tour.id 
        WHERE t.leader_username = ? 
        ORDER BY tour.id
    """, (username,))
    
    if not teams:
        await update.message.reply_text("🤷 You haven't registered any teams yet.\n\nUse '📋 Tournaments' to join one! 🎯")
        return
    
    text = "👥 <b>Your Registered Teams:</b>\n\n"
    for team_name, tour_name, tid, status in teams:
        status_emoji = "⚔️" if status == 'in_progress' else "✅" if status == 'finished' else "📝"
        text += f"• <b>{team_name}</b> in {tour_name} (ID: {tid}) {status_emoji}\n"
    
    text += "\nUse <code>/info [tournament_id]</code> to see tournament details!"
    await update.message.reply_text(text, parse_mode="HTML")

async def player_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show player statistics"""
    user = update.effective_user
    username = user.username
    
    if not username:
        await update.message.reply_text("❌ You need a Telegram username to view stats.")
        return
    
    # Count teams led
    teams_led = await db_fetchone("SELECT COUNT(*) FROM teams WHERE leader_username = ?", (username,))
    
    # Count tournaments participated
    tournaments_count = await db_fetchone("""
        SELECT COUNT(DISTINCT tournament_id) FROM teams WHERE leader_username = ?
    """, (username,))
    
    # Count wins
    wins = await db_fetchone("""
        SELECT COUNT(*) FROM bracket_matches b 
        JOIN teams t ON b.winner_team_id = t.id 
        WHERE t.leader_username = ?
    """, (username,))
    
    text = f"""📊 <b>Player Statistics for @{username}</b>

👥 <b>Teams Led:</b> {teams_led[0]}
🏆 <b>Tournaments Joined:</b> {tournaments_count[0]}
🎯 <b>Matches Won:</b> {wins[0]}

<b>Keep dominating the arena! 🚀</b>"""
    
    await update.message.reply_text(text, parse_mode="HTML")

async def search_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search tournaments by name"""
    if not context.args:
        await update.message.reply_text("Usage: /search <tournament_name>")
        return
    
    search_term = " ".join(context.args).lower()
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments WHERE LOWER(name) LIKE ? ORDER BY id DESC", (f'%{search_term}%',))
    
    if not rows:
        await update.message.reply_text("❌ No tournaments found matching your search.")
        return
    
    items = []
    for tid, name, max_teams, status in rows:
        count = await count_registered(tid)
        status_emoji = "⚔️" if status == 'in_progress' else "✅" if status == 'finished' else "📝"
        items.append((f"{name} ({count}/{max_teams}) {status_emoji}", f"view_t_{tid}"))
    
    await update.message.reply_text("🔍 Search Results:", reply_markup=make_keyboard(items))

async def show_bracket_public(tid: int):
    """Show bracket for players (view only)"""
    matches = await db_fetchall("""
        SELECT round_index, match_index, teamA_id, teamB_id, winner_team_id 
        FROM bracket_matches WHERE tournament_id = ? ORDER BY round_index, match_index
    """, (tid,))
    
    if not matches:
        return "❌ Bracket not generated yet."
    
    text = "⚔️ <b>Tournament Bracket:</b>\n\n"
    rounds = {}
    for match in matches:
        round_idx, match_idx, teamA_id, teamB_id, winner_id = match
        if round_idx not in rounds:
            rounds[round_idx] = []
        rounds[round_idx].append(match)
    
    for round_idx in sorted(rounds.keys()):
        text += f"--- <b>Round {round_idx + 1}</b> ---\n"
        for match in rounds[round_idx]:
            _, match_idx, teamA_id, teamB_id, winner_id = match
            
            teamA_name = await get_team_name(teamA_id)
            teamB_name = await get_team_name(teamB_id)
            
            if winner_id:
                winner_name = await get_team_name(winner_id)
                text += f"Match {match_idx+1}: {teamA_name} vs {teamB_name} → 🏆 {winner_name}\n"
            else:
                text += f"Match {match_idx+1}: {teamA_name} vs {teamB_name}\n"
    
    return text

# =======================
# REGISTRATION FLOW
# =======================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "none":
        return

    elif data.startswith("view_t_"):
        tid = int(data.split("_")[-1])
        text, kb = await show_tournament_detail(tid, query.from_user.id)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    elif data.startswith("reg_"):
        tid = int(data.split("_")[-1])
        
        # Check if tournament is full
        if await is_tournament_full(tid):
            await query.message.reply_text("❌ Tournament is full! No more registrations accepted.")
            return
        
        context.user_data['reg_tid'] = tid
        await query.message.reply_text("📝 Enter your team name:")
        return REG_TEAM_NAME

    elif data.startswith("teams_"):
        tid = int(data.split("_")[-1])
        teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ?", (tid,))
        if not teams:
            await query.edit_message_text("❌ No teams registered yet.")
            return
        items = [(f"👥 {name}", f"team_{tid}_{team_id}") for team_id, name in teams]
        await query.edit_message_text("📋 Registered Teams:", reply_markup=make_keyboard(items))

    elif data.startswith("team_"):
        parts = data.split("_")
        tid = int(parts[1])
        team_id = int(parts[2])
        
        team = await db_fetchone("SELECT name, leader_username FROM teams WHERE id = ?", (team_id,))
        if not team:
            await query.edit_message_text("❌ Team not found.")
            return
            
        name, leader = team
        text = f"👥 <b>Team:</b> {name}\n👑 <b>Leader:</b> @{leader if leader else 'Not provided'}"
        
        # Get roster photos
        file_ids = await db_fetchall("SELECT telegram_file_id FROM roster_files WHERE team_id = ?", (team_id,))
        if file_ids:
            await query.message.reply_text(text, parse_mode="HTML")
            media = [InputMediaPhoto(row[0]) for row in file_ids]
            try:
                await query.message.reply_media_group(media)
            except Exception as e:
                logger.error(f"Error sending media: {e}")
                await query.message.reply_text("📷 Roster photos available")
        else:
            await query.edit_message_text(text + "\n📷 No roster photos available", parse_mode="HTML")

    elif data.startswith("bracket_"):
        tid = int(data.split("_")[-1])
        bracket_text = await show_bracket_public(tid)
        await query.edit_message_text(bracket_text, parse_mode="HTML")

    # Admin callbacks
    elif data.startswith("admin_"):
        await admin_callback_handler(update, context)
    
    # Winner selection callbacks
    elif data.startswith("win_"):
        await winner_callback_handler(update, context)

async def reg_team_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle team name registration"""
    team_name = update.message.text.strip()
    tid = context.user_data.get('reg_tid')
    
    if not tid:
        await update.message.reply_text("❌ Session expired. Please start over.")
        return ConversationHandler.END
    
    # Check if tournament is still open
    if await is_tournament_full(tid):
        await update.message.reply_text("❌ Tournament is now full! Registration closed.")
        return ConversationHandler.END
    
    # Check if team name exists in this tournament
    existing = await db_fetchone("SELECT id FROM teams WHERE tournament_id = ? AND name = ?", (tid, team_name))
    if existing:
        await update.message.reply_text("❌ Team name already taken in this tournament. Choose another:")
        return REG_TEAM_NAME
    
    context.user_data['reg_teamname'] = team_name
    await update.message.reply_text("👑 Enter team leader's Telegram username (without @):")
    return REG_LEADER_USERNAME

async def reg_leader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle leader username registration"""
    leader = update.message.text.strip().lstrip('@')
    if not leader:
        await update.message.reply_text("❌ Please enter a valid username:")
        return REG_LEADER_USERNAME
    
    context.user_data['reg_leader'] = leader
    await update.message.reply_text("📸 Now send roster photos (3-4 players). Send photos one by one. When done, send /done")
    context.user_data['reg_roster'] = []
    return REG_WAIT_ROSTER

async def reg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle roster photo upload"""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data.setdefault('reg_roster', []).append(file_id)
        count = len(context.user_data['reg_roster'])
        await update.message.reply_text(f"✅ Photo {count} received. Send more or /done when finished.\n\n⏰ Session will timeout in 5 minutes.")
    return REG_WAIT_ROSTER

async def reg_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Complete registration process"""
    tid = context.user_data.get('reg_tid')
    team_name = context.user_data.get('reg_teamname')
    leader = context.user_data.get('reg_leader')
    roster_files = context.user_data.get('reg_roster', [])
    
    if not tid:
        await update.message.reply_text("❌ Session expired.")
        return ConversationHandler.END
    
    # Final check if tournament is full
    if await is_tournament_full(tid):
        await update.message.reply_text("❌ Tournament is now full! Registration closed.")
        return ConversationHandler.END
    
    if not roster_files:
        await update.message.reply_text("❌ Please send at least 1 photo for roster.")
        return REG_WAIT_ROSTER
    
    # Save team to database
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(
            "INSERT INTO teams (tournament_id, name, leader_username) VALUES (?, ?, ?)",
            (tid, team_name, leader)
        )
        team_id = cur.lastrowid
        
        # Save roster file IDs
        for file_id in roster_files:
            await db.execute(
                "INSERT INTO roster_files (team_id, telegram_file_id) VALUES (?, ?)",
                (team_id, file_id)
            )
        await db.commit()
    
    # Check if tournament is now full
    count = await count_registered(tid)
    max_teams = await get_tournament_max_teams(tid)
    
    if count >= max_teams:
        await db_execute("UPDATE tournaments SET status = 'full' WHERE id = ?", (tid,))
        # Notify admins that tournament is full
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    admin_id, 
                    f"🎉 Tournament {tid} is now FULL! ({count}/{max_teams})\nUse admin panel to generate bracket."
                )
            except Exception as e:
                logger.error(f"Error notifying admin: {e}")
    
    # Notify admins about new registration
    for admin_id in ADMINS:
        try:
            await context.bot.send_message(
                admin_id, 
                f"📢 New registration:\nTeam: {team_name}\nLeader: @{leader}\nTournament: {tid}\nTotal: {count}/{max_teams}"
            )
        except Exception as e:
            logger.error(f"Error notifying admin: {e}")
    
    await update.message.reply_text(f"✅ Team '{team_name}' registered successfully! 🎉\n\nTotal teams: {count}/{max_teams}\n\nUse /myteams to see all your teams!")
    context.user_data.clear()
    return ConversationHandler.END

# =======================
# ADMIN FEATURES - WITH FIXED DELETION
# =======================

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel"""
    kb = [
        [InlineKeyboardButton("🏆 Create Tournament", callback_data="admin_create")],
        [InlineKeyboardButton("📋 Manage Tournaments", callback_data="admin_list")],
        [InlineKeyboardButton("📊 View All Teams", callback_data="admin_all_teams")],
        [InlineKeyboardButton("🗑️ Delete Tournament", callback_data="admin_delete_tournament")],
        [InlineKeyboardButton("👥 Delete Team", callback_data="admin_delete_team")]
    ]
    await update.message.reply_text("🛠️ Admin Panel", reply_markup=InlineKeyboardMarkup(kb))

@admin_only
async def create_tournament_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create tournament command"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /create <tournament_name> <max_teams>\nExample: /create Summer Cup 8")
        return
    
    try:
        name = " ".join(context.args[:-1])
        max_teams = int(context.args[-1])
        
        if max_teams < 2:
            await update.message.reply_text("❌ Minimum 2 teams required.")
            return
        if max_teams > 64:
            await update.message.reply_text("❌ Maximum 64 teams allowed.")
            return
            
        await db_execute(
            "INSERT INTO tournaments (name, max_teams, status) VALUES (?, ?, 'registration')",
            (name, max_teams)
        )
        
        tournament = await db_fetchone("SELECT id FROM tournaments ORDER BY id DESC LIMIT 1")
        tid = tournament[0] if tournament else "unknown"
        
        await update.message.reply_text(f"✅ Tournament created! 🎉\nName: {name}\nMax Teams: {max_teams}\nID: {tid}")
        
    except ValueError:
        await update.message.reply_text("❌ Max teams must be a number.\nUsage: /create <name> <max_teams>")
    except Exception as e:
        logger.error(f"Error creating tournament: {e}")
        await update.message.reply_text("❌ Error creating tournament.")

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin callbacks with FIXED deletion"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_create":
        await query.message.reply_text("🏆 To create tournament, use:\n\n<code>/create Tournament Name 8</code>\n\nReplace with your tournament name and max teams.", parse_mode="HTML")

    elif data == "admin_list":
        rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
        if not rows:
            await query.edit_message_text("❌ No tournaments.")
            return
        
        items = []
        for tid, name, max_teams, status in rows:
            count = await count_registered(tid)
            status_emoji = "⚔️" if status == 'in_progress' else "✅" if status == 'finished' else "📝"
            items.append((f"{name} ({count}/{max_teams}) {status_emoji}", f"admin_t_{tid}"))
        
        await query.edit_message_text("🏆 Tournaments:", reply_markup=make_keyboard(items))

    elif data.startswith("admin_t_"):
        tid = int(data.split("_")[-1])
        row = await db_fetchone("SELECT name, status FROM tournaments WHERE id = ?", (tid,))
        if not row:
            await query.edit_message_text("❌ Tournament not found.")
            return
            
        name, status = row
        count = await count_registered(tid)
        max_teams = await get_tournament_max_teams(tid)
        
        kb = [
            [InlineKeyboardButton("📋 View Registrations", callback_data=f"admin_reg_{tid}")],
            [InlineKeyboardButton("🗑️ Remove Team", callback_data=f"admin_remove_{tid}")],
        ]
        
        if count >= 2 and status in ['registration', 'full']:
            kb.append([InlineKeyboardButton("⚔️ Generate Bracket", callback_data=f"admin_bracket_{tid}")])
        
        if status == 'in_progress':
            kb.append([InlineKeyboardButton("🎯 Manage Bracket", callback_data=f"admin_manage_bracket_{tid}")])
            
        kb.append([InlineKeyboardButton("🧹 Delete Tournament", callback_data=f"admin_del_{tid}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="admin_list")])
        
        text = f"🛠️ Admin: {name}\nStatus: {status}\nTeams: {count}/{max_teams}"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("admin_reg_"):
        tid = int(data.split("_")[-1])
        teams = await db_fetchall("SELECT id, name, leader_username FROM teams WHERE tournament_id = ? ORDER BY id", (tid,))
        if not teams:
            await query.edit_message_text("❌ No teams registered.")
            return
            
        text = "📋 Registered Teams:\n\n"
        for i, (team_id, name, leader) in enumerate(teams, 1):
            text += f"{i}. {name} - @{leader or 'No username'}\n"
            
        await query.edit_message_text(text)

    elif data.startswith("admin_remove_"):
        tid = int(data.split("_")[-1])
        teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ?", (tid,))
        if not teams:
            await query.edit_message_text("❌ No teams to remove.")
            return
            
        items = [(f"🗑️ {name}", f"remove_{tid}_{team_id}") for team_id, name in teams]
        await query.edit_message_text("Select team to remove:", reply_markup=make_keyboard(items))

    elif data.startswith("remove_"):
        parts = data.split("_")
        tid = int(parts[1])
        team_id = int(parts[2])
        
        team = await db_fetchone("SELECT name FROM teams WHERE id = ?", (team_id,))
        if team:
            success = await delete_team_complete(team_id)
            if success:
                await query.edit_message_text(f"✅ Removed team: {team[0]}")
            else:
                await query.edit_message_text(f"❌ Failed to remove team: {team[0]}")
        else:
            await query.edit_message_text("❌ Team not found.")

    elif data.startswith("admin_bracket_"):
        tid = int(data.split("_")[-1])
        count = await count_registered(tid)
        if count < 2:
            await query.edit_message_text("❌ Need at least 2 teams for bracket.")
            return
            
        # Generate bracket
        await generate_bracket(tid)
        await db_execute("UPDATE tournaments SET status = 'in_progress' WHERE id = ?", (tid,))
        
        # Show the bracket immediately after generation
        await show_bracket_admin_view(update, context, tid)

    elif data.startswith("admin_manage_bracket_"):
        tid = int(data.split("_")[-1])
        await show_bracket_admin_view(update, context, tid)

    elif data.startswith("admin_del_"):
        tid = int(data.split("_")[-1])
        tournament = await db_fetchone("SELECT name FROM tournaments WHERE id = ?", (tid,))
        if tournament:
            kb = [
                [InlineKeyboardButton("✅ Confirm Delete", callback_data=f"confirm_del_{tid}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"admin_t_{tid}")]
            ]
            await query.edit_message_text(f"⚠️ Delete tournament '{tournament[0]}'? This will remove ALL teams and data.", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("confirm_del_"):
        tid = int(data.split("_")[-1])
        tournament = await db_fetchone("SELECT name FROM tournaments WHERE id = ?", (tid,))
        if tournament:
            success = await delete_tournament_complete(tid)
            if success:
                await query.edit_message_text(f"✅ Deleted tournament: {tournament[0]}")
            else:
                await query.edit_message_text(f"❌ Failed to delete tournament: {tournament[0]}")

    elif data == "admin_all_teams":
        teams = await db_fetchall("""
            SELECT t.id, t.name, t.leader_username, tour.name 
            FROM teams t 
            JOIN tournaments tour ON t.tournament_id = tour.id 
            ORDER BY tour.id, t.id
        """)
        if not teams:
            await query.edit_message_text("❌ No teams registered in any tournament.")
            return
            
        text = "📋 All Teams Across Tournaments:\n\n"
        current_tournament = None
        for team_id, team_name, leader, tournament_name in teams:
            if tournament_name != current_tournament:
                current_tournament = tournament_name
                text += f"\n🏆 {tournament_name}:\n"
            text += f"• {team_name} - @{leader or 'No username'}\n"
            
        await query.edit_message_text(text)

    # NEW: Direct deletion options
    elif data == "admin_delete_tournament":
        rows = await db_fetchall("SELECT id, name FROM tournaments ORDER BY id DESC")
        if not rows:
            await query.edit_message_text("❌ No tournaments to delete.")
            return
        
        items = [(f"🗑️ {name}", f"admin_del_{tid}") for tid, name in rows]
        await query.edit_message_text("Select tournament to delete:", reply_markup=make_keyboard(items))

    elif data == "admin_delete_team":
        teams = await db_fetchall("""
            SELECT t.id, t.name, tour.name 
            FROM teams t 
            JOIN tournaments tour ON t.tournament_id = tour.id 
            ORDER BY tour.id, t.id
        """)
        if not teams:
            await query.edit_message_text("❌ No teams to delete.")
            return
        
        items = []
        for team_id, team_name, tournament_name in teams:
            items.append((f"🗑️ {team_name} ({tournament_name})", f"remove_0_{team_id}"))
        
        await query.edit_message_text("Select team to delete:", reply_markup=make_keyboard(items))

# =======================
# WINNER SELECTION HANDLER
# =======================

async def winner_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle winner selection callbacks"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("win_"):
        parts = data.split("_")
        match_id = int(parts[1])
        team_side = parts[2]  # A or B
        
        # Get match details
        match = await db_fetchone("SELECT tournament_id, teamA_id, teamB_id FROM bracket_matches WHERE id = ?", (match_id,))
        if not match:
            await query.edit_message_text("❌ Match not found.")
            return
            
        tid, teamA_id, teamB_id = match
        
        # Determine winner team ID
        if team_side == "A" and teamA_id:
            winner_id = teamA_id
        elif team_side == "B" and teamB_id:
            winner_id = teamB_id
        else:
            await query.edit_message_text("❌ Invalid selection.")
            return
        
        # Get winner team name for confirmation
        winner_team = await db_fetchone("SELECT name FROM teams WHERE id = ?", (winner_id,))
        if not winner_team:
            await query.edit_message_text("❌ Team not found.")
            return
            
        # Update match winner
        await db_execute("UPDATE bracket_matches SET winner_team_id = ? WHERE id = ?", (winner_id, match_id))
        
        # Propagate winner to next round
        await propagate_winner(tid)
        
        # Show updated bracket
        await show_bracket_admin_view(update, context, tid)

# =======================
# BRACKET SYSTEM
# =======================

async def generate_bracket(tid: int):
    """Generate random bracket for tournament"""
    teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ?", (tid,))
    teams = [{"id": row[0], "name": row[1]} for row in teams]
    random.shuffle(teams)
    
    # Clear existing bracket
    await db_execute("DELETE FROM bracket_matches WHERE tournament_id = ?", (tid,))
    
    # Generate matches for first round
    round_index = 0
    match_index = 0
    
    for i in range(0, len(teams), 2):
        teamA = teams[i]
        teamB = teams[i+1] if i+1 < len(teams) else None
        
        await db_execute(
            "INSERT INTO bracket_matches (tournament_id, round_index, match_index, teamA_id, teamB_id) VALUES (?, ?, ?, ?, ?)",
            (tid, round_index, match_index, teamA["id"], teamB["id"] if teamB else None)
        )
        match_index += 1

async def propagate_winner(tid: int):
    """Propagate winners to next rounds"""
    matches = await db_fetchall("""
        SELECT id, round_index, match_index, teamA_id, teamB_id, winner_team_id 
        FROM bracket_matches 
        WHERE tournament_id = ? 
        ORDER BY round_index, match_index
    """, (tid,))
    
    if not matches:
        return
    
    # Group matches by round
    rounds = {}
    for match in matches:
        mid, round_idx, match_idx, teamA, teamB, winner = match
        if round_idx not in rounds:
            rounds[round_idx] = {}
        rounds[round_idx][match_idx] = {
            "id": mid, "teamA": teamA, "teamB": teamB, "winner": winner
        }
    
    max_round = max(rounds.keys())
    
    # Propagate winners to next round
    for round_idx in range(max_round):
        if round_idx + 1 not in rounds:
            continue
            
        for match_idx, match in rounds[round_idx].items():
            if match["winner"]:
                next_match_idx = match_idx // 2
                if next_match_idx in rounds[round_idx + 1]:
                    next_match = rounds[round_idx + 1][next_match_idx]
                    
                    # Determine if winner goes to teamA or teamB slot
                    position_in_next_match = match_idx % 2  # 0 for teamA, 1 for teamB
                    
                    if position_in_next_match == 0:  # Goes to teamA
                        await db_execute("UPDATE bracket_matches SET teamA_id = ? WHERE id = ?", (match["winner"], next_match["id"]))
                    else:  # Goes to teamB
                        await db_execute("UPDATE bracket_matches SET teamB_id = ? WHERE id = ?", (match["winner"], next_match["id"]))
    
    # Check if tournament is finished (final has winner)
    final_matches = [m for m in matches if m[1] == max_round]
    if len(final_matches) == 1 and final_matches[0][5]:  # Only one final match and has winner
        await declare_winner(tid, final_matches[0][5])

async def declare_winner(tid: int, winner_team_id: int):
    """Declare tournament winner"""
    winner_team = await db_fetchone("SELECT name FROM teams WHERE id = ?", (winner_team_id,))
    if winner_team:
        await db_execute("UPDATE tournaments SET status = 'finished' WHERE id = ?", (tid,))
        # Notify admins
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"🏆 TOURNAMENT FINISHED!\nWinner: {winner_team[0]}\n\nYou can now delete the tournament and create a new one."
                )
            except Exception as e:
                logger.error(f"Error notifying admin: {e}")

async def show_bracket_admin_view(update: Update, context: ContextTypes.DEFAULT_TYPE, tid: int):
    """Show bracket with buttons for admins to record winners"""
    matches = await db_fetchall("""
        SELECT id, round_index, match_index, teamA_id, teamB_id, winner_team_id 
        FROM bracket_matches 
        WHERE tournament_id = ? 
        ORDER BY round_index, match_index
    """, (tid,))
    
    if not matches:
        await update.callback_query.edit_message_text("❌ No bracket matches found.")
        return
    
    text = "⚔️ Tournament Bracket:\n\n"
    kb = []
    
    # Group by rounds
    rounds = {}
    for match in matches:
        mid, round_idx, match_idx, teamA_id, teamB_id, winner_id = match
        if round_idx not in rounds:
            rounds[round_idx] = []
        rounds[round_idx].append(match)
    
    for round_idx in sorted(rounds.keys()):
        text += f"--- Round {round_idx + 1} ---\n"
        for match in rounds[round_idx]:
            mid, _, match_idx, teamA_id, teamB_id, winner_id = match
            
            # Get team names
            teamA_name = await get_team_name(teamA_id)
            teamB_name = await get_team_name(teamB_id)
            
            winner_text = ""
            if winner_id:
                winner_name = await get_team_name(winner_id)
                winner_text = f" 🏆 Winner: {winner_name}"
            
            text += f"Match {match_idx + 1}: {teamA_name} vs {teamB_name}{winner_text}\n"
            
            # Add buttons for matches without winners
            if not winner_id and teamA_id and teamB_id:
                kb.append([
                    InlineKeyboardButton(f"✅ {teamA_name}", callback_data=f"win_{mid}_A"),
                    InlineKeyboardButton(f"✅ {teamB_name}", callback_data=f"win_{mid}_B")
                ])
    
    if not kb:
        text += "\n🎉 All matches completed! Tournament finished."
    
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb) if kb else None)

# =======================
# TEXT MESSAGE HANDLER
# =======================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    text = update.message.text.strip()
    user = update.effective_user
    
    if text in ("📋 Tournaments", "tournaments"):
        await tournaments_button(update, context)
    elif text in ("🔎 View Teams", "teams"):
        kb = await show_tournaments_keyboard()
        await update.message.reply_text("Select tournament to view teams:", reply_markup=kb)
    elif text in ("ℹ️ Help", "help"):
        await help_cmd(update, context)
    elif text in ("📊 My Stats", "stats", "mystats"):
        await player_stats(update, context)
    elif text in ("🛠️ Admin Panel", "admin") and user.id in ADMINS:
        await admin_panel(update, context)
    else:
        # If no command matches, show main keyboard again
        kb = [
            [KeyboardButton("📋 Tournaments"), KeyboardButton("🔎 View Teams")],
            [KeyboardButton("ℹ️ Help"), KeyboardButton("📊 My Stats")]
        ]
        if user.id in ADMINS:
            kb.append([KeyboardButton("🛠️ Admin Panel")])
        
        reply_markup = ReplyKeyboardMarkup(kb, resize_keyboard=True, is_persistent=True)
        await update.message.reply_text(
            "🎮 Use the buttons below or type /help for commands!",
            reply_markup=reply_markup
        )

# =======================
# ERROR HANDLER
# =======================

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors in the bot."""
    logger.error(f"Exception while handling an update: {context.error}")
    
    # Try to notify user about the error
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again or contact support."
            )
    except Exception:
        pass

# =======================
# MAIN FUNCTION
# =======================

async def auto_backup():
    """Auto-backup every hour"""
    while True:
        await asyncio.sleep(3600)  # 1 hour
        await backup_database()
        logger.info("🔄 Auto-backup completed")

def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        return
    
    logger.info("🚀 Starting Brawl Stars Tournament Bot...")
    
    # Initialize database
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.run_until_complete(init_db())
    
    # Start auto-backup
    asyncio.ensure_future(auto_backup())
    
    # Build application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("create", create_tournament_simple))
    app.add_handler(CommandHandler("admin_list", admin_panel))
    app.add_handler(CommandHandler("info", tournament_info))
    app.add_handler(CommandHandler("myteams", my_teams))
    app.add_handler(CommandHandler("stats", player_stats))
    app.add_handler(CommandHandler("search", search_tournament))
    
    # Registration conversation
    reg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(callback_handler, pattern=r"^reg_")],
        states={
            REG_TEAM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_team_name)],
            REG_LEADER_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_leader)],
            REG_WAIT_ROSTER: [
                MessageHandler(filters.PHOTO, reg_photo),
                CommandHandler("done", reg_done),
            ],
        },
        fallbacks=[CommandHandler("done", reg_done)],
        per_message=False
    )
    app.add_handler(reg_conv)
    
    # Callback queries
    app.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^(view_t_|reg_|teams_|team_|admin_|bracket_)"))
    app.add_handler(CallbackQueryHandler(winner_callback_handler, pattern=r"^win_"))
    
    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("🤖 Bot is running with ALL features and FIXED DELETION...")
    app.run_polling()

if __name__ == "__main__":
    main()
