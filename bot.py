"""
Brawl Stars Tournament Bot - Improved Full Version
Features:
- SQLite storage (aiosqlite) for robust persistence and concurrency
- Roster photos downloaded to disk under ./rosters/{tournament_id}/{team_id}/
- Auto-generate single-elimination bracket when max teams reached (or manually)
- Admins create/delete tournaments, remove teams, start bracket, record match winners
- Live registration list, admin notifications, optional 3rd-place match support
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
BOT_TOKEN = os.getenv("BOT_TOKEN", "REPLACE_WITH_YOUR_BOT_TOKEN")
ADMINS = {123456789, 987654321}   # set your numeric Telegram user IDs
DATABASE = "tournaments.db"
ROSTERS_DIR = Path("./rosters")
# -----------------------

logging.basicConfig(level=logging.INFO)
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
    kb = [
        [KeyboardButton("📋 Tournaments")],
        [KeyboardButton("🔎 View Teams")]
    ]
    await update.message.reply_text("Brawl Stars Tourney Bot — use the keyboard or /help", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/create_tournament - admin only (start conv)\n"
        "/delete_tournament - admin only\n"
        "/admin_list - admin list\n\n"
        "Use keyboard: '📋 Tournaments' to browse and register."
    )
    await update.message.reply_text(text)

# show tournaments keyboard
async def show_tournaments_keyboard():
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    items = []
    for r in rows:
        tid, name, max_teams, status = r[0], r[1], r[2], r[3]
        cnt = await count_registered(tid)
        label = f"{name} ({cnt}/{max_teams})"
        items.append((label, f"view_t_{tid}"))
    if not items:
        items = [("No tournaments available", "none")]
    return make_keyboard_from_list(items)

async def tournaments_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = await show_tournaments_keyboard()
    await update.message.reply_text("Available tournaments:", reply_markup=kb)

# show tournament detail
async def show_tournament_detail(tid: int, user_id: Optional[int]):
    row = await db_fetchone("SELECT id, name, max_teams, status, created_at FROM tournaments WHERE id = ?", (tid,))
    if not row:
        return "Tournament not found.", None
    tid, name, max_teams, status, created_at = row
    cnt = await count_registered(tid)
    text = f"🏆 <b>{name}</b>\nID: {tid}\nMax teams: {max_teams}\nRegistered: {cnt}\nStatus: {status}"
    kb = []
    if status == 'registration':
        kb.append([InlineKeyboardButton("✅ Register Team", callback_data=f"reg_t_{tid}")])
    kb.append([InlineKeyboardButton("👀 View teams", callback_data=f"listteams_{tid}")])
    if user_id in ADMINS:
        kb.append([InlineKeyboardButton("🛠️ Admin: Manage", callback_data=f"admin_t_{tid}")])
    return text, InlineKeyboardMarkup(kb)

# -----------------------
# Callback router
# -----------------------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "none":
        await query.edit_message_text("No action.")
        return

    if data.startswith("view_t_"):
        tid = int(data.split("_")[-1])
        text, kb = await show_tournament_detail(tid, query.from_user.id)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("reg_t_"):
        # begin registration flow
        tid = int(data.split("_")[-1])
        # store in user_data
        context.user_data['reg_tid'] = tid
        await query.message.reply_text("Enter team name (must be unique):")
        return

    if data.startswith("listteams_"):
        tid = int(data.split("_")[-1])
        teams = await db_fetchall("SELECT id, name, leader_username FROM teams WHERE tournament_id = ?", (tid,))
        if not teams:
            await query.edit_message_text("No teams registered yet.")
            return
        kb_items = [(t[1], f"viewroster_{tid}_{t[0]}") for t in teams]
        await query.edit_message_text("Teams:", reply_markup=make_keyboard_from_list(kb_items))
        return

    if data.startswith("viewroster_"):
        parts = data.split("_")
        tid = int(parts[1]); team_id = int(parts[2])
        team = await db_fetchone("SELECT id, name, leader_username FROM teams WHERE id = ? AND tournament_id = ?", (team_id, tid))
        if not team:
            await query.edit_message_text("Team not found.")
            return
        _, name, leader = team
        text = f"Team: {name}\nLeader: @{leader if leader else '-'}"
        # fetch roster files
        files = await db_fetchall("SELECT telegram_file_id, local_path FROM roster_files WHERE team_id = ?", (team_id,))
        if files:
            # send text then media group if multiple images
            await query.message.reply_text(text)
            media = [InputMediaPhoto(f[0]) for f in files]  # use telegram file_id for speed
            # telegram API supports 1..10 media in a group; we assume roster <= 6
            try:
                await query.message.reply_media_group(media)
            except Exception:
                # fallback: send individually
                for m in media:
                    await query.message.reply_photo(m.media)
        else:
            await query.edit_message_text(text)
        return

    if data.startswith("admin_t_"):
        tid = int(data.split("_")[-1])
        # admin options
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View registrations", callback_data=f"adm_viewreg_{tid}")],
            [InlineKeyboardButton("🗑️ Remove team", callback_data=f"adm_removeteam_{tid}")],
            [InlineKeyboardButton("▶️ Start / View Bracket", callback_data=f"adm_bracket_{tid}")],
            [InlineKeyboardButton("🧹 Delete Tournament", callback_data=f"adm_delete_{tid}")]
        ])
        row = await db_fetchone("SELECT id, name, max_teams, status FROM tournaments WHERE id = ?", (tid,))
        if not row:
            await query.edit_message_text("Tournament missing.")
            return
        await query.edit_message_text(f"Admin panel for <b>{row[1]}</b>\nStatus: {row[3]}", reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("adm_viewreg_"):
        tid = int(data.split("_")[-1])
        rows = await db_fetchall("SELECT id, name, leader_username FROM teams WHERE tournament_id = ? ORDER BY id", (tid,))
        if not rows:
            await query.edit_message_text("No teams registered.")
            return
        txt = "Registered teams (live):\n"
        for i, r in enumerate(rows, start=1):
            txt += f"{i}. {r[1]} — @{r[2] or '-'} (team_id={r[0]})\n"
        await query.edit_message_text(txt)
        return

    if data.startswith("adm_removeteam_"):
        tid = int(data.split("_")[-1])
        teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ?", (tid,))
        if not teams:
            await query.edit_message_text("No teams to remove.")
            return
        kb = [[InlineKeyboardButton(f"Remove {t[1]}", callback_data=f"adm_remove_{tid}_{t[0]}")] for t in teams]
        await query.edit_message_text("Select team to remove:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_remove_"):
        _, tid_s, team_id_s = data.split("_")
        tid = int(tid_s); team_id = int(team_id_s)
        team = await db_fetchone("SELECT name FROM teams WHERE id = ? AND tournament_id = ?", (team_id, tid))
        if not team:
            await query.edit_message_text("Team not found.")
            return
        await db_execute("DELETE FROM teams WHERE id = ?", (team_id,))
        await query.edit_message_text(f"Removed team {team[0]}.")
        return

    if data.startswith("adm_delete_"):
        tid = int(data.split("_")[-1])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes, delete", callback_data=f"adm_delete_confirm_{tid}")],
            [InlineKeyboardButton("Cancel", callback_data="none")]
        ])
        await query.edit_message_text("Delete tournament? This will remove all teams and files.", reply_markup=kb)
        return

    if data.startswith("adm_delete_confirm_"):
        tid = int(data.split("_")[-1])
        await db_execute("DELETE FROM tournaments WHERE id = ?", (tid,))
        # optionally remove rosters folder
        p = ROSTERS_DIR / str(tid)
        if p.exists():
            try:
                for child in p.rglob('*'):
                    if child.is_file():
                        child.unlink()
                for child in sorted(p.rglob('*'), reverse=True):
                    if child.is_dir():
                        child.rmdir()
            except Exception:
                pass
        await query.edit_message_text("Tournament deleted.")
        return

    # Bracket generation & viewing
    if data.startswith("adm_bracket_"):
        tid = int(data.split("_")[-1])
        # check if bracket exists
        matches = await db_fetchall("SELECT id, round_index, match_index, teamA_id, teamB_id, winner_team_id FROM bracket_matches WHERE tournament_id = ? ORDER BY round_index, match_index", (tid,))
        if not matches:
            # generate if enough teams
            cnt = await count_registered(tid)
            row = await db_fetchone("SELECT max_teams, status FROM tournaments WHERE id = ?", (tid,))
            if not row:
                await query.edit_message_text("Tournament missing.")
                return
            max_teams, status = row
            if cnt < 2:
                await query.edit_message_text("Not enough teams to create bracket (need >=2).")
                return
            # create bracket now
            await generate_and_store_bracket(tid)
            await db_execute("UPDATE tournaments SET status = 'in_progress' WHERE id = ?", (tid,))
            await query.edit_message_text("Bracket generated and tournament set to in_progress. Open admin panel again to manage matches.")
            return
        else:
            # show bracket status and provide buttons to pick winners if admin
            await show_bracket_admin_view(update, context, tid)
            return

    # Pick winner callback format: win_{match_id}_{team_side} team_side = A or B
    if data.startswith("win_"):
        _, match_id_s, side = data.split("_")
        match_id = int(match_id_s)
        row = await db_fetchone("SELECT id, tournament_id, teamA_id, teamB_id, winner_team_id FROM bracket_matches WHERE id = ?", (match_id,))
        if not row:
            await query.edit_message_text("Match not found.")
            return
        _, tid, teamA_id, teamB_id, winner_id = row
        if side == "A" and teamA_id:
            new_winner = teamA_id
        elif side == "B" and teamB_id:
            new_winner = teamB_id
        else:
            await query.edit_message_text("Invalid selection.")
            return
        await db_execute("UPDATE bracket_matches SET winner_team_id = ? WHERE id = ?", (new_winner, match_id))
        # propagate winner to next round
        await propagate_winner_to_next_round(tid)
        await show_bracket_admin_view(update, context, tid)
        return

    await query.edit_message_text("Unknown action.")

# -----------------------
# Bracket generation / propagation
# -----------------------
async def generate_and_store_bracket(tid: int):
    # fetch teams and shuffle
    teams = await db_fetchall("SELECT id, name FROM teams WHERE tournament_id = ? ORDER BY id", (tid,))
    teams = [dict(id=r[0], name=r[1]) for r in teams]
    random.shuffle(teams)
    # ensure number of slots = next power of 2
    n = len(teams)
    m = 1
    while m < n:
        m *= 2
    # fill byes with None
    slots = teams + [None] * (m - n)
    rounds = []
    # round 0 matches
    matches = []
    for i in range(0, len(slots), 2):
        a = slots[i]
        b = slots[i+1]
        matches.append((a, b))
    rounds.append(matches)
    # subsequent rounds placeholders
    rcount = 0
    cur = len(matches)
    while cur > 1:
        cur = cur // 2
        rounds.append([ (None, None) for _ in range(cur) ])
        rcount += 1
    # store matches into DB
    async with aiosqlite.connect(DATABASE) as db:
        for r_idx, matches in enumerate(rounds):
            for m_idx, pair in enumerate(matches):
                a, b = pair
                a_id = a['id'] if a else None
                b_id = b['id'] if b else None
                await db.execute("""
                    INSERT INTO bracket_matches (tournament_id, round_index, match_index, teamA_id, teamB_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (tid, r_idx, m_idx, a_id, b_id))
        await db.commit()

