# main.py — NK Quiz Bot (Dual DB: Supabase Postgres or local SQLite)
# Telethon 1.34.0 + Flask keep-alive + aiosqlite/asyncpg
# Features:
# - Dual-mode DB: use POSTGRES if DATABASE_URL provided, else fallback to SQLite
# - Same quiz features: per-group scheduler, polls (Telethon), leaderboard, admin checks
# - Robust PM vs Group replies, owner-only commands, broadcast flow, inline add-to-group button
# - Uses async DB drivers: asyncpg (Postgres) and aiosqlite (SQLite)

import os
import asyncio
import random
import json
from datetime import datetime
from typing import Optional, Tuple

from flask import Flask
import multiprocessing
import aiohttp

from telethon import TelegramClient, events, Button
from telethon.tl import functions, types
from telethon.utils import get_peer_id

# Optional DB drivers (ensure these are in requirements)
# asyncpg for Postgres (Supabase), aiosqlite for SQLite async
try:
    import asyncpg
except Exception:
    asyncpg = None
try:
    import aiosqlite
except Exception:
    aiosqlite = None

# ---------------- CONFIG ----------------
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "" )
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

DB_URL = os.environ.get("DATABASE_URL")  # Supabase connection string
DB_PATH = os.environ.get("DB_PATH", "quiz_bot.db")  # fallback file path for sqlite
KEEPALIVE_URL = os.environ.get("KEEPALIVE_URL", "")
KEEPALIVE_INTERVAL = int(os.environ.get("KEEPALIVE_INTERVAL", 240))

USE_POSTGRES = bool(DB_URL)
if USE_POSTGRES and asyncpg is None:
    raise RuntimeError("DATABASE_URL set but asyncpg not installed. Add asyncpg to requirements.")
if (not USE_POSTGRES) and aiosqlite is None:
    # aiosqlite useful for async sqlite; if missing we'll try to import sync sqlite later
    raise RuntimeError("aiosqlite not installed. Add aiosqlite to requirements or set DATABASE_URL.")

