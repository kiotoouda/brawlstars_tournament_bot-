"""
Brawl Stars Tournament Bot - Persistent Version for Render
"""

import os
import asyncio
import random
import logging
import aiosqlite
import aiohttp
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
ROSTERS_DIR = Path("./rosters")

# Free cloud storage for backups
BACKUP_URL = "https://api.jsonbin.io/v3/b"
BACKUP_HEADERS = {
    'Content-Type': 'application/json',
    'X-Master-Key': '$2a$10$YOUR_FREE_KEY_HERE'  # Get free key from jsonbin.io
}
# -----------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
(REG_TEAM_NAME, REG_LEADER_USERNAME, REG_WAIT_ROSTER,
 ADMIN_CREATE_NAME, ADMIN_CREATE_MAXTEAMS) = range(5)

# Database with backup/restore
async def init_db():
    # First try to restore from backup
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
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS roster_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            telegram_file_id TEXT,
            file_data TEXT  -- Store file as base64 for persistence
        );
        CREATE TABLE IF NOT EXISTS bracket_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            round_index INTEGER NOT NULL,
            match_index INTEGER NOT NULL,
            teamA_id INTEGER,
            teamB_id INTEGER,
            winner_team_id INTEGER
        );
        """)
        await db.commit()

async def backup_database():
    """Backup entire database to free cloud storage"""
    try:
        # Read database file
        with open(DATABASE, 'rb') as f:
            db_data = base64.b64encode(f.read()).decode()
        
        # Backup to free service
        async with aiohttp.ClientSession() as session:
            data = {"database": db_data, "timestamp": str(asyncio.get_event_loop().time())}
            async with session.put(BACKUP_URL, json=data, headers=BACKUP_HEADERS) as response:
                if response.status == 200:
                    logger.info("✅ Database backed up successfully")
                else:
                    logger.warning("⚠️ Backup failed but continuing")
    except Exception as e:
        logger.error(f"Backup error: {e}")

async def restore_from_backup():
    """Restore database from cloud backup"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BACKUP_URL + "/latest", headers=BACKUP_HEADERS) as response:
                if response.status == 200:
                    data = await response.json()
                    db_data = base64.b64decode(data['record']['database'])
                    with open(DATABASE, 'wb') as f:
                        f.write(db_data)
                    logger.info("✅ Database restored from backup")
    except Exception as e:
        logger.warning(f"Restore failed, starting fresh: {e}")

async def db_execute(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(query, params)
        await db.commit()
    # Auto-backup after every write operation
    await backup_database()

async def db_fetchone(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(query, params)
        return await cur.fetchone()

async def db_fetchall(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(query, params)
        return await cur.fetchall()

# Store photos in database instead of local files
async def save_roster_photos(team_id: int, file_ids: List[str]):
    """Save roster photos as file IDs in database (persistent)"""
    for file_id in file_ids:
        await db_execute(
            "INSERT INTO roster_files (team_id, telegram_file_id) VALUES (?, ?)",
            (team_id, file_id)
        )

async def get_roster_photos(team_id: int) -> List[str]:
    """Get roster photo file IDs from database"""
    rows = await db_fetchall("SELECT telegram_file_id FROM roster_files WHERE team_id = ?", (team_id,))
    return [row[0] for row in rows] if rows else []

# Keep your existing utility functions but remove file operations
async def count_registered(tid: int) -> int:
    row = await db_fetchone("SELECT COUNT(*) FROM teams WHERE tournament_id = ?", (tid,))
    return row[0] if row else 0

def make_keyboard(items: List[Tuple[str, str]]):
    kb = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in items]
    return InlineKeyboardMarkup(kb)

# Keep your existing handlers but MODIFY photo handling:

async def reg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store photo file IDs (not actual files)"""
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
    roster_files = context.user_data.get('reg_roster', [])
    
    if not roster_files:
        await update.message.reply_text("❌ Please send at least 1 photo.")
        return REG_WAIT_ROSTER
    
    # Save team to database
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(
            "INSERT INTO teams (tournament_id, name, leader_username) VALUES (?, ?, ?)",
            (tid, team_name, leader)
        )
        team_id = cur.lastrowid
        
        # Save roster file IDs to database
        for file_id in roster_files:
            await db.execute(
                "INSERT INTO roster_files (team_id, telegram_file_id) VALUES (?, ?)",
                (team_id, file_id)
            )
        await db.commit()
    
    # Backup to cloud
    await backup_database()
    
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

# Modify the team view to use stored file IDs
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # ... [keep your existing callback code] ...
    
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
        
        # Get roster photos from database (file IDs)
        file_ids = await get_roster_photos(team_id)
        if file_ids:
            await query.message.reply_text(text)
            media = [InputMediaPhoto(file_id) for file_id in file_ids]
            try:
                await query.message.reply_media_group(media)
            except Exception as e:
                logger.error(f"Error sending photos: {e}")
                await query.message.reply_text("📷 Roster photos available")
        else:
            await query.edit_message_text(text + "\n📷 No roster photos")

# Add auto-backup every hour
async def auto_backup():
    """Auto-backup database every hour"""
    while True:
        await asyncio.sleep(3600)  # 1 hour
        await backup_database()

# Keep your existing start, help, admin functions...
# [KEEP ALL YOUR EXISTING HANDLER CODE]

# Modified main function
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        return
    
    logger.info("🚀 Starting Brawl Stars Tournament Bot...")
    
    # Initialize database with backup/restore
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.run_until_complete(init_db())
    
    # Start auto-backup in background
    asyncio.ensure_future(auto_backup())
    
    # Build application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add all your handlers (keep existing)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("create", create_tournament_simple))
    app.add_handler(CommandHandler("admin_list", admin_panel))
    
    # Add registration conversation
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
    
    # Add other handlers
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("🤖 Bot is running with persistent storage...")
    app.run_polling()

if __name__ == "__main__":
    main()