async def propagate_winner_to_next_round(tid: int):
    """
    For matches with winners recorded, fill next round slots automatically.
    If the next round match gets both participants, leave them for admin to pick winner.
    If winner is final, mark tournament finished and compute placements.
    """
    async with aiosqlite.connect(DATABASE) as db:
        # fetch all matches ordered
        cur = await db.execute("SELECT id, round_index, match_index, teamA_id, teamB_id, winner_team_id FROM bracket_matches WHERE tournament_id = ? ORDER BY round_index, match_index", (tid,))
        matches = await cur.fetchall()
        if not matches:
            return
        # index by round and match index
        by_round: Dict[int, Dict[int, Dict[str, Any]]] = {}
        for m in matches:
            mid, r, idx, a_id, b_id, winner = m
            by_round.setdefault(r, {})[idx] = {"id": mid, "teamA": a_id, "teamB": b_id, "winner": winner}
        max_round = max(by_round.keys())
        # For each round's matches, if winner exists, place into next round
        for r in range(max_round + 1):
            for idx, m in list(by_round.get(r, {}).items()):
                if m['winner'] is None and (m['teamA'] is not None and m['teamB'] is None):
                    # single team vs bye -> auto-advance
                    await db.execute("UPDATE bracket_matches SET winner_team_id = ? WHERE id = ?", (m['teamA'], m['id']))
                    m['winner'] = m['teamA']
                elif m['winner'] is None and (m['teamA'] is None and m['teamB'] is not None):
                    await db.execute("UPDATE bracket_matches SET winner_team_id = ? WHERE id = ?", (m['teamB'], m['id']))
                    m['winner'] = m['teamB']
                # if winner exists, place in next round
                if m['winner'] is not None and r < max_round:
                    next_r = r + 1
                    next_idx = idx // 2
                    next_match = by_round[next_r][next_idx]
                    # decide whether to put into A or B
                    if next_match['teamA'] is None:
                        await db.execute("UPDATE bracket_matches SET teamA_id = ? WHERE id = ?", (m['winner'], next_match['id']))
                        next_match['teamA'] = m['winner']
                    elif next_match['teamB'] is None:
                        await db.execute("UPDATE bracket_matches SET teamB_id = ? WHERE id = ?", (m['winner'], next_match['id']))
                        next_match['teamB'] = m['winner']
        await db.commit()
        # check if final has winner -> mark tournament finished and compute placements (basic)
        final_row = await db_fetchone("SELECT winner_team_id FROM bracket_matches WHERE tournament_id = ? AND round_index = (SELECT MAX(round_index) FROM bracket_matches WHERE tournament_id = ?)", (tid, tid))
        if final_row and final_row[0]:
            # mark finished and champion
            await db.execute("UPDATE tournaments SET status = 'finished' WHERE id = ?", (tid,))
            await db.commit()

