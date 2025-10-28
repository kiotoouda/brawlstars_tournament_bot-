"""
Brawl Stars Tournament Bot - Complete Version
"""

import os
import asyncio
import random
import logging
import aiosqlite
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
ADMINS = {7665378359, 6548564636}  # Replace with your Telegram user IDs
DATABASE = "tournaments.db"
ROSTERS_DIR = Path("./rosters")
# -----------------------

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Conversation states
(REG_TEAM_NAME, REG_LEADER_USERNAME, REG_WAIT_ROSTER,
 ADMIN_CREATE_NAME, ADMIN_CREATE_MAXTEAMS) = range(5)

# Admin decorator
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user and user.id not in ADMINS:
            await update.effective_message.reply_text("⛔ Admin-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# Database functions
async def init_db():
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
            FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS roster_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            telegram_file_id TEXT,
            local_path TEXT,
            FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS bracket_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_index INTEGER NOT NULL,
            match_index INTEGER NOT NULL,
            teamA_id INTEGER,
            teamB_id INTEGER,
            winner_team_id INTEGER,
            FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        );
        """)
        await db.commit()

async def db_execute(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(query, params)
        await db.commit()

async def db_fetchone(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(query, params)
        return await cur.fetchone()

async def db_fetchall(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(query, params)
        return await cur.fetchall()

# Utility functions
async def count_registered(tid: int) -> int:
    row = await db_fetchone("SELECT COUNT(*) FROM teams WHERE tournament_id = ?", (tid,))
    return row[0] if row else 0

def ensure_roster_dir(tid: int, team_id: int) -> Path:
    path = ROSTERS_DIR / str(tid) / str(team_id)
    path.mkdir(parents=True, exist_ok=True)
    return path

def make_keyboard(items: List[Tuple[str, str]]):
    kb = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in items]
    return InlineKeyboardMarkup(kb)

# Start command with greeting
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    greeting = f"""
🎮 Welcome to Brawl Stars Tournament Bot, {user.first_name}! 🎮

I can help you organize and manage Brawl Stars tournaments with ease!

✨ Features:
• Create and manage tournaments
• Team registration with roster photos  
• Automatic bracket generation
• Live tournament progress tracking
• Admin controls for tournament management

Use the buttons below to get started! 🚀
    """
    
    kb = [
        [KeyboardButton("📋 Tournaments"), KeyboardButton("🔎 View Teams")],
        [KeyboardButton("ℹ️ Help")]
    ]
    await update.message.reply_text(
        greeting, 
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode="HTML"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🤖 **Brawl Stars Tournament Bot Help**

**For Players:**
• Use "📋 Tournaments" to browse and register
• Use "🔎 View Teams" to see registered teams
• Follow the registration process when joining a tournament

**For Admins:**
• /create_tournament - Create new tournament
• /delete_tournament - Delete tournament  
• /admin_list - List all tournaments
• Use tournament admin panel to manage brackets

**Need Help?**
Contact the tournament organizers!
    """
    await update.message.reply_text(text, parse_mode="Markdown")

# Show tournaments
async def show_tournaments_keyboard():
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    items = []
    for row in rows:
        tid, name, max_teams, status = row
        count = await count_registered(tid)
        label = f"{name} ({count}/{max_teams})"
        items.append((label, f"view_t_{tid}"))
    return make_keyboard(items) if items else make_keyboard([("No tournaments", "none")])

async def tournaments_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = await show_tournaments_keyboard()
    await update.message.reply_text("🏆 Available tournaments:", reply_markup=kb)

# Callback handler
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "none":
        return

    if data.startswith("view_t_"):
        tid = int(data.split("_")[-1])
        row = await db_fetchone("SELECT name, max_teams, status FROM tournaments WHERE id = ?", (tid,))
        if not row:
            await query.edit_message_text("❌ Tournament not found.")
            return
        
        name, max_teams, status = row
        count = await count_registered(tid)
        text = f"🏆 <b>{name}</b>\n📊 Teams: {count}/{max_teams}\n🎯 Status: {status}"
        
        kb = []
        if status == 'registration':
            kb.append([InlineKeyboardButton("✅ Register Team", callback_data=f"reg_{tid}")])
        kb.append([InlineKeyboardButton("👀 View Teams", callback_data=f"teams_{tid}")])
        if query.from_user.id in ADMINS:
            kb.append([InlineKeyboardButton("🛠️ Admin", callback_data=f"admin_{tid}")])
            
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith("reg_"):
        tid = int(data.split("_")[-1])
        context.user_data['reg_tid'] = tid
        await query.message.reply_text("📝 Enter your team name:")
        return REG_TEAM_NAME

    elif data.startswith("teams_"):
        tid = int(data.split("_")[-1])
        teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ?", (tid,))
        if not teams:
            await query.edit_message_text("❌ No teams registered.")
            return
        items = [(f"👥 {name}", f"team_{tid}_{team_id}") for team_id, name in teams]
        await query.edit_message_text("📋 Teams:", reply_markup=make_keyboard(items))

