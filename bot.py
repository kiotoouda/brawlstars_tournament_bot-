"""
Brawl Stars Tournament Bot - COMPLETE DEPLOYMENT READY VERSION
"""

import os
import asyncio
import random
import logging
import aiosqlite
import requests
import json
import base64
from aiohttp import web
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
ADMINS = {int(x.strip()) for x in os.getenv("ADMINS", "7665378359,6548564636").split(",")}
DATABASE = "tournaments.db"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PORT = int(os.getenv("PORT", 8080))
# -----------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Conversation states
(REG_TEAM_NAME, REG_LEADER_USERNAME, REG_WAIT_ROSTER) = range(3)

# =======================
# WEB SERVER FOR RENDER
# =======================

async def handle_health_check(request):
    """Health check endpoint for Render"""
    return web.Response(text="✅ Bot is running!")

async def start_web_server():
    """Start a simple web server for Render"""
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/health', handle_health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Web server running on port {PORT}")

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
            await db.execute("PRAGMA foreign_keys = ON")
            
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
        await db.execute("PRAGMA foreign_keys = ON")
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
# DELETION FUNCTIONS - WORKING VERSION
# =======================

async def delete_tournament(tournament_id: int) -> Tuple[bool, str]:
    """Delete tournament and all related data"""
    try:
        tournament = await db_fetchone("SELECT name FROM tournaments WHERE id = ?", (tournament_id,))
        if not tournament:
            return False, "Tournament not found"
        
        tournament_name = tournament[0]
        await db_execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
        
        logger.info(f"✅ Tournament '{tournament_name}' (ID: {tournament_id}) deleted successfully")
        return True, tournament_name
        
    except Exception as e:
        logger.error(f"❌ Error deleting tournament {tournament_id}: {e}")
        return False, str(e)

async def delete_team(team_id: int) -> Tuple[bool, str]:
    """Delete team and all related data"""
    try:
        team_info = await db_fetchone("""
            SELECT t.name, t.tournament_id, tour.name 
            FROM teams t 
            JOIN tournaments tour ON t.tournament_id = tour.id 
            WHERE t.id = ?
        """, (team_id,))
        
        if not team_info:
            return False, "Team not found"
            
        team_name, tournament_id, tournament_name = team_info
        await db_execute("DELETE FROM teams WHERE id = ?", (team_id,))
        
        count = await count_registered(tournament_id)
        max_teams_row = await db_fetchone("SELECT max_teams FROM tournaments WHERE id = ?", (tournament_id,))
        if max_teams_row and count < max_teams_row[0]:
            await db_execute("UPDATE tournaments SET status = 'registration' WHERE id = ?", (tournament_id,))
        
        logger.info(f"✅ Team '{team_name}' deleted from tournament '{tournament_name}'")
        return True, f"{team_name} from {tournament_name}"
        
    except Exception as e:
        logger.error(f"❌ Error deleting team {team_id}: {e}")
        return False, str(e)

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
# BOT HANDLERS - USER
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message and main menu keyboard"""
    user = update.effective_user
    
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
    
    kb = [
        [KeyboardButton("📋 Tournaments"), KeyboardButton("🔎 View Teams")],
        [KeyboardButton("ℹ️ Help"), KeyboardButton("📊 My Stats")]
    ]
    
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
# PLAYER FEATURES
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
        
        teams = await db_fetchall("SELECT name, leader_username FROM teams WHERE tournament_id = ? ORDER BY name", (tid,))
        
        text = f"""🏆 <b>{name}</b>
📊 ID: {tid}
👥 Teams: {count}/{max_teams}
🎯 Status: {status}