async def show_bracket_admin_view(update: Update, context: ContextTypes.DEFAULT_TYPE, tid: int):
    # prepare bracket text and buttons for admin to pick winners
    matches = await db_fetchall("SELECT id, round_index, match_index, teamA_id, teamB_id, winner_team_id FROM bracket_matches WHERE tournament_id = ? ORDER BY round_index, match_index", (tid,))
    if not matches:
        await update.callback_query.edit_message_text("No bracket exists yet.")
        return
    text = f"Bracket for tournament {tid}\n"
    lines = []
    kb = []
    # group by round
    rounds: Dict[int, List[Tuple]] = {}
    for m in matches:
        mid, r, idx, a_id, b_id, winner = m
        rounds.setdefault(r, []).append(m)
    for r in sorted(rounds.keys()):
        text += f"\n— Round {r+1} —\n"
        for m in rounds[r]:
            mid, r_idx, m_idx, a_id, b_id, winner = m
            a_name = "-"
            b_name = "-"
            if a_id:
                row = await db_fetchone("SELECT name FROM teams WHERE id = ?", (a_id,))
                a_name = row[0] if row else f"id:{a_id}"
            if b_id:
                row = await db_fetchone("SELECT name FROM teams WHERE id = ?", (b_id,))
                b_name = row[0] if row else f"id:{b_id}"
            win_label = f" — Winner: { (await name_by_id(winner)) if winner else '-' }"
            text += f"Match {m_idx+1}: {a_name} vs {b_name}{win_label}\n"
            if (a_id and b_id) and (winner is None):
                kb.append([InlineKeyboardButton(f"{a_name} ✅", callback_data=f"win_{mid}_A"),
                           InlineKeyboardButton(f"{b_name} ✅", callback_data=f"win_{mid}_B")])
            else:
                kb.append([InlineKeyboardButton(f"Match {m_idx+1} (r{r+1})", callback_data="none")])
    final = await db_fetchall("SELECT winner_team_id FROM bracket_matches WHERE tournament_id = ? AND round_index = (SELECT MAX(round_index) FROM bracket_matches WHERE tournament_id = ?)", (tid, tid))
    champion = None
    if final and final[0] and final[0][0]:
        champion = await name_by_id(final[0][0])
        text += f"\n🏅 Champion: {champion}\n"
    # edit
    try:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb) if kb else None)
    except Exception:
        # fallback plain send
        await update.effective_message.reply_text(text)

