"""
Brawl Stars Tournament Bot - Fixed and Improved Version
Features:
- SQLite storage (aiosqlite) for robust persistence and concurrency
- Roster photos downloaded to disk under ./rosters/{tournament_id}/{team_id}/
- Auto-generate single-elimination bracket when max teams reached (or manually)
- Admins create/delete tournaments, remove teams, start bracket, record match winners
- Live registration list, admin notifications, optional 3rd-place match support
- Greeting message when someone starts the bot
- Simple to run locally for checking (render) and easy to deploy

Depends:
    python-telegram-bot==20.4
    aiosqlite

Run:
    pip install python-telegram-bot==20.4 aiosqlite
    python brawl_tourney_bot.py
"""

import os
import asyncio
import json
import random
import logging
import aiosqlite
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from functools import wraps

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton, ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters,
    CallbackQueryHandler, ConversationHandler
)

# -----------------------
# CONFIG - set these
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMINS = {7665378359, 6548564636}   # set your numeric Telegram user IDs
DATABASE = "tournaments.db"
ROSTERS_DIR = Path("./rosters")
# -----------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Conversation states
(REG_TEAM_NAME, REG_LEADER_USERNAME, REG_WAIT_ROSTER,
 ADMIN_CREATE_NAME, ADMIN_CREATE_MAXTEAMS) = range(5)

# Utility decorators
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        uid = user.id if user else None
        if uid not in ADMINS:
            if update.callback_query:
                await update.callback_query.answer("Admin only", show_alert=True)
            else:
                await update.effective_message.reply_text("⛔ Admin-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# -----------------------
# Database helpers
# -----------------------
async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            max_teams INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'registration', -- registration|full|in_progress|finished
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
        row = await cur.fetchone()
        return row

async def db_fetchall(query: str, params: tuple = ()):
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
        return rows

# -----------------------
# Utility functions
# -----------------------
def tournament_summary_row(row) -> str:
    tid, name, max_teams, status, created = row
    return f"🏆 <b>{name}</b>\nID: {tid}\nMax teams: {max_teams}\nStatus: {status}\nRegistered: (live count shown separately)"

async def count_registered(tid: int) -> int:
    r = await db_fetchone("SELECT COUNT(*) FROM teams WHERE tournament_id = ?", (tid,))
    return r[0] if r else 0

def ensure_roster_dir(tid: int, team_id: int) -> Path:
    p = ROSTERS_DIR / str(tid) / str(team_id)
    p.mkdir(parents=True, exist_ok=True)
    return p

def make_keyboard_from_list(items: List[Tuple[str,str]]):
    # items: list of (label, callback_data)
    kb = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in items]
    return InlineKeyboardMarkup(kb)

# -----------------------
# Core handlers - basic
# -----------------------
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

Use the buttons below or commands to get started! 🚀
    """
    
    kb = [
        [KeyboardButton("📋 Tournaments"), KeyboardButton("🔎 View Teams")],
        [KeyboardButton("ℹ️ Help")]
    ]
    await update.message
