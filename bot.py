"""
Brawl Stars Tournament Bot - WORKING DELETION VERSION
"""

import os
import asyncio
import random
import logging
import aiosqlite
from typing import Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
    CallbackQueryHandler, ConversationHandler
)

# -----------------------
# CONFIG
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = {7665378359, 6548564636}  # Replace with your actual Telegram IDs
DATABASE = "tournaments.db"
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

def make_keyboard(items: list):
    kb = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in items]
    return InlineKeyboardMarkup(kb)

# =======================
# DATABASE FUNCTIONS
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
# DELETION FUNCTIONS
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
# BOT HANDLERS
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

# =======================
# CALLBACK HANDLERS
# =======================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main callback handler"""
    query = update.callback_query
    await query.answer()
    data = query.data

    logger.info(f"Callback received: {data}")

    if data == "admin_create":
        await query.message.reply_text("🏆 To create tournament, use:\n\n<code>/create Tournament Name 8</code>\n\nReplace with your tournament name and max teams.", parse_mode="HTML")

    elif data == "admin_list":
        await show_tournaments_for_management(query, context)

    elif data == "admin_delete_tournament":
        await show_tournaments_for_deletion(query, context)

    elif data == "admin_delete_team":
        await show_teams_for_deletion(query, context)

    elif data.startswith("delete_tournament_"):
        tournament_id = int(data.split("_")[2])
        await confirm_tournament_deletion(query, context, tournament_id)

    elif data.startswith("confirm_delete_tournament_"):
        tournament_id = int(data.split("_")[3])
        await execute_tournament_deletion(query, context, tournament_id)

    elif data.startswith("delete_team_"):
        team_id = int(data.split("_")[2])
        await execute_team_deletion(query, context, team_id)

    elif data.startswith("view_t_"):
        tournament_id = int(data.split("_")[2])
        await view_tournament_details(query, context, tournament_id)

    elif data == "admin_back":
        await admin_panel_callback(query, context)

async def show_tournaments_for_management(query, context):
    """Show tournaments for management"""
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    
    if not rows:
        await query.edit_message_text("❌ No tournaments available.")
        return
    
    items = []
    for tid, name, max_teams, status in rows:
        count = await count_registered(tid)
        status_emoji = "⚔️" if status == 'in_progress' else "✅" if status == 'finished' else "📝"
        items.append((f"{name} ({count}/{max_teams}) {status_emoji}", f"view_t_{tid}"))
    
    kb = make_keyboard(items)
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    
    await query.edit_message_text("🏆 Select tournament to manage:", reply_markup=kb)

async def show_tournaments_for_deletion(query, context):
    """Show tournaments for deletion"""
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

async def show_teams_for_deletion(query, context):
    """Show all teams for deletion"""
    teams_data = await db_fetchall("""
        SELECT t.id, t.name, tour.name 
        FROM teams t 
        JOIN tournaments tour ON t.tournament_id = tour.id 
        ORDER BY tour.id, t.id
    """)
    
    if not teams_data:
        await query.edit_message_text("❌ No teams to delete.")
        return
    
    items = []
    for team_id, team_name, tournament_name in teams_data:
        items.append((f"🗑️ {team_name} ({tournament_name})", f"delete_team_{team_id}"))
    
    kb = make_keyboard(items)
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    
    await query.edit_message_text("👥 Select team to DELETE:", reply_markup=kb)

async def view_tournament_details(query, context, tournament_id: int):
    """Show tournament details"""
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
    if query.from_user.id in ADMINS:
        kb.append([InlineKeyboardButton("🗑️ Delete Tournament", callback_data=f"delete_tournament_{tournament_id}")])
    
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="admin_list")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def confirm_tournament_deletion(query, context, tournament_id: int):
    """Show confirmation for tournament deletion"""
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

async def execute_tournament_deletion(query, context, tournament_id: int):
    """Execute tournament deletion"""
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

async def execute_team_deletion(query, context, team_id: int):
    """Execute team deletion"""
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

async def admin_panel_callback(query, context):
    """Show admin panel via callback"""
    kb = [
        [InlineKeyboardButton("🏆 Create Tournament", callback_data="admin_create")],
        [InlineKeyboardButton("📋 Manage Tournaments", callback_data="admin_list")],
        [InlineKeyboardButton("🗑️ Delete Tournament", callback_data="admin_delete_tournament")],
        [InlineKeyboardButton("👥 Delete Team", callback_data="admin_delete_team")]
    ]
    await query.edit_message_text("🛠️ Admin Panel - Choose an option:", reply_markup=InlineKeyboardMarkup(kb))

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
        items.append((f"👀 {name}", f"view_t_{tid}"))
    
    await update.message.reply_text("Select tournament to view teams:", reply_markup=make_keyboard(items))

# =======================
# MAIN FUNCTION
# =======================

async def main():
    """Main function"""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        return
    
    logger.info("🚀 Starting Brawl Stars Tournament Bot...")
    
    # Initialize database
    await init_db()
    
    # Build application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("create", create_tournament_simple))
    application.add_handler(CommandHandler("admin", admin_panel))
    
    # Callback queries
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("🤖 Bot is running!")
    
    # Start the bot
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