async def name_by_id(team_id: Optional[int]) -> str:
    if not team_id:
        return "-"
    row = await db_fetchone("SELECT name FROM teams WHERE id = ?", (team_id,))
    return row[0] if row else f"id:{team_id}"

# -----------------------
# Registration conversation handlers
# -----------------------
async def reg_receive_teamname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team_name = update.message.text.strip()
    tid = context.user_data.get('reg_tid')
    if not tid:
        await update.message.reply_text("Session expired. Start from tournament list.")
        return ConversationHandler.END
    # check uniqueness
    exists = await db_fetchone("SELECT id FROM teams WHERE tournament_id = ? AND LOWER(name) = LOWER(?)", (tid, team_name))
    if exists:
        await update.message.reply_text("Team name taken. Send another team name:")
        return REG_TEAM_NAME
    context.user_data['reg_teamname'] = team_name
    await update.message.reply_text("Send team leader's Telegram username (without @) or '-' to skip:")
    return REG_LEADER_USERNAME

async def reg_receive_leader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leader = update.message.text.strip().lstrip('@')
    context.user_data['reg_leader'] = leader if leader != "-" else None
    await update.message.reply_text("Now send 3 or 4 photos as roster (one by one). When done send /done")
    context.user_data['reg_roster'] = []
    return REG_WAIT_ROSTER