📋 Registered Teams:
"""
        for i, (team_name, leader) in enumerate(teams, 1):
            text += f"{i}. {team_name} - @{leader or 'No username'}\n"
        
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
    
    teams_led = await db_fetchone("SELECT COUNT(*) FROM teams WHERE leader_username = ?", (username,))
    tournaments_count = await db_fetchone("""
        SELECT COUNT(DISTINCT tournament_id) FROM teams WHERE leader_username = ?
    """, (username,))
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

    # Deletion callbacks
    elif data.startswith("delete_tournament_") or data.startswith("confirm_delete_tournament_") or data.startswith("delete_team_"):
        await deletion_callback_handler(update, context)

async def deletion_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deletion callbacks"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("delete_tournament_"):
        tournament_id = int(data.split("_")[2])
        await confirm_tournament_deletion(update, context, tournament_id)

    elif data.startswith("confirm_delete_tournament_"):
        tournament_id = int(data.split("_")[3])
        await execute_tournament_deletion(update, context, tournament_id)

    elif data.startswith("delete_team_"):
        team_id = int(data.split("_")[2])
        await execute_team_deletion(update, context, team_id)

async def reg_team_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle team name registration"""
    team_name = update.message.text.strip()
    tid = context.user_data.get('reg_tid')
    
    if not tid:
        await update.message.reply_text("❌ Session expired. Please start over.")
        return ConversationHandler.END
    
    if await is_tournament_full(tid):
        await update.message.reply_text("❌ Tournament is now full! Registration closed.")
        return ConversationHandler.END
    
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
    
    if await is_tournament_full(tid):
        await update.message.reply_text("❌ Tournament is now full! Registration closed.")
        return ConversationHandler.END
    
    if not roster_files:
        await update.message.reply_text("❌ Please send at least 1 photo for roster.")
        return REG_WAIT_ROSTER
    
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        cur = await db.execute(
            "INSERT INTO teams (tournament_id, name, leader_username) VALUES (?, ?, ?)",
            (tid, team_name, leader)
        )
        team_id = cur.lastrowid
        
        for file_id in roster_files:
            await db.execute(
                "INSERT INTO roster_files (team_id, telegram_file_id) VALUES (?, ?)",
                (team_id, file_id)
            )
        await db.commit()
    
    count = await count_registered(tid)
    max_teams = await get_tournament_max_teams(tid)
    
    if count >= max_teams:
        await db_execute("UPDATE tournaments SET status = 'full' WHERE id = ?", (tid,))
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(
                    admin_id, 
                    f"🎉 Tournament {tid} is now FULL! ({count}/{max_teams})\nUse admin panel to generate bracket."
                )
            except Exception as e:
                logger.error(f"Error notifying admin: {e}")
    
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
# ADMIN FEATURES
# =======================

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel"""
    kb = [
        [InlineKeyboardButton("🏆 Create Tournament", callback_data="admin_create")],
        [InlineKeyboardButton("📋 Manage Tournaments", callback_data="admin_list")],
        [InlineKeyboardButton("🗑️ Delete Tournament", callback_data="admin_delete_tournament")],
        [InlineKeyboardButton("👥 Delete Team", callback_data="admin_delete_team")]
    ]
    await update.message.reply_text("🛠️ Admin Panel - Choose an option:", reply_markup=InlineKeyboardMarkup(kb))

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
    """Handle admin callbacks"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_create":
        await query.message.reply_text("🏆 To create tournament, use:\n\n<code>/create Tournament Name 8</code>\n\nReplace with your tournament name and max teams.", parse_mode="HTML")

    elif data == "admin_list":
        await show_tournaments_for_management(update, context)

    elif data == "admin_delete_tournament":
        await show_tournaments_for_deletion(update, context)

    elif data == "admin_delete_team":
        await show_teams_for_deletion(update, context)

    elif data.startswith("manage_tournament_"):
        tournament_id = int(data.split("_")[2])
        await show_tournament_management(update, context, tournament_id)

async def show_tournaments_for_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tournaments for management"""
    query = update.callback_query
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    
    if not rows:
        await query.edit_message_text("❌ No tournaments available.")
        return
    
    items = []
    for tid, name, max_teams, status in rows:
        count = await count_registered(tid)
        status_emoji = "⚔️" if status == 'in_progress' else "✅" if status == 'finished' else "📝"
        items.append((f"{name} ({count}/{max_teams}) {status_emoji}", f"manage_tournament_{tid}"))
    
    kb = make_keyboard(items)
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    
    await query.edit_message_text("🏆 Select tournament to manage:", reply_markup=kb)

async def show_tournaments_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tournaments for deletion"""
    query = update.callback_query
    rows = await db_fetchall("SELECT id, name FROM tournaments ORDER BY id DESC")
    
    if not rows:
        await query.edit_message_text("❌ No tournaments to delete.")
        return
    
    items = []
    for tid, name in rows:
        items.append((f"🗑️ {name}", f"delete_tournament_{tid}"))
    
    kb = make_keyboard(items)
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    
    await query.edit_message_text("🗑️ Select tournament to DELETE:", reply_markup=kb)