# ---------------- TELEGRAM CLIENT ----------------
client = TelegramClient("quiz_bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
BOT_USERNAME = None

# Scheduler tasks
_group_tasks: dict[int, asyncio.Task] = {}

# ---------------- DB: schema ----------------
CREATE_TABLES = {
    "groups": (
        "CREATE TABLE IF NOT EXISTS groups ("
        "group_id BIGINT PRIMARY KEY,"
        "group_name TEXT,"
        "quiz_active BOOLEAN DEFAULT TRUE,"
        "interval_minutes INTEGER DEFAULT 30,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "users": (
        "CREATE TABLE IF NOT EXISTS users ("
        "user_id BIGINT PRIMARY KEY,"
        "username TEXT,"
        "first_name TEXT,"
        "started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "players": (
        "CREATE TABLE IF NOT EXISTS players ("
        "id SERIAL PRIMARY KEY,"
        "user_id BIGINT,"
        "group_id BIGINT,"
        "username TEXT,"
        "first_name TEXT,"
        "score INTEGER DEFAULT 0,"
        "correct_answers INTEGER DEFAULT 0,"
        "wrong_answers INTEGER DEFAULT 0,"
        "current_streak INTEGER DEFAULT 0,"
        "max_streak INTEGER DEFAULT 0,"
        "last_answer_time TIMESTAMP,"
        "UNIQUE(user_id, group_id)"
        ")"
    ),
    "questions": (
        "CREATE TABLE IF NOT EXISTS questions ("
        "id SERIAL PRIMARY KEY,"
        "question TEXT NOT NULL,"
        "option_a TEXT NOT NULL,"
        "option_b TEXT NOT NULL,"
        "option_c TEXT NOT NULL,"
        "option_d TEXT NOT NULL,"
        "correct_answer INTEGER NOT NULL,"
        "category TEXT DEFAULT 'General',"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ),
    "question_usage": (
        "CREATE TABLE IF NOT EXISTS question_usage ("
        "group_id BIGINT,"
        "question_id BIGINT,"
        "used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        "PRIMARY KEY (group_id, question_id)"
        ")"
    ),
    "active_polls": (
        "CREATE TABLE IF NOT EXISTS active_polls ("
        "group_id BIGINT PRIMARY KEY,"
        "poll_id TEXT,"
        "question_id BIGINT,"
        "message_id BIGINT,"
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
}

# ---------------- DB helpers (async) ----------------
async def init_db():
    # Create tables and add sample questions if empty
    if USE_POSTGRES:
        conn = await asyncpg.connect(DB_URL)
        for q in CREATE_TABLES.values():
            await conn.execute(q)
        # sample questions
        count = await conn.fetchval("SELECT COUNT(*) FROM questions")
        if count == 0:
            await conn.executemany(
                "INSERT INTO questions (question, option_a, option_b, option_c, option_d, correct_answer, category) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                [
                    ("What is the capital of France?","London","Berlin","Paris","Madrid",2,"Geography"),
                    ("Which planet is called Red Planet?","Venus","Mars","Jupiter","Saturn",1,"Science"),
                    ("Square root of 144?","10","11","12","13",2,"Math")
                ]
            )
        await conn.close()
    else:
        # sqlite via aiosqlite
        async with aiosqlite.connect(DB_PATH) as db:
            for q in CREATE_TABLES.values():
                await db.execute(q)
            await db.commit()
            cur = await db.execute("SELECT COUNT(*) FROM questions")
            row = await cur.fetchone()
            if row and row[0] == 0:
                await db.executemany(
                    "INSERT INTO questions (question, option_a, option_b, option_c, option_d, correct_answer, category) VALUES (?,?,?,?,?,?,?)",
                    [
                        ("What is the capital of France?","London","Berlin","Paris","Madrid",2,"Geography"),
                        ("Which planet is called Red Planet?","Venus","Mars","Jupiter","Saturn",1,"Science"),
                        ("Square root of 144?","10","11","12","13",2,"Math")
                    ]
                )
                await db.commit()

# Generic query helpers
async def db_fetch(query: str, *params):
    if USE_POSTGRES:
        conn = await asyncpg.connect(DB_URL)
        rows = await conn.fetch(query, *params)
        await conn.close()
        return rows
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
            return rows

async def db_fetchrow(query: str, *params):
    if USE_POSTGRES:
        conn = await asyncpg.connect(DB_URL)
        row = await conn.fetchrow(query, *params)
        await conn.close()
        return row
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(query, params)
            row = await cur.fetchone()
            return row

async def db_execute(query: str, *params):
    if USE_POSTGRES:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute(query, *params)
        await conn.close()
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(query, params)
            await db.commit()

# Convenience wrappers used by bot logic
async def add_group(group_id: int, group_name: str):
    if USE_POSTGRES:
        await db_execute("INSERT INTO groups (group_id, group_name) VALUES ($1,$2) ON CONFLICT (group_id) DO NOTHING", group_id, group_name)
    else:
        await db_execute("INSERT OR IGNORE INTO groups (group_id, group_name) VALUES (?,?)", group_id, group_name)

async def get_group_settings(group_id: int) -> Tuple[bool,int]:
    row = await db_fetchrow("SELECT quiz_active, interval_minutes FROM groups WHERE group_id=$1" if USE_POSTGRES else "SELECT quiz_active, interval_minutes FROM groups WHERE group_id=?", group_id)
    if not row:
        return True, 30
    # row types differ
    return bool(row[0]), int(row[1])

async def update_group_settings(group_id: int, **kwargs):
    # simple approach: update each key
    for k,v in kwargs.items():
        if USE_POSTGRES:
            await db_execute(f"UPDATE groups SET {k}=$1 WHERE group_id=$2", v, group_id)
        else:
            await db_execute(f"UPDATE groups SET {k}=? WHERE group_id=?", v, group_id)

async def add_or_update_player(user_id:int, group_id:int, username:Optional[str], first_name:str):
    if USE_POSTGRES:
        await db_execute(
            "INSERT INTO players (user_id, group_id, username, first_name) VALUES ($1,$2,$3,$4) ON CONFLICT (user_id, group_id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name",
            user_id, group_id, username, first_name
        )
    else:
        await db_execute(
            "INSERT INTO players (user_id, group_id, username, first_name) VALUES (?,?,?,?) ON CONFLICT(user_id, group_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name" if False else "INSERT OR REPLACE INTO players (user_id, group_id, username, first_name) VALUES (?,?,?,?)",
            user_id, group_id, username, first_name
        )

async def update_player_score(user_id:int, group_id:int, points:int, is_correct:bool):
    if is_correct:
        if USE_POSTGRES:
            await db_execute(
                "UPDATE players SET score = score + $1, correct_answers = correct_answers + 1, current_streak = current_streak + 1, max_streak = GREATEST(max_streak, current_streak + 1), last_answer_time = CURRENT_TIMESTAMP WHERE user_id=$2 AND group_id=$3",
                points, user_id, group_id
            )
        else:
            await db_execute(
                "UPDATE players SET score = score + ?, correct_answers = correct_answers + 1, current_streak = current_streak + 1, max_streak = CASE WHEN max_streak < current_streak+1 THEN current_streak+1 ELSE max_streak END, last_answer_time = CURRENT_TIMESTAMP WHERE user_id=? AND group_id=?",
                points, user_id, group_id
            )
    else:
        if USE_POSTGRES:
            await db_execute("UPDATE players SET score = score + $1, wrong_answers = wrong_answers + 1, current_streak = 0, last_answer_time = CURRENT_TIMESTAMP WHERE user_id=$2 AND group_id=$3", points, user_id, group_id)
        else:
            await db_execute("UPDATE players SET score = score + ?, wrong_answers = wrong_answers + 1, current_streak = 0, last_answer_time = CURRENT_TIMESTAMP WHERE user_id=? AND group_id=?", points, user_id, group_id)

async def get_next_question(group_id:int):
    # choose unused question for group
    if USE_POSTGRES:
        rows = await db_fetch(
            "SELECT q.* FROM questions q LEFT JOIN question_usage qu ON q.id=qu.question_id AND qu.group_id=$1 WHERE qu.question_id IS NULL ORDER BY RANDOM() LIMIT 1", group_id
        )
        if not rows:
            await db_execute("DELETE FROM question_usage WHERE group_id=$1", group_id)
            rows = await db_fetch("SELECT * FROM questions ORDER BY RANDOM() LIMIT 1")
        q = rows[0] if rows else None
        if q:
            await db_execute("INSERT INTO question_usage (group_id, question_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", group_id, q[0])
        return q
    else:
        rows = await db_fetch("SELECT q.* FROM questions q LEFT JOIN question_usage qu ON q.id=qu.question_id AND qu.group_id=? WHERE qu.question_id IS NULL ORDER BY RANDOM() LIMIT 1", group_id)
        if not rows:
            await db_execute("DELETE FROM question_usage WHERE group_id=?", group_id)
            rows = await db_fetch("SELECT * FROM questions ORDER BY RANDOM() LIMIT 1")
        q = rows[0] if rows else None
        if q:
            await db_execute("INSERT OR REPLACE INTO question_usage (group_id, question_id) VALUES (?,?)", group_id, q[0])
        return q

async def store_active_poll(group_id:int, poll_id:str, question_id:int, message_id:int):
    if USE_POSTGRES:
        await db_execute("INSERT INTO active_polls (group_id, poll_id, question_id, message_id) VALUES ($1,$2,$3,$4) ON CONFLICT (group_id) DO UPDATE SET poll_id=EXCLUDED.poll_id, question_id=EXCLUDED.question_id, message_id=EXCLUDED.message_id", group_id, poll_id, question_id, message_id)
    else:
        await db_execute("INSERT OR REPLACE INTO active_polls (group_id, poll_id, question_id, message_id) VALUES (?,?,?,?)", group_id, poll_id, question_id, message_id)

async def get_active_poll(group_id:int):
    row = await db_fetchrow("SELECT poll_id, question_id, message_id FROM active_polls WHERE group_id=$1" if USE_POSTGRES else "SELECT poll_id, question_id, message_id FROM active_polls WHERE group_id=?", group_id)
    return row

async def remove_active_poll(group_id:int):
    if USE_POSTGRES:
        await db_execute("DELETE FROM active_polls WHERE group_id=$1", group_id)
    else:
        await db_execute("DELETE FROM active_polls WHERE group_id=?", group_id)

async def get_group_leaderboard(group_id:int, limit:int=10):
    rows = await db_fetch("SELECT user_id, username, first_name, score, correct_answers, wrong_answers, current_streak, max_streak FROM players WHERE group_id=$1 ORDER BY score DESC, correct_answers DESC LIMIT $2" if USE_POSTGRES else "SELECT user_id, username, first_name, score, correct_answers, wrong_answers, current_streak, max_streak FROM players WHERE group_id=? ORDER BY score DESC, correct_answers DESC LIMIT ?", group_id, limit)
    return rows

async def get_all_users():
    rows = await db_fetch("SELECT DISTINCT user_id FROM users")
    return [r[0] for r in rows]

async def get_all_groups():
    rows = await db_fetch("SELECT group_id FROM groups")
    return [r[0] for r in rows]

# ---------------- Utilities ----------------
async def is_owner(event) -> bool:
    return event.sender_id == OWNER_ID

async def is_admin(event) -> bool:
    if not event.is_group:
        return False
    try:
        perms = await client.get_permissions(event.chat_id, event.sender_id)
        if getattr(perms, 'is_admin', False) or getattr(perms, 'is_creator', False):
            return True
    except Exception:
        pass
    # Anonymous admin heuristics
    msg = event.message
    if getattr(msg, 'post_author', None) is not None:
        return True
    if msg and msg.sender_id and event.chat_id and msg.sender_id == event.chat_id:
        return True
    return False

def html_escape(s: str) -> str:
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))