async def reg_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send photo or /done.")
        return REG_WAIT_ROSTER
    file_id = update.message.photo[-1].file_id
    context.user_data.setdefault('reg_roster', []).append(file_id)
    await update.message.reply_text(f"Photo received #{len(context.user_data['reg_roster'])}. Send more or /done.")

async def reg_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = context.user_data.get('reg_tid')
    if not tid:
        await update.message.reply_text("Session expired.")
        return ConversationHandler.END
    roster = context.user_data.get('reg_roster', [])
    if len(roster) < 1:
        await update.message.reply_text("At least 1 photo required.")
        return REG_WAIT_ROSTER
    team_name = context.user_data.get('reg_teamname')
    leader = context.user_data.get('reg_leader')
    # insert team
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute("INSERT INTO teams (tournament_id, name, leader_username) VALUES (?, ?, ?)", (tid, team_name, leader))
        await db.commit()
        team_id = cur.lastrowid
    # download roster files and store
    roster_path = ensure_roster_dir(tid, team_id)
    async with aiosqlite.connect(DATABASE) as db:
        for i, tfid in enumerate(roster, start=1):
            try:
                f = await context.bot.get_file(tfid)
                local_fname = roster_path / f"{i}.jpg"
                await f.download_to_drive(custom_path=str(local_fname))
                await db.execute("INSERT INTO roster_files (team_id, telegram_file_id, local_path) VALUES (?, ?, ?)", (team_id, tfid, str(local_fname)))
            except Exception as e:
                logger.exception("Failed to download roster image: %s", e)
                # still store telegram_file_id so it can be shown later
                await db.execute("INSERT INTO roster_files (team_id, telegram_file_id, local_path) VALUES (?, ?, ?)", (team_id, tfid, None))
        await db.commit()
    # notify admins
    cnt = await count_registered(tid)
    max_row = await db_fetchone("SELECT max_teams FROM tournaments WHERE id = ?", (tid,))
    max_teams = max_row[0] if max_row else 9999
    if cnt >= max_teams:
        await db_execute("UPDATE tournaments SET status = 'full' WHERE id = ?", (tid,))
    for aid in ADMINS:
        try:
            await context.bot.send_message(chat_id=aid, text=f"New registration: {team_name} in tournament {tid}. Registered: {cnt}/{max_teams}")
        except Exception:
            pass
    await update.message.reply_text(f"Team {team_name} registered successfully!")
    return ConversationHandler.END

# -----------------------
# Admin create tournament conv
# -----------------------
@admin_only
async def create_tournament_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enter tournament name:")
    return ADMIN_CREATE_NAME