# Registration flow
async def reg_team_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team_name = update.message.text.strip()
    tid = context.user_data.get('reg_tid')
    
    if not tid:
        await update.message.reply_text("❌ Session expired.")
        return ConversationHandler.END
    
    # Check if team name exists
    existing = await db_fetchone("SELECT id FROM teams WHERE tournament_id = ? AND name = ?", (tid, team_name))
    if existing:
        await update.message.reply_text("❌ Team name taken. Choose another:")
        return REG_TEAM_NAME
    
    context.user_data['reg_teamname'] = team_name
    await update.message.reply_text("👑 Enter team leader's username (without @) or '-' to skip:")
    return REG_LEADER_USERNAME

async def reg_leader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leader = update.message.text.strip().lstrip('@')
    context.user_data['reg_leader'] = leader if leader != "-" else None
    await update.message.reply_text("📸 Send roster photos (1-6). Send /done when finished.")
    context.user_data['reg_roster'] = []
    return REG_WAIT_ROSTER

async def reg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data.setdefault('reg_roster', []).append(file_id)
        count = len(context.user_data['reg_roster'])
        await update.message.reply_text(f"✅ Photo {count} received. Send more or /done.")
    return REG_WAIT_ROSTER

async def reg_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = context.user_data.get('reg_tid')
    team_name = context.user_data.get('reg_teamname')
    leader = context.user_data.get('reg_leader')
    roster = context.user_data.get('reg_roster', [])
    
    if not roster:
        await update.message.reply_text("❌ Please send at least 1 photo.")
        return REG_WAIT_ROSTER
    
    # Save team to database
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(
            "INSERT INTO teams (tournament_id, name, leader_username) VALUES (?, ?, ?)",
            (tid, team_name, leader)
        )
        team_id = cur.lastrowid
        
        # Save roster photos
        roster_path = ensure_roster_dir(tid, team_id)
        for i, file_id in enumerate(roster, 1):
            try:
                file = await context.bot.get_file(file_id)
                local_path = roster_path / f"{i}.jpg"
                await file.download_to_drive(custom_path=str(local_path))
                await db.execute(
                    "INSERT INTO roster_files (team_id, telegram_file_id, local_path) VALUES (?, ?, ?)",
                    (team_id, file_id, str(local_path))
                )
            except Exception as e:
                logger.error(f"Error saving photo: {e}")
                await db.execute(
                    "INSERT INTO roster_files (team_id, telegram_file_id) VALUES (?, ?)",
                    (team_id, file_id)
                )
        await db.commit()
    
    # Notify admins
    count = await count_registered(tid)
    for admin_id in ADMINS:
        try:
            await context.bot.send_message(
                admin_id, 
                f"📢 New team: {team_name} in tournament {tid}. Total: {count}"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
    
    await update.message.reply_text(f"✅ Team '{team_name}' registered successfully! 🎉")
    context.user_data.clear()
    return ConversationHandler.END

# Admin commands
@admin_only
async def create_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏆 Enter tournament name:")
    return ADMIN_CREATE_NAME

async def admin_create_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Name cannot be empty. Enter name:")
        return ADMIN_CREATE_NAME
    context.user_data['tournament_name'] = name
    await update.message.reply_text("🔢 Enter max number of teams:")
    return ADMIN_CREATE_MAXTEAMS

async def admin_create_maxteams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        max_teams = int(update.message.text.strip())
        if max_teams < 2:
            await update.message.reply_text("❌ Minimum 2 teams. Enter again:")
            return ADMIN_CREATE_MAXTEAMS
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number:")
        return ADMIN_CREATE_MAXTEAMS
    
    name = context.user_data['tournament_name']
    await db_execute(
        "INSERT INTO tournaments (name, max_teams, status) VALUES (?, ?, 'registration')",
        (name, max_teams)
    )
    
    await update.message.reply_text(f"✅ Tournament '{name}' created!")
    context.user_data.clear()
    return ConversationHandler.END

@admin_only
async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    if not rows:
        await update.message.reply_text("❌ No tournaments.")
        return
    
    items = []
    for row in rows:
        tid, name, max_teams, status = row
        count = await count_registered(tid)
        items.append((f"{name} ({count}/{max_teams})", f"admin_{tid}"))
    
    await update.message.reply_text("🏆 Tournaments:", reply_markup=make_keyboard(items))

# Text message handler
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text in ("📋 Tournaments", "tournaments"):
        await tournaments_button(update, context)
    elif text in ("🔎 View Teams", "teams"):
        kb = await show_tournaments_keyboard()
        await update.message.reply_text("Select tournament:", reply_markup=kb)
    elif text in ("ℹ️ Help", "help"):
        await help_cmd(update, context)
    else:
        await update.message.reply_text("❓ Use /help for commands")

# Main function
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        return
    
    logger.info("🚀 Starting Brawl Stars Tournament Bot...")
    
    # Initialize
    ROSTERS_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.get_event_loop().run_until_complete(init_db())
    
    # Build application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin_list", admin_list))
    
    # Create tournament conversation
    create_conv = ConversationHandler(
        entry_points=[CommandHandler("create_tournament", create_tournament)],
        states={
            ADMIN_CREATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_name)],
            ADMIN_CREATE_MAXTEAMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_maxteams)],
        },
        fallbacks=[]
    )
    app.add_handler(create_conv)
    
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
        fallbacks=[CommandHandler("done", reg_done)]
    )
    app.add_handler(reg_conv)
    
    # Callback queries
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Start polling
    logger.info("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