# ---------------- Quiz sending (Telethon poll API) ----------------
async def send_quiz_question(group_id: int):
    try:
        active, interval = await get_group_settings(group_id)
        if not active:
            return
        ap = await get_active_poll(group_id)
        if ap:
            try:
                await client.delete_messages(group_id, ap[2])
            except Exception:
                pass
            await remove_active_poll(group_id)
        q = await get_next_question(group_id)
        if not q:
            print(f"[WARN] No questions for {group_id}")
            return
        # q columns: id, question, option_a, option_b, option_c, option_d, correct_answer, category ...
        qid = q[0]; text = q[1]; a = q[2]; b = q[3]; c = q[4]; d = q[5]; correct = int(q[6]); category = q[7]

        answers = [
            types.PollAnswer(text=a, option=b"A"),
            types.PollAnswer(text=b, option=b"B"),
            types.PollAnswer(text=c, option=b"C"),
            types.PollAnswer(text=d, option=b"D"),
        ]
        correct_byte = bytes([65 + correct])
        poll = types.Poll(
            id=0,
            question=text,
            answers=answers,
            multiple_choice=False,
            quiz=True,
            public_voters=False,
            close_date=None,
            closed=False,
            correct_answers=[correct_byte],
            solution=None,
            solution_entities=None,
        )
        caption = f"📚 <b>Quiz Time!</b>\n\n<b>Category:</b> {html_escape(category)}\n\n{html_escape(text)}"
        updates = await client(functions.messages.SendMediaRequest(peer=group_id, media=types.InputMediaPoll(poll=poll), message=caption, random_id=random.getrandbits(64), parse_mode='html'))
        # extract message and poll id
        message_id = None; poll_id = None
        for u in updates.updates:
            try:
                m = u.message
                message_id = m.id
                poll_id = m.media.poll.id
                break
            except Exception:
                continue
        if message_id and poll_id:
            await store_active_poll(group_id, str(poll_id), qid, message_id)
        print(f"[OK] Quiz sent to {group_id} msg={message_id}")
    except Exception as e:
        print(f"[ERR] send_quiz_question {group_id}: {e}")

