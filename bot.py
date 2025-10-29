"""
Brawl Stars Tournament Bot - Persistent Version with GitHub Backup
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# -----------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
(REG_TEAM_NAME, REG_LEADER_USERNAME, REG_WAIT_ROSTER,
 ADMIN_CREATE_NAME, ADMIN_CREATE_MAXTEAMS) = range(5)

# Database with GitHub backup
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
            telegram_file_id TEXT
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
    logger.info("✅ Database initialized")

# Store the gist ID after first backup
gist_id = None

async def backup_database():
    """Backup database to GitHub Gist"""
    if not GITHUB_TOKEN:
        logger.warning("⚠️ No GitHub token for backup")
        return
    
    try:
        # Read database file
        with open(DATABASE, 'rb') as f:
            db_data = base64.b64encode(f.read()).decode()
        
        backup_data = {
            "database": db_data,
            "timestamp": str(asyncio.get_event_loop().time())
        }
        
        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        async with aiohttp.ClientSession() as session:
            global gist_id
            if gist_id:
                # Update existing gist
                url = f'https://api.github.com/gists/{gist_id}'
                data = {"files": {"bot_backup.json": {"content": json.dumps(backup_data)}}}
                async with session.patch(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        logger.info("✅ Database backed up to Gist")
                    else:
                        logger.warning(f"⚠️ Backup update failed: {response.status}")
            else:
                # Create new gist
                url = 'https://api.github.com/gists'
                data = {
                    "public": False,
                    "description": "Brawl Stars Bot Backup",
                    "files": {"bot_backup.json": {"content": json.dumps(backup_data)}}
                }
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 201:
                        result = await response.json()
                        gist_id = result['id']
                        logger.info(f"✅ New backup Gist created: {gist_id}")
                    else:
                        logger.error(f"❌ Gist creation failed: {response.status}")
                        
    except Exception as e:
        logger.error(f"Backup error: {e}")

async def restore_from_backup():
    """Restore database from GitHub Gist"""
    if not GITHUB_TOKEN:
        logger.warning("⚠️ No GitHub token for restore")
        return
    
    try:
        # First, try to find existing gists
        headers = {'Authorization': f'token {GITHUB_TOKEN}'}
        
        async with aiohttp.ClientSession() as session:
            url = 'https://api.github.com/gists'
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    gists = await response.json()
                    for gist in gists:
                        if 'bot_backup.json' in gist['files']:
                            global gist_id
                            gist_id = gist['id']
                            
                            # Get the backup data
                            gist_url = f'https://api.github.com/gists/{gist_id}'
                            async with session.get(gist_url, headers=headers) as gist_response:
                                if gist_response.status == 200:
                                    result = await gist_response.json()
                                    backup_data = json.loads(result['files']['bot_backup.json']['content'])
                                    db_data = base64.b64decode(backup_data['database'])
                                    
                                    with open(DATABASE, 'wb') as f:
                                        f.write(db_data)
                                    logger.info("✅ Database restored from Gist")
                                    return
                    
                    logger.info("ℹ️ No existing backup found, starting fresh")
                    
    except Exception as e:
        logger.warning(f"Restore failed, starting fresh: {e}")

async def db_execute(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(query, params)
        await db.commit()
    # Auto-backup after important operations
    await backup_database()

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

def make_keyboard(items: List[Tuple[str, str]]):
    kb = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in items]
    return InlineKeyboardMarkup(kb)

# Auto-backup every hour
async def auto_backup():
    """Auto-backup database every hour"""
    while True:
        await asyncio.sleep(3600)  # 1 hour
        await backup_database()
        logger.info("🔄 Auto-backup completed")

# [KEEP ALL YOUR EXISTING BOT CODE HERE]
# Just replace the database functions and add the backup calls

# Add backup to your registration flow
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
        
        # Save roster file IDs
        for file_id in roster_files:
            await db.execute(
                "INSERT INTO roster_files (team_id, telegram_file_id) VALUES (?, ?)",
                (team_id, file_id)
            )
        await db.commit()
    
    # Backup to GitHub
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

# Modified main function
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN environment variable is required!")
        return
    
    logger.info("🚀 Starting Brawl Stars Tournament Bot...")
    
    # Initialize
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
    
    # Add all your existing handlers
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
    
    logger.info("🤖 Bot is running with GitHub backup...")
    app.run_polling()

if __name__ == "__main__":
    main()
