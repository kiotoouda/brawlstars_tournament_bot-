"""
Brawl Stars Tournament Bot - WORKING DELETION VERSION
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
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
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
# DATABASE FUNCTIONS - SIMPLIFIED
# =======================

async def init_db():
    """Initialize database"""
    try:
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
    """Execute database query"""
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(query, params)
        await db.commit()

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

# =======================
# DELETION FUNCTIONS - SIMPLE & RELIABLE
# =======================

async def delete_tournament(tournament_id: int) -> Tuple[bool, str]:
    """Delete tournament and all related data - SIMPLE VERSION"""
    try:
        # Get tournament name for confirmation message
        tournament = await db_fetchone("SELECT name FROM tournaments WHERE id = ?", (tournament_id,))
        if not tournament:
            return False, "Tournament not found"
        
        tournament_name = tournament[0]
        
        # Delete tournament (cascades to teams, roster_files, bracket_matches)
        await db_execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
        
        logger.info(f"✅ Tournament '{tournament_name}' (ID: {tournament_id}) deleted successfully")
        return True, tournament_name
        
    except Exception as e:
        logger.error(f"❌ Error deleting tournament {tournament_id}: {e}")
        return False, str(e)

async def delete_team(team_id: int) -> Tuple[bool, str]:
    """Delete team and all related data - SIMPLE VERSION"""
    try:
        # Get team info for confirmation message
        team_info = await db_fetchone("""
            SELECT t.name, t.tournament_id, tour.name 
            FROM teams t 
            JOIN tournaments tour ON t.tournament_id = tour.id 
            WHERE t.id = ?
        """, (team_id,))
        
        if not team_info:
            return False, "Team not found"
            
        team_name, tournament_id, tournament_name = team_info
        
        # Delete team (cascades to roster_files)
        await db_execute("DELETE FROM teams WHERE id = ?", (team_id,))
        
        # Update tournament status if needed
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
# BOT HANDLERS - SIMPLIFIED
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    user = update.effective_user
    
    greeting = f"""
✨ <b>WELCOME TO BRAWL STARS TOURNAMENT BOT!</b> ✨

🎮 <i>Hello {user.first_name}! Ready to dominate the tournament?</i> 🎮

Use the buttons below to get started! ⚔️
    """
    
    kb = [
        [KeyboardButton("📋 Tournaments"), KeyboardButton("🔎 View Teams")],
        [KeyboardButton("ℹ️ Help"), KeyboardButton("📊 My Stats")]
    ]
    
    if user.id in ADMINS:
        kb.append([KeyboardButton("🛠️ Admin Panel")])
    
    reply_markup = ReplyKeyboardMarkup(kb, resize_keyboard=True, is_persistent=True)
    await update.message.reply_text(greeting, reply_markup=reply_markup, parse_mode="HTML")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    text = """
🤖 <b>BRAWL STARS TOURNAMENT BOT</b> 🤖

<b>🎮 FOR PLAYERS:</b>
• Browse and register for tournaments
• View teams and their rosters
• Track tournament progress

<b>🛠️ FOR ADMINS:</b>
• Create and manage tournaments
• Generate brackets and record results
• Delete tournaments and teams
    """
    await update.message.reply_text(text, parse_mode="HTML")

# =======================
# ADMIN FEATURES - WORKING DELETION
# =======================

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel with working deletion"""
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