async def schedule_quiz_for_group(group_id: int):
    while True:
        try:
            active, interval = await get_group_settings(group_id)
            if active:
                await send_quiz_question(group_id)
            await asyncio.sleep(max(60, int(interval) * 60))
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[ERR] scheduler {group_id}: {e}")
            await asyncio.sleep(300)

def start_group_quiz_schedule(group_id: int):
    if group_id in _group_tasks:
        return
    _group_tasks[group_id] = asyncio.create_task(schedule_quiz_for_group(group_id))

def stop_group_quiz_schedule(group_id: int):
    t = _group_tasks.pop(group_id, None)
    if t:
        t.cancel()

# ---------------- Event Handlers ----------------
@client.on(events.NewMessage(pattern=r"/start"))
async def start_handler(event):
    global BOT_USERNAME
    me = await client.get_me()
    BOT_USERNAME = me.username
    if event.is_group:
        gid = event.chat_id; gname = getattr(event.chat, 'title', 'Group')
        await add_group(gid, gname)
        start_group_quiz_schedule(gid)
        await event.respond(("🤖 <b>Quiz Bot Enabled!</b>\n\n"
                             "• I will send quiz polls periodically.\n"
                             "• Default interval: <b>30 minutes</b>. Use <code>/setinterval &lt;min&gt;</code> to change.\n"
                             "• Scoring: <b>+4</b> correct, <b>-1</b> wrong.\n\n"
                             "<b>Admin commands</b> (creator/admins):\n"
                             "<code>/quizstart</code> – resume sending\n"
                             "<code>/quizstop</code> – stop sending\n"
                             "<code>/quiznow</code> – send a question now\n"
                             "<code>/setinterval 5..1440</code> – set minutes\n"
                             "<code>/leaderboard</code> – group top 10\n"
                             "<code>/resetboard</code> – clear scores\n"
                            ), parse_mode='html')
    else:
        btns = [[Button.url("➕ Add me to a group", f"https://t.me/{BOT_USERNAME}?startgroup=true")],[Button.inline("📖 Help", data=b"help")]]
        user = await event.get_sender()
        # ensure user stored
        if USE_POSTGRES:
            await db_execute("INSERT INTO users (user_id, username, first_name) VALUES ($1,$2,$3) ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name", user.id, user.username, user.first_name or "")
        else:
            await db_execute("INSERT OR REPLACE INTO users (user_id, username, first_name) VALUES (?,?,?)", user.id, user.username, user.first_name or "")
        await event.respond(("👋 Hi! I run timed quiz polls in groups.\nAdd me to a group and send <code>/start</code> there.\n\nOwner‑only (PM) utilities: /addquestion, /newq, /deleteallq, /questioncount, /broadcast"), buttons=btns, parse_mode='html')

