"""
Brawl Stars Tournament Bot - Fixed Version
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
ADMINS = {7665378359, 6548564636}  # Your Telegram user IDs
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
            if update.callback_query:
                await update.callback_query.answer("Admin only", show_alert=True)
            else:
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
    
    # Add admin buttons if user is admin
    if user.id in ADMINS:
        kb.append([KeyboardButton("🛠️ Admin Panel")])
    
    await update.message.reply_text(
        greeting, 
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode="HTML"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = """
🤖 **Brawl Stars Tournament Bot Help**

**For Players:**
• Use "📋 Tournaments" to browse and register
• Use "🔎 View Teams" to see registered teams
• Follow the registration process when joining a tournament
"""
    
    if user.id in ADMINS:
        text += """
**For Admins:**
• Use "🛠️ Admin Panel" for admin controls
• /create - Create new tournament
• Use tournament admin panel to manage brackets
"""
    
    text += """
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

# Admin panel
@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🏆 Create Tournament", callback_data="admin_create")],
        [InlineKeyboardButton("📋 Manage Tournaments", callback_data="admin_list")],
        [InlineKeyboardButton("🗑️ Delete Tournament", callback_data="admin_delete")],
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")]
    ]
    await update.message.reply_text("🛠️ Admin Panel", reply_markup=InlineKeyboardMarkup(kb))

# SIMPLE CREATE TOURNAMENT COMMAND - NO CONVERSATION
@admin_only
async def create_tournament_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simple tournament creation without conversation"""
    if not context.args:
        await update.message.reply_text("Usage: /create <tournament_name> <max_teams>\nExample: /create Summer Cup 16")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /create <tournament_name> <max_teams>\nExample: /create Summer Cup 16")
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
        
        # Get the created tournament ID
        tournament = await db_fetchone("SELECT id FROM tournaments ORDER BY id DESC LIMIT 1")
        tid = tournament[0] if tournament else "unknown"
        
        await update.message.reply_text(f"✅ Tournament created! 🎉\nName: {name}\nMax Teams: {max_teams}\nID: {tid}")
        
    except ValueError:
        await update.message.reply_text("❌ Max teams must be a number.\nUsage: /create <name> <max_teams>")
    except Exception as e:
        logger.error(f"Error creating tournament: {e}")
        await update.message.reply_text("❌ Error creating tournament. Please try again.")

# Callback handler
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "none":
        return

    elif data.startswith("view_t_"):
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

    elif data.startswith("team_"):
        parts = data.split("_")
        tid = int(parts[1])
        team_id = int(parts[2])
        
        team = await db_fetchone("SELECT name, leader_username FROM teams WHERE id = ?", (team_id,))
        if not team:
            await query.edit_message_text("❌ Team not found.")
            return
            
        name, leader = team
        text = f"👥 Team: {name}\n👑 Leader: @{leader if leader else 'N/A'}"
        
        # Get roster photos
        photos = await db_fetchall("SELECT telegram_file_id FROM roster_files WHERE team_id = ?", (team_id,))
        if photos:
            await query.message.reply_text(text)
            media = [InputMediaPhoto(photo[0]) for photo in photos]
            try:
                await query.message.reply_media_group(media)
            except Exception as e:
                logger.error(f"Error sending photos: {e}")
                await query.message.reply_text("📷 Roster photos available but couldn't display.")
        else:
            await query.edit_message_text(text + "\n📷 No roster photos")

    # Admin callbacks
    elif data == "admin_create":
        await query.message.reply_text("🏆 To create a tournament, use:\n\n<code>/create Tournament Name 16</code>\n\nReplace 'Tournament Name' with your tournament name and '16' with the maximum number of teams.", parse_mode="HTML")

    elif data == "admin_list":
        rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
        if not rows:
            await query.edit_message_text("❌ No tournaments.")
            return
        
        items = []
        for row in rows:
            tid, name, max_teams, status = row
            count = await count_registered(tid)
            items.append((f"{name} ({count}/{max_teams})", f"admin_t_{tid}"))
        
        await query.edit_message_text("🏆 Tournaments:", reply_markup=make_keyboard(items))

    elif data.startswith("admin_t_"):
        tid = int(data.split("_")[-1])
        row = await db_fetchone("SELECT name, status FROM tournaments WHERE id = ?", (tid,))
        if not row:
            await query.edit_message_text("❌ Tournament not found.")
            return
            
        name, status = row
        count = await count_registered(tid)
        
        kb = [
            [InlineKeyboardButton("📋 View Registrations", callback_data=f"admin_reg_{tid}")],
            [InlineKeyboardButton("🗑️ Remove Team", callback_data=f"admin_remove_{tid}")],
            [InlineKeyboardButton("⚔️ Generate Bracket", callback_data=f"admin_bracket_{tid}")],
            [InlineKeyboardButton("🧹 Delete Tournament", callback_data=f"admin_del_{tid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_list")]
        ]
        
        text = f"🛠️ Admin: {name}\nStatus: {status}\nTeams: {count}"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("admin_reg_"):
        tid = int(data.split("_")[-1])
        teams = await db_fetchall("SELECT id, name, leader_username FROM teams WHERE tournament_id = ? ORDER BY id", (tid,))
        if not teams:
            await query.edit_message_text("❌ No teams registered.")
            return
            
        text = "📋 Registered Teams:\n\n"
        for i, (team_id, name, leader) in enumerate(teams, 1):
            text += f"{i}. {name} - @{leader or 'N/A'}\n"
            
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
            await db_execute("DELETE FROM teams WHERE id = ?", (team_id,))
            await query.edit_message_text(f"✅ Removed team: {team[0]}")
        else:
            await query.edit_message_text("❌ Team not found.")

    elif data.startswith("admin_del_"):
        tid = int(data.split("_")[-1])
        tournament = await db_fetchone("SELECT name FROM tournaments WHERE id = ?", (tid,))
        if tournament:
            kb = [
                [InlineKeyboardButton("✅ Confirm Delete", callback_data=f"confirm_del_{tid}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"admin_t_{tid}")]
            ]
            await query.edit_message_text(
                f"⚠️ Delete tournament '{tournament[0]}'? This will remove all teams and data.",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        else:
            await query.edit_message_text("❌ Tournament not found.")

    elif data.startswith("confirm_del_"):
        tid = int(data.split("_")[-1])
        tournament = await db_fetchone("SELECT name FROM tournaments WHERE id = ?", (tid,))
        if tournament:
            await db_execute("DELETE FROM tournaments WHERE id = ?", (tid,))
            await query.edit_message_text(f"✅ Deleted tournament: {tournament[0]}")
        else:
            await query.edit_message_text("❌ Tournament not found.")

    elif data.startswith("admin_bracket_"):
        tid = int(data.split("_")[-1])
        count = await count_registered(tid)
        if count < 2:
            await query.edit_message_text("❌ Need at least 2 teams for bracket.")
            return
            
        # Generate bracket
        await generate_bracket(tid)
        await db_execute("UPDATE tournaments SET status = 'in_progress' WHERE id = ?", (tid,))
        await query.edit_message_text("✅ Bracket generated! Tournament status: in_progress")

    elif data == "admin_stats":
        total_tournaments = await db_fetchone("SELECT COUNT(*) FROM tournaments")
        total_teams = await db_fetchone("SELECT COUNT(*) FROM teams")
        active_tournaments = await db_fetchone("SELECT COUNT(*) FROM tournaments WHERE status != 'finished'")
        
        text = f"""