# =======================
# DELETION CALLBACK HANDLERS - WORKING VERSION
# =======================

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin callbacks - SIMPLIFIED"""
    query = update.callback_query
    await query.answer()
    data = query.data

    logger.info(f"Admin callback received: {data}")

    if data == "admin_create":
        await query.message.reply_text("🏆 To create tournament, use:\n\n<code>/create Tournament Name 8</code>\n\nReplace with your tournament name and max teams.", parse_mode="HTML")

    elif data == "admin_list":
        await show_tournaments_for_management(update, context)

    elif data == "admin_delete_tournament":
        await show_tournaments_for_deletion(update, context)

    elif data == "admin_delete_team":
        await show_teams_for_deletion(update, context)

    elif data.startswith("delete_tournament_"):
        tournament_id = int(data.split("_")[2])
        await confirm_tournament_deletion(update, context, tournament_id)

    elif data.startswith("confirm_delete_tournament_"):
        tournament_id = int(data.split("_")[3])
        await execute_tournament_deletion(update, context, tournament_id)

    elif data.startswith("delete_team_"):
        team_id = int(data.split("_")[2])
        await execute_team_deletion(update, context, team_id)

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
# CALLBACK HANDLER - MAIN
# =======================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main callback handler"""
    query = update.callback_query
    await query.answer()
    data = query.data

    logger.info(f"Callback received: {data}")

    # Handle admin callbacks
    if data.startswith("admin"):
        await admin_callback_handler(update, context)
    
    # Handle back button
    elif data == "admin_back":
        await admin_panel_callback(update, context)
    
    # Handle other callbacks
    elif data.startswith("view_t_"):
        await view_tournament_callback(update, context)
    elif data.startswith("reg_"):
        await registration_callback(update, context)
    else:
        await query.edit_message_text("❌ Unknown command. Please try again.")

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel via callback"""
    query = update.callback_query
    kb = [
        [InlineKeyboardButton("🏆 Create Tournament", callback_data="admin_create")],
        [InlineKeyboardButton("📋 Manage Tournaments", callback_data="admin_list")],
        [InlineKeyboardButton("🗑️ Delete Tournament", callback_data="admin_delete_tournament")],
        [InlineKeyboardButton("👥 Delete Team", callback_data="admin_delete_team")]
    ]
    await query.edit_message_text("🛠️ Admin Panel - Choose an option:", reply_markup=InlineKeyboardMarkup(kb))

async def view_tournament_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle tournament view callback"""
    query = update.callback_query
    data = query.data
    tournament_id = int(data.split("_")[2])
    
    row = await db_fetchone("SELECT name, max_teams, status FROM tournaments WHERE id = ?", (tournament_id,))
    if not row:
        await query.edit_message_text("❌ Tournament not found.")
        return
    
    name, max_teams, status = row
    count = await count_registered(tournament_id)
    
    text = f"""🏆 <b>{name}</b>
📊 ID: {tournament_id}
👥 Teams: {count}/{max_teams}
🎯 Status: {status}"""

    kb = []
    if status == 'registration' and count < max_teams:
        kb.append([InlineKeyboardButton("✅ Register Team", callback_data=f"reg_{tournament_id}")])
    kb.append([InlineKeyboardButton("👀 View Teams", callback_data=f"teams_{tournament_id}")])
    
    if query.from_user.id in ADMINS:
        kb.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data=f"admin_manage_{tournament_id}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle registration callback"""
    query = update.callback_query
    data = query.data
    tournament_id = int(data.split("_")[1])
    
    # Check if tournament exists and has space
    tournament = await db_fetchone("SELECT name, max_teams FROM tournaments WHERE id = ?", (tournament_id,))
    if not tournament:
        await query.message.reply_text("❌ Tournament not found.")
        return
    
    name, max_teams = tournament
    count = await count_registered(tournament_id)
    
    if count >= max_teams:
        await query.message.reply_text("❌ Tournament is full! No more registrations accepted.")
        return
    
    context.user_data['reg_tid'] = tournament_id
    await query.message.reply_text("📝 Enter your team name:")
    return REG_TEAM_NAME