@client.on(events.CallbackQuery(data=b"help"))
async def pm_help_cb(event):
    if not event.is_private:
        await event.answer("Open in PM", alert=True)
        return
    await event.edit(("<b>Help</b>\n\n"
                      "➕ Add me to a group → press the button above.\n"
                      "In a group, send <code>/start</code> to enable quizzes.\n\n"
                      "<b>Group commands</b> (admins):\n"
                      "• /quizstart, /quizstop, /quiznow\n"
                      "• /setinterval &lt;5..1440&gt;\n"
                      "• /leaderboard, /resetboard\n\n"
                      "<b>Owner (PM)</b>: /addquestion, /newq, /deleteallq, /questioncount, /broadcast <text>"), buttons=[[Button.url("➕ Add to group", f"https://t.me/{(await client.get_me()).username}?startgroup=true")]], parse_mode='html')

# group-only decorator
def group_only(handler):
    async def wrapper(event):
        if not event.is_group:
            await event.respond("⚠️ This command works only in groups. Add me to a group and try there.")
            return
        await handler(event)
    return wrapper

@client.on(events.NewMessage(pattern=r"/quizstop"))
@group_only
async def quiz_stop(event):
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this.")
        return
    gid = event.chat_id
    await update_group_settings(gid, quiz_active=False)
    ap = await get_active_poll(gid)
    if ap:
        try:
            await client.delete_messages(gid, ap[2])
        except Exception:
            pass
        await remove_active_poll(gid)
    await event.respond("🛑 Quiz stopped. Use /quizstart to resume.")

@client.on(events.NewMessage(pattern=r"/quizstart"))
@group_only
async def quiz_start(event):
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this.")
        return
    gid = event.chat_id
    await update_group_settings(gid, quiz_active=True)
    start_group_quiz_schedule(gid)
    await event.respond("✅ Quiz resumed.")

@client.on(events.NewMessage(pattern=r"/quiznow"))
@group_only
async def quiz_now(event):
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this.")
        return
    await send_quiz_question(event.chat_id)