📊 Bot Statistics:
• Total Tournaments: {total_tournaments[0]}
• Total Teams: {total_teams[0]}
• Active Tournaments: {active_tournaments[0]}
• Admin Users: {len(ADMINS)}
"""
        await query.edit_message_text(text)

    elif data == "admin_delete":
        rows = await db_fetchall("SELECT id, name FROM tournaments ORDER BY id DESC")
        if not rows:
            await query.edit_message_text("❌ No tournaments to delete.")
            return
            
        items = [(f"🗑️ {name}", f"admin_del_{tid}") for tid, name in rows]
        await query.edit_message_text("Select tournament to delete:", reply_markup=make_keyboard(items))

# Bracket generation
async def generate_bracket(tid: int):
    teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ?", (tid,))
    teams = [{"id": row[0], "name": row[1]} for row in teams]
    random.shuffle(teams)
    
    # Simple bracket generation for now
    for i in range(0, len(teams), 2):
        if i + 1 < len(teams):
            await db_execute(
                "INSERT INTO bracket_matches (tournament_id, round_index, match_index, teamA_id, teamB_id) VALUES (?, ?, ?, ?, ?)",
                (tid, 0, i//2, teams[i]["id"], teams[i+1]["id"])
            )

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

# Text message handler
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    
    if text in ("📋 Tournaments", "tournaments"):
        await tournaments_button(update, context)
    elif text in ("🔎 View Teams", "teams"):
        kb = await show_tournaments_keyboard()
        await update.message.reply_text("Select tournament:", reply_markup=kb)
    elif text in ("ℹ️ Help", "help"):
        await help_cmd(update, context)
    elif text in ("🛠️ Admin Panel", "admin") and user.id in ADMINS:
        await admin_panel(update, context)
    else:
        # Check if user is in registration flow
        if context.user_data.get('reg_tid'):
            # User is in registration, handle accordingly
            await update.message.reply_text("Please complete your registration or send /done to cancel.")
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
    
    # Fix for asyncio warning
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.run_until_complete(init_db())
    
    # Build application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("create", create_tournament_simple))
    app.add_handler(CommandHandler("admin_list", admin_panel))
    
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
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Start polling
    logger.info("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