# =======================
# TEXT MESSAGE HANDLER
# =======================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    text = update.message.text.strip()
    user = update.effective_user
    
    if text in ("📋 Tournaments", "tournaments"):
        await show_tournaments_list(update, context)
    elif text in ("🔎 View Teams", "teams"):
        await show_tournaments_for_teams(update, context)
    elif text in ("ℹ️ Help", "help"):
        await help_cmd(update, context)
    elif text in ("📊 My Stats", "stats", "mystats"):
        await update.message.reply_text("📊 Stats feature coming soon!")
    elif text in ("🛠️ Admin Panel", "admin") and user.id in ADMINS:
        await admin_panel(update, context)
    else:
        await update.message.reply_text("🎮 Use the buttons below or type /help for commands!")

async def show_tournaments_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tournaments list"""
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    if not rows:
        await update.message.reply_text("❌ No tournaments available.")
        return
    
    items = []
    for tid, name, max_teams, status in rows:
        count = await count_registered(tid)
        status_emoji = "⚔️" if status == 'in_progress' else "✅" if status == 'finished' else "📝"
        items.append((f"{name} ({count}/{max_teams}) {status_emoji}", f"view_t_{tid}"))
    
    await update.message.reply_text("🏆 Available tournaments:", reply_markup=make_keyboard(items))

async def show_tournaments_for_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tournaments for viewing teams"""
    rows = await db_fetchall("SELECT id, name FROM tournaments ORDER BY id DESC")
    if not rows:
        await update.message.reply_text("❌ No tournaments available.")
        return
    
    items = []
    for tid, name in rows:
        items.append((f"👀 {name}", f"teams_{tid}"))
    
    await update.message.reply_text("Select tournament to view teams:", reply_markup=make_keyboard(items))

# =======================
# REGISTRATION FLOW (simplified)
# =======================

async def reg_team_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle team name registration"""
    team_name = update.message.text.strip()
    tid = context.user_data.get('reg_tid')
    
    if not tid:
        await update.message.reply_text("❌ Session expired. Please start over.")
        return ConversationHandler.END
    
    context.user_data['reg_teamname'] = team_name
    await update.message.reply_text("👑 Enter team leader's Telegram username (without @):")
    return REG_LEADER_USERNAME

async def reg_leader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle leader username registration"""
    leader = update.message.text.strip().lstrip('@')
    context.user_data['reg_leader'] = leader
    await update.message.reply_text("📸 Send roster photos one by one. When done, send /done")
    context.user_data['reg_roster'] = []
    return REG_WAIT_ROSTER

async def reg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle roster photo upload"""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data.setdefault('reg_roster', []).append(file_id)
        count = len(context.user_data['reg_roster'])
        await update.message.reply_text(f"✅ Photo {count} received. Send more or /done when finished.")
    return REG_WAIT_ROSTER

async def reg_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Complete registration process"""
    tid = context.user_data.get('reg_tid')
    team_name = context.user_data.get('reg_teamname')
    leader = context.user_data.get('reg_leader')
    roster_files = context.user_data.get('reg_roster', [])
    
    if not tid or not team_name:
        await update.message.reply_text("❌ Session expired.")
        return ConversationHandler.END
    
    try:
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
        await update.message.reply_text(f"✅ Team '{team_name}' registered successfully! 🎉\n\nTotal teams: {count}")
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        await update.message.reply_text("❌ Error registering team. Please try again.")
    
    context.user_data.clear()
    return ConversationHandler.END

# =======================
# MAIN FUNCTION - UPDATED
# =======================

async def main():
    """Main function with modern application setup"""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        return
    
    logger.info("🚀 Starting Brawl Stars Tournament Bot with WORKING DELETION...")
    
    # Initialize database
    await init_db()
    
    # Build application with modern approach
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("create", create_tournament_simple))
    application.add_handler(CommandHandler("admin", admin_panel))
    
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
    
    # Callback queries - SIMPLIFIED PATTERNS
    application.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^(admin|view_t_|teams_|reg_|delete_|confirm_|manage_|generate_)"))
    
    # Text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("🤖 Bot is running with WORKING DELETION SYSTEM!")
    
    # Start the bot
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