@client.on(events.NewMessage(pattern=r"/setinterval (\d+)"))
@group_only
async def set_interval(event):
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this.")
        return
    try:
        minutes = int(event.pattern_match.group(1))
        if minutes < 5 or minutes > 1440:
            await event.respond("❌ Interval must be between 5 and 1440 minutes.")
            return
        await update_group_settings(event.chat_id, interval_minutes=minutes)
        await event.respond(f"⏰ Interval set to <b>{minutes}</b> minutes.", parse_mode='html')
    except Exception:
        await event.respond("⚠️ Usage: /setinterval 5..1440")

@client.on(events.NewMessage(pattern=r"/leaderboard"))
@group_only
async def leaderboard(event):
    rows = await get_group_leaderboard(event.chat_id)
    if not rows:
        await event.respond("📊 No players yet. Answer a quiz to get on the board!")
        return
    lines = ["🏆 <b>Group Leaderboard</b>\n"]
    medals = ["👑","🥈","🥉"]
    for i, r in enumerate(rows, start=1):
        uid, uname, fname, score, corr, wrong, cur, mx = r
        rank = medals[i-1] if i<=3 else f"{i}."
        disp = f"@{uname}" if uname else (fname or str(uid))
        streak = f" ({cur}🔥)" if cur>0 else ""
        lines.append(f"{rank} <b>{html_escape(disp)}</b>{streak}\n    💯 {score} | ✅ {corr} | ❌ {wrong} | 🔥 Max {mx}")
    await event.respond("\n".join(lines), parse_mode='html')

@client.on(events.NewMessage(pattern=r"/resetboard"))
@group_only
async def reset_board(event):
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this.")
        return
    if USE_POSTGRES:
        await db_execute("UPDATE players SET score=0, correct_answers=0, wrong_answers=0, current_streak=0, max_streak=0 WHERE group_id=$1", event.chat_id)
        await db_execute("DELETE FROM question_usage WHERE group_id=$1", event.chat_id)
    else:
        await db_execute("UPDATE players SET score=0, correct_answers=0, wrong_answers=0, current_streak=0, max_streak=0 WHERE group_id=?", event.chat_id)
        await db_execute("DELETE FROM question_usage WHERE group_id=?", event.chat_id)
    await event.respond("🔄 Leaderboard reset for this group.")

# Owner-only PM decorator
def owner_pm_only(handler):
    async def wrapper(event):
        if not event.is_private:
            await event.respond("⚠️ PM me to use this command.")
            return
        if not await is_owner(event):
            await event.respond("❌ Only the bot owner can use this.")
            return
        await handler(event)
    return wrapper

@client.on(events.NewMessage(pattern=r"/addquestion"))
@owner_pm_only
async def addq_format(event):
    await event.respond(("📝 <b>Add Question</b>\n\nSend: <code>/newq Question?|A|B|C|D|2|Category</code>\nCorrect index: 0=A,1=B,2=C,3=D"), parse_mode='html')

@client.on(events.NewMessage(pattern=r"(?s)/newq (.+)"))
@owner_pm_only
async def addq(event):
    try:
        payload = event.pattern_match.group(1)
        parts = [p.strip() for p in payload.split("|")]
        if len(parts) != 7:
            await event.respond("❌ Wrong format. Use /addquestion for help.")
            return
        q,a,b,c,d,corr,cat = parts
        corr_i = int(corr)
        if corr_i not in (0,1,2,3):
            await event.respond("❌ Correct index must be 0..3")
            return
        if USE_POSTGRES:
            await db_execute("INSERT INTO questions (question, option_a, option_b, option_c, option_d, correct_answer, category) VALUES ($1,$2,$3,$4,$5,$6,$7)", q,a,b,c,d,corr_i,cat)
        else:
            await db_execute("INSERT INTO questions (question, option_a, option_b, option_c, option_d, correct_answer, category) VALUES (?,?,?,?,?,?,?)", q,a,b,c,d,corr_i,cat)
        await event.respond("✅ Question added.")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r"/deleteallq"))
@owner_pm_only
async def delete_all_q(event):
    if USE_POSTGRES:
        await db_execute("DELETE FROM questions")
        await db_execute("DELETE FROM question_usage")
    else:
        await db_execute("DELETE FROM questions")
        await db_execute("DELETE FROM question_usage")
    await event.respond("🗑️ All questions deleted.")