async def show_teams_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all teams for deletion"""
    query = update.callback_query
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
        items.append((f"🗑️ {team_name} ({tournament_name})", f"delete_team_{team_id}"))
    
    kb = make_keyboard(items)
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    
    await query.edit_message_text("👥 Select team to DELETE:", reply_markup=kb)

async def show_tournament_management(update: Update, context: ContextTypes.DEFAULT_TYPE, tournament_id: int):
    """Show tournament management options"""
    query = update.callback_query
    row = await db_fetchone("SELECT name, status FROM tournaments WHERE id = ?", (tournament_id,))
    
    if not row:
        await query.edit_message_text("❌ Tournament not found.")
        return
        
    name, status = row
    count = await count_registered(tournament_id)
    
    kb = [
        [InlineKeyboardButton("📋 View Teams", callback_data=f"view_teams_{tournament_id}")],
        [InlineKeyboardButton("🗑️ Delete Tournament", callback_data=f"delete_tournament_{tournament_id}")],
    ]
    
    if count >= 2 and status in ['registration', 'full']:
        kb.append([InlineKeyboardButton("⚔️ Generate Bracket", callback_data=f"generate_bracket_{tournament_id}")])
    
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="admin_list")])
    
    text = f"🛠️ Managing: {name}\nStatus: {status}\nTeams: {count}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def confirm_tournament_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE, tournament_id: int):
    """Show confirmation for tournament deletion"""
    query = update.callback_query
    tournament = await db_fetchone("SELECT name FROM tournaments WHERE id = ?", (tournament_id,))
    
    if not tournament:
        await query.edit_message_text("❌ Tournament not found.")
        return
    
    kb = [
        [InlineKeyboardButton("✅ YES, Delete Tournament", callback_data=f"confirm_delete_tournament_{tournament_id}")],
        [InlineKeyboardButton("❌ NO, Cancel", callback_data="admin_delete_tournament")]
    ]
    
    await query.edit_message_text(
        f"⚠️ <b>CONFIRM DELETION</b> ⚠️\n\n"
        f"Are you sure you want to delete tournament:\n"
        f"<b>{tournament[0]}</b>?\n\n"
        f"❌ This will delete ALL teams, rosters, and bracket data!",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )

async def execute_tournament_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE, tournament_id: int):
    """Execute tournament deletion"""
    query = update.callback_query
    
    success, result = await delete_tournament(tournament_id)
    
    if success:
        await query.edit_message_text(
            f"✅ <b>TOURNAMENT DELETED SUCCESSFULLY!</b>\n\n"
            f"Tournament: <b>{result}</b>\n"
            f"All teams, rosters, and bracket data have been removed.",
            parse_mode="HTML"
        )
    else:
        await query.edit_message_text(
            f"❌ <b>DELETION FAILED</b>\n\n"
            f"Error: {result}",
            parse_mode="HTML"
        )

async def execute_team_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE, team_id: int):
    """Execute team deletion"""
    query = update.callback_query
    
    success, result = await delete_team(team_id)
    
    if success:
        await query.edit_message_text(
            f"✅ <b>TEAM DELETED SUCCESSFULLY!</b>\n\n"
            f"Team: <b>{result}</b>\n"
            f"All roster data has been removed.",
            parse_mode="HTML"
        )
    else:
        await query.edit_message_text(
            f"❌ <b>TEAM DELETION FAILED</b>\n\n"
            f"Error: {result}",
            parse_mode="HTML"
        )

# =======================
# BRACKET SYSTEM
# =======================

async def generate_bracket(tid: int):
    """Generate random bracket for tournament"""
    teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ?", (tid,))
    teams = [{"id": row[0], "name": row[1]} for row in teams]
    random.shuffle(teams)
    
    await db_execute("DELETE FROM bracket_matches WHERE tournament_id = ?", (tid,))
    
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

async def winner_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle winner selection callbacks"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("win_"):
        parts = data.split("_")
        match_id = int(parts[1])
        team_side = parts[2]  # A or B
        
        match = await db_fetchone("SELECT tournament_id, teamA_id, teamB_id FROM bracket_matches WHERE id = ?", (match_id,))
        if not match:
            await query.edit_message_text("❌ Match not found.")
            return
            
        tid, teamA_id, teamB_id = match
        
        if team_side == "A" and teamA_id:
            winner_id = teamA_id
        elif team_side == "B" and teamB_id:
            winner_id = teamB_id
        else:
            await query.edit_message_text("❌ Invalid selection.")
            return
        
        winner_team = await db_fetchone("SELECT name FROM teams WHERE id = ?", (winner_id,))
        if not winner_team:
            await query.edit_message_text("❌ Team not found.")
            return
            
        await db_execute("UPDATE bracket_matches SET winner_team_id = ? WHERE id = ?", (winner_id, match_id))
        await propagate_winner(tid)
        await show_bracket_admin_view(update, context, tid)

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
    
    rounds = {}
    for match in matches:
        mid, round_idx, match_idx, teamA, teamB, winner = match
        if round_idx not in rounds:
            rounds[round_idx] = {}
        rounds[round_idx][match_idx] = {
            "id": mid, "teamA": teamA, "teamB": teamB, "winner": winner
        }
    
    max_round = max(rounds.keys())
    
    for round_idx in range(max_round):
        if round_idx + 1 not in rounds:
            continue
            
        for match_idx, match in rounds[round_idx].items():
            if match["winner"]:
                next_match_idx = match_idx // 2
                if next_match_idx in rounds[round_idx + 1]:
                    next_match = rounds[round_idx + 1][next_match_idx]
                    
                    position_in_next_match = match_idx % 2
                    
                    if position_in_next_match == 0:
                        await db_execute("UPDATE bracket_matches SET teamA_id = ? WHERE id = ?", (match["winner"], next_match["id"]))
                    else:
                        await db_execute("UPDATE bracket_matches SET teamB_id = ? WHERE id = ?", (match["winner"], next_match["id"]))
    
    final_matches = [m for m in matches if m[1] == max_round]
    if len(final_matches) == 1 and final_matches[0][5]:
        await declare_winner(tid, final_matches[0][5])

async def declare_winner(tid: int, winner_team_id: int):
    """Declare tournament winner"""
    winner_team = await db_fetchone("SELECT name FROM teams WHERE id = ?", (winner_team_id,))
    if winner_team:
        await db_execute("UPDATE tournaments SET status = 'finished' WHERE id = ?", (tid,))
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
            
            teamA_name = await get_team_name(teamA_id)
            teamB_name = await get_team_name(teamB_id)
            
            winner_text = ""
            if winner_id:
                winner_name = await get_team_name(winner_id)
                winner_text = f" 🏆 Winner: {winner_name}"
            
            text += f"Match {match_idx + 1}: {teamA_name} vs {teamB_name}{winner_text}\n"
            
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
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again or contact support."
            )
    except Exception:
        pass

# =======================
# AUTO BACKUP
# =======================

async def auto_backup():
    """Auto-backup every hour"""
    while True:
        await asyncio.sleep(3600)
        await backup_database()
        logger.info("🔄 Auto-backup completed")

# =======================
# MAIN BOT SETUP
# =======================

async def setup_bot():
    """Setup and run the bot"""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        return None
    
    logger.info("🚀 Setting up Brawl Stars Tournament Bot...")
    
    # Initialize database
    await init_db()
    
    # Build application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("create", create_tournament_simple))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("info", tournament_info))
    application.add_handler(CommandHandler("myteams", my_teams))
    application.add_handler(CommandHandler("stats", player_stats))
    application.add_handler(CommandHandler("search", search_tournament))
    
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
    application.add_handler(reg_conv)
    
    # Callback queries
    application.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^(view_t_|reg_|teams_|team_|admin_|bracket_|delete_|confirm_|manage_|generate_|win_)"))
    
    # Text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Start auto-backup
    asyncio.create_task(auto_backup())
    
    return application

async def main():
    """Main function to run both bot and web server"""
    # Start web server for Render
    await start_web_server()
    logger.info("✅ Web server started successfully")
    
    # Setup and start bot
    application = await setup_bot()
    if application:
        logger.info("✅ Bot setup completed successfully")
        logger.info("🤖 Bot is now running and ready to receive messages!")
        
        # Start polling
        await application.run_polling()
    else:
        logger.error("❌ Failed to setup bot")

if __name__ == "__main__":
    # Run the bot
    asyncio.run(main())