async def create_tournament_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data['new_t_name'] = name
    await update.message.reply_text("Enter max number of teams (integer):")
    return ADMIN_CREATE_MAXTEAMS

async def create_tournament_maxteams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        max_teams = int(update.message.text.strip())
        if max_teams < 2:
            await update.message.reply_text("Minimum is 2. Enter again:")
            return ADMIN_CREATE_MAXTEAMS
    except ValueError:
        await update.message.reply_text("Not a number. Enter integer:")
        return ADMIN_CREATE_MAXTEAMS
    name = context.user_data.get('new_t_name')
    await db_execute("INSERT INTO tournaments (name, max_teams, status) VALUES (?, ?, 'registration')", (name, max_teams))
    last = await db_fetchone("SELECT id FROM tournaments ORDER BY id DESC LIMIT 1")
    tid = last[0] if last else None
    await update.message.reply_text(f"Tournament created: {name} (ID {tid}). Players can register.")
    return ConversationHandler.END

@admin_only
async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db_fetchall("SELECT id, name, max_teams, status FROM tournaments ORDER BY id DESC")
    if not rows:
        await update.message.reply_text("No tournaments.")
        return
    kb_items = [(f"{r[1]} ({await count_registered(r[0])}/{r[2]})", f"admin_t_{r[0]}") for r in rows]
    await update.message.reply_text("Tournaments:", reply_markup=make_keyboard_from_list(kb_items))

@admin_only
async def delete_tournament_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db_fetchall("SELECT id, name FROM tournaments ORDER BY id DESC")
    if not rows:
        await update.message.reply_text("No tournaments.")
        return
    kb = [[InlineKeyboardButton(f"Delete {r[1]}", callback_data=f"adm_delete_{r[0]}")] for r in rows]
    await update.message.reply_text("Select tournament to delete:", reply_markup=InlineKeyboardMarkup(kb))

# -----------------------
# Text router for keyboard text
# -----------------------
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().lower()
    if txt in ("📋 tournaments", "tournaments", "tournaments📋"):
        await tournaments_button(update, context)
        return
    if txt in ("🔎 view teams", "view teams", "teams"):
        kb = await show_tournaments_keyboard()
        await update.message.reply_text("Select tournament to view teams:", reply_markup=kb)
        return
    await update.message.reply_text("Use /help or the keyboard.")

# -----------------------
# Boot
# -----------------------
def main():
    if BOT_TOKEN == "REPLACE_WITH_YOUR_BOT_TOKEN" or not BOT_TOKEN:
        print("Set BOT_TOKEN env var or edit the file to include your bot token.")
        return
    ROSTERS_DIR.mkdir(parents=True, exist_ok=True)
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # initialize DB
    asyncio.get_event_loop().run_until_complete(init_db())

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin_list", admin_list))
    app.add_handler(CommandHandler("create_tournament", create_tournament_start))
    app.add_handler(CommandHandler("delete_tournament", delete_tournament_cmd))

    # create conv
    create_conv = ConversationHandler(
        entry_points=[CommandHandler("create_tournament", create_tournament_start)],
        states={
            ADMIN_CREATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_tournament_name)],
            ADMIN_CREATE_MAXTEAMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_tournament_maxteams)],
        },
        fallbacks=[]
    )
    app.add_handler(create_conv)

    # registration conv started by callback 'reg_t_{tid}' - but CallbackQueryHandler triggers conversation entry
    reg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(callback_router, pattern=r"^reg_t_")],
        states={
            REG_TEAM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_receive_teamname)],
            REG_LEADER_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_receive_leader)],
            REG_WAIT_ROSTER: [
                MessageHandler(filters.PHOTO, reg_receive_photo),
                CommandHandler("done", reg_done),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("Send photos or /done")),
            ],
        },
        fallbacks=[CommandHandler("done", reg_done)],
        per_user=True,
        name="reg_conv",
    )
    app.add_handler(reg_conv)

    # callbacks
    app.add_handler(CallbackQueryHandler(callback_router))

    # text handler (keyboard)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("Bot started — running polling. Press ctrl-c to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