@client.on(events.NewMessage(pattern=r"/questioncount"))
@owner_pm_only
async def qcount(event):
    row = await db_fetchrow("SELECT COUNT(*) FROM questions" if USE_POSTGRES else "SELECT COUNT(*) FROM questions")
    count = int(row[0]) if row else 0
    await event.respond(f"📊 Total questions: {count}")

@client.on(events.NewMessage(pattern=r"(?s)/broadcast (.+)"))
@owner_pm_only
async def broadcast_prep(event):
    msg = event.pattern_match.group(1)
    await event.respond(f"📢 <b>Broadcast preview</b>:\n\n{html_escape(msg)}\n\nReply: <code>users</code> | <code>groups</code> | <code>all</code>", parse_mode='html')

@client.on(events.NewMessage(pattern=r"^(users|groups|all)$"))
async def broadcast_do(event):
    if not event.is_reply or not event.is_private or not await is_owner(event):
        return
    replied = await event.get_reply_message()
    if not replied or "Broadcast preview" not in (replied.text or ""):
        return
    target = event.pattern_match.group(1)
    try:
        text = replied.text.split("\n\n",2)[1]
    except Exception:
        await event.respond("❌ Couldn't parse preview.")
        return
    sent = failed = 0
    if target in ("users","all"):
        for uid in await get_all_users():
            try:
                await client.send_message(uid, text, parse_mode='html')
                sent += 1
                await asyncio.sleep(0.08)
            except Exception:
                failed += 1
    if target in ("groups","all"):
        for gid in await get_all_groups():
            try:
                await client.send_message(gid, text, parse_mode='html')
                sent += 1
                await asyncio.sleep(0.08)
            except Exception:
                failed += 1
    await event.respond(f"✅ Broadcast done. Sent: {sent} | Failed: {failed}")

# Poll vote updates handler
@client.on(events.Raw(types=[types.UpdateMessagePollVote]))
async def on_poll_vote(event_raw):
    try:
        upd = event_raw
        user_id = upd.user_id
        msg_id = upd.msg_id
        peer = upd.peer
        # get peer chat id
        group_id = get_peer_id(peer)
        ap = await get_active_poll(group_id)
        if not ap:
            return
        stored_poll_id, qid, message_id = ap
        if message_id != msg_id:
            return
        try:
            user = await client.get_entity(user_id)
            username = user.username
            first_name = user.first_name or ""
        except Exception:
            username = None; first_name = 'User'
        await add_or_update_player(user_id, group_id, username, first_name)
        row = await db_fetchrow("SELECT correct_answer FROM questions WHERE id=$1" if USE_POSTGRES else "SELECT correct_answer FROM questions WHERE id=?", qid)
        if not row:
            return
        correct_idx = int(row[0])
        correct_byte = bytes([65 + correct_idx])
        selected = None
        if getattr(upd, 'options', None):
            selected = upd.options[0]
        if selected is None:
            return
        is_correct = (selected == correct_byte)
        points = 4 if is_correct else -1
        await update_player_score(user_id, group_id, points, is_correct)
    except Exception as e:
        print(f"[ERR] poll vote: {e}")

# ---------------- Flask keep-alive ----------------
app = Flask(__name__)
@app.get('/')
def root():
    return 'Quiz Bot is running!'
@app.get('/health')
def health():
    return {'ok': True, 'time': datetime.utcnow().isoformat()}

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

async def keep_alive():
    if not KEEPALIVE_URL:
        return
    while True:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(KEEPALIVE_URL) as resp:
                    _ = await resp.text()
            print('[PING] keep-alive sent')
        except Exception as e:
            print(f'[PING] failed: {e}')
        await asyncio.sleep(KEEPALIVE_INTERVAL)

# ---------------- Main ----------------
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    print('[DB] init done')
    multiprocessing.Process(target=run_web, daemon=True).start()
    print('[WEB] flask started')
    # start keepalive
    if KEEPALIVE_URL:
        client.loop.create_task(keep_alive())
    print('[BOT] starting...')
    client.run_until_disconnected()