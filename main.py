from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantsAdmins, UpdateMessagePollVote
import random, asyncio, json, os, re
from dataclasses import dataclass, field
from typing import Optional, Dict, List
import threading
from flask import Flask
from datetime import datetime, timezone
import sqlite3
import multiprocessing
import aiohttp

# ---------------- DATABASE SETUP ----------------
def init_quiz_db():
    """Initialize SQLite database for quiz bot"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    
    # Groups table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY,
            group_name TEXT,
            quiz_active BOOLEAN DEFAULT TRUE,
            interval_minutes INTEGER DEFAULT 30,
            last_question_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    #users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Players table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            group_id INTEGER,
            username TEXT,
            first_name TEXT,
            score INTEGER DEFAULT 0,
            correct_answers INTEGER DEFAULT 0,
            wrong_answers INTEGER DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            max_streak INTEGER DEFAULT 0,
            last_answer_time TIMESTAMP,
            UNIQUE(user_id, group_id)
        )
    ''')
    
    # Questions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_answer INTEGER NOT NULL,
            category TEXT DEFAULT 'General',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Question usage tracking per group
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS question_usage (
            group_id INTEGER,
            question_id INTEGER,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (group_id, question_id)
        )
    ''')
    
    # Active polls tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_polls (
            group_id INTEGER PRIMARY KEY,
            poll_id TEXT,
            question_id INTEGER,
            message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    
    # Insert default questions if not exists
    insert_default_questions()

def insert_default_questions():
    """Insert 50 default GK questions"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    
    # Check if questions already exist
    cursor.execute('SELECT COUNT(*) FROM questions')
    if cursor.fetchone()[0] > 0:
        conn.close()
        return
    
    questions = [
        ("What is the capital of France?", "London", "Berlin", "Paris", "Madrid", 2, "Geography"),
        ("Which planet is known as the Red Planet?", "Venus", "Mars", "Jupiter", "Saturn", 1, "Science"),
        ("Who wrote Romeo and Juliet?", "Charles Dickens", "William Shakespeare", "Mark Twain", "Jane Austen", 1, "Literature"),
        ("What is the largest mammal in the world?", "Elephant", "Blue Whale", "Giraffe", "Hippopotamus", 1, "Biology"),
        ("In which year did World War II end?", "1944", "1945", "1946", "1947", 1, "History"),
        ("What is the chemical symbol for gold?", "Go", "Gd", "Au", "Ag", 2, "Chemistry"),
        ("Which country is home to Machu Picchu?", "Brazil", "Argentina", "Peru", "Chile", 2, "Geography"),
        ("Who painted the Mona Lisa?", "Vincent van Gogh", "Pablo Picasso", "Leonardo da Vinci", "Michelangelo", 2, "Art"),
        ("What is the smallest prime number?", "0", "1", "2", "3", 2, "Mathematics"),
        ("Which gas makes up about 78% of Earth's atmosphere?", "Oxygen", "Nitrogen", "Carbon Dioxide", "Argon", 1, "Science"),
        ("What is the hardest natural substance on Earth?", "Gold", "Iron", "Diamond", "Platinum", 2, "Science"),
        ("Which river is the longest in the world?", "Amazon", "Nile", "Mississippi", "Yangtze", 1, "Geography"),
        ("Who was the first person to walk on the moon?", "Buzz Aldrin", "Neil Armstrong", "John Glenn", "Alan Shepard", 1, "History"),
        ("What is the largest ocean on Earth?", "Atlantic", "Indian", "Arctic", "Pacific", 3, "Geography"),
        ("Which element has the atomic number 1?", "Helium", "Hydrogen", "Lithium", "Carbon", 1, "Chemistry"),
        ("In Greek mythology, who is the king of the gods?", "Poseidon", "Hades", "Zeus", "Apollo", 2, "Mythology"),
        ("What is the speed of light in vacuum?", "299,792,458 m/s", "300,000,000 m/s", "299,000,000 m/s", "301,000,000 m/s", 0, "Physics"),
        ("Which country has the most time zones?", "Russia", "USA", "China", "Canada", 0, "Geography"),
        ("What is the currency of Japan?", "Yuan", "Won", "Yen", "Ringgit", 2, "Economics"),
        ("Who composed The Four Seasons?", "Bach", "Mozart", "Vivaldi", "Beethoven", 2, "Music"),
        ("What is the largest desert in the world?", "Sahara", "Gobi", "Antarctica", "Arabian", 2, "Geography"),
        ("Which blood type is known as the universal donor?", "A+", "B+", "AB+", "O-", 3, "Biology"),
        ("What is the square root of 144?", "11", "12", "13", "14", 1, "Mathematics"),
        ("Which country invented pizza?", "Greece", "Italy", "France", "Spain", 1, "Culture"),
        ("What is the boiling point of water at sea level?", "90°C", "95°C", "100°C", "105°C", 2, "Science"),
        ("Who wrote '1984'?", "Aldous Huxley", "George Orwell", "Ray Bradbury", "H.G. Wells", 1, "Literature"),
        ("What is the smallest country in the world?", "Monaco", "Vatican City", "San Marino", "Liechtenstein", 1, "Geography"),
        ("Which instrument did Louis Armstrong play?", "Piano", "Trumpet", "Saxophone", "Drums", 1, "Music"),
        ("What is the main ingredient in guacamole?", "Tomato", "Avocado", "Onion", "Pepper", 1, "Food"),
        ("Which planet is closest to the Sun?", "Venus", "Mercury", "Earth", "Mars", 1, "Science"),
        ("What does 'www' stand for?", "World Wide Web", "World Wide Network", "Web World Wide", "Wide World Web", 0, "Technology"),
        ("Which animal is known as the 'Ship of the Desert'?", "Horse", "Camel", "Elephant", "Donkey", 1, "Biology"),
        ("What is the capital of Australia?", "Sydney", "Melbourne", "Canberra", "Brisbane", 2, "Geography"),
        ("Who invented the telephone?", "Thomas Edison", "Alexander Graham Bell", "Nikola Tesla", "Benjamin Franklin", 1, "History"),
        ("What is the largest bone in the human body?", "Tibia", "Femur", "Humerus", "Fibula", 1, "Biology"),
        ("Which metal is liquid at room temperature?", "Lead", "Mercury", "Tin", "Zinc", 1, "Chemistry"),
        ("What is the study of earthquakes called?", "Geology", "Seismology", "Meteorology", "Volcanology", 1, "Science"),
        ("Which vitamin is produced when skin is exposed to sunlight?", "Vitamin A", "Vitamin B", "Vitamin C", "Vitamin D", 3, "Biology"),
        ("What is the longest river in South America?", "Orinoco", "Amazon", "Paraná", "Magdalena", 1, "Geography"),
        ("Who painted 'The Starry Night'?", "Pablo Picasso", "Vincent van Gogh", "Claude Monet", "Salvador Dalí", 1, "Art"),
        ("What is the chemical formula for water?", "H2O2", "H2O", "HO2", "H3O", 1, "Chemistry"),
        ("Which continent is the Sahara Desert located on?", "Asia", "Africa", "Australia", "South America", 1, "Geography"),
        ("What is the tallest mountain in the world?", "K2", "Mount Everest", "Kangchenjunga", "Lhotse", 1, "Geography"),
        ("Who was the first President of the United States?", "Thomas Jefferson", "George Washington", "John Adams", "Benjamin Franklin", 1, "History"),
        ("What is the largest planet in our solar system?", "Saturn", "Jupiter", "Neptune", "Uranus", 1, "Science"),
        ("Which ocean is Bermuda located in?", "Pacific", "Atlantic", "Indian", "Arctic", 1, "Geography"),
        ("What does 'CPU' stand for?", "Central Processing Unit", "Computer Processing Unit", "Central Program Unit", "Computer Program Unit", 0, "Technology"),
        ("Which bird is a symbol of peace?", "Eagle", "Dove", "Swan", "Crane", 1, "Culture"),
        ("What is the freezing point of water?", "-1°C", "0°C", "1°C", "2°C", 1, "Science"),
        ("Who wrote 'Pride and Prejudice'?", "Emily Brontë", "Jane Austen", "Charlotte Brontë", "George Eliot", 1, "Literature")
    ]
    
    cursor.executemany('''
        INSERT INTO questions (question, option_a, option_b, option_c, option_d, correct_answer, category)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', questions)
    
    conn.commit()
    conn.close()

# Database helper functions
def add_group(group_id, group_name):
    """Add a group to database"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO groups (group_id, group_name)
        VALUES (?, ?)
    ''', (group_id, group_name))
    conn.commit()
    conn.close()

def get_group_settings(group_id):
    """Get group quiz settings"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT quiz_active, interval_minutes, last_question_id
        FROM groups WHERE group_id = ?
    ''', (group_id,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (True, 30, 0)

def update_group_settings(group_id, **kwargs):
    """Update group settings"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    
    for key, value in kwargs.items():
        cursor.execute(f'''
            UPDATE groups SET {key} = ? WHERE group_id = ?
        ''', (value, group_id))
    
    conn.commit()
    conn.close()

def add_or_update_player(user_id, group_id, username, first_name):
    """Add or update player in database"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO players (user_id, group_id, username, first_name, score, correct_answers, wrong_answers, current_streak, max_streak)
        VALUES (?, ?, ?, ?, 
                COALESCE((SELECT score FROM players WHERE user_id = ? AND group_id = ?), 0),
                COALESCE((SELECT correct_answers FROM players WHERE user_id = ? AND group_id = ?), 0),
                COALESCE((SELECT wrong_answers FROM players WHERE user_id = ? AND group_id = ?), 0),
                COALESCE((SELECT current_streak FROM players WHERE user_id = ? AND group_id = ?), 0),
                COALESCE((SELECT max_streak FROM players WHERE user_id = ? AND group_id = ?), 0))
    ''', (user_id, group_id, username, first_name, user_id, group_id, user_id, group_id, user_id, group_id, user_id, group_id, user_id, group_id))
    conn.commit()
    conn.close()

def update_player_score(user_id, group_id, points, is_correct):
    """Update player score and statistics"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    
    if is_correct:
        cursor.execute('''
            UPDATE players 
            SET score = score + ?, 
                correct_answers = correct_answers + 1,
                current_streak = current_streak + 1,
                max_streak = MAX(max_streak, current_streak + 1),
                last_answer_time = CURRENT_TIMESTAMP
            WHERE user_id = ? AND group_id = ?
        ''', (points, user_id, group_id))
    else:
        cursor.execute('''
            UPDATE players 
            SET score = score + ?, 
                wrong_answers = wrong_answers + 1,
                current_streak = 0,
                last_answer_time = CURRENT_TIMESTAMP
            WHERE user_id = ? AND group_id = ?
        ''', (points, user_id, group_id))
    
    conn.commit()
    conn.close()

def get_next_question(group_id):
    """Get next unused question for the group"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()

    # Check if any questions exist at all
    cursor.execute('SELECT COUNT(*) FROM questions')
    total_questions = cursor.fetchone()[0]

    if total_questions == 0:
        conn.close()
        return None # No questions in database
    
    # Get questions not used by this group
    cursor.execute('''
        SELECT q.* FROM questions q
        LEFT JOIN question_usage qu ON q.id = qu.question_id AND qu.group_id = ?
        WHERE qu.question_id IS NULL
        ORDER BY RANDOM()
        LIMIT 1
    ''', (group_id,))
    
    question = cursor.fetchone()
    
    # If all questions used, reset and get random question
    if not question:
        cursor.execute('DELETE FROM question_usage WHERE group_id = ?', (group_id,))
        cursor.execute('SELECT * FROM questions ORDER BY RANDOM() LIMIT 1')
        question = cursor.fetchone()
    
    # Mark question as used
    if question:
        cursor.execute('''
            INSERT OR REPLACE INTO question_usage (group_id, question_id)
            VALUES (?, ?)
        ''', (group_id, question[0]))
    
    conn.commit()
    conn.close()
    return question

def get_group_leaderboard(group_id, limit=10):
    """Get group leaderboard"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, first_name, score, correct_answers, wrong_answers, current_streak, max_streak
        FROM players
        WHERE group_id = ?
        ORDER BY score DESC, correct_answers DESC
        LIMIT ?
    ''', (group_id, limit))
    result = cursor.fetchall()
    conn.close()
    return result

def reset_group_leaderboard(group_id):
    """Reset group leaderboard"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE players 
        SET score = 0, correct_answers = 0, wrong_answers = 0, current_streak = 0, max_streak = 0
        WHERE group_id = ?
    ''', (group_id,))
    cursor.execute('DELETE FROM question_usage WHERE group_id = ?', (group_id,))
    conn.commit()
    conn.close()

def store_active_poll(group_id, poll_id, question_id, message_id):
    """Store active poll information"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO active_polls (group_id, poll_id, question_id, message_id)
        VALUES (?, ?, ?, ?)
    ''', (group_id, poll_id, question_id, message_id))
    conn.commit()
    conn.close()

def get_active_poll(group_id):
    """Get active poll for group"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT poll_id, question_id, message_id FROM active_polls WHERE group_id = ?
    ''', (group_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def remove_active_poll(group_id):
    """Remove active poll"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM active_polls WHERE group_id = ?', (group_id,))
    conn.commit()
    conn.close()

# ---------------- TELEGRAM CLIENT SETUP ----------------
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

client = TelegramClient('quiz_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Global storage for scheduled tasks
group_tasks = {}

# ---------------- QUIZ FUNCTIONS ----------------

def mention_name(user):
    return f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

async def send_quiz_question(group_id):
    """Send a quiz question to the group"""
    try:
        # Check if quiz is active for this group
        quiz_active, interval_minutes, _ = get_group_settings(group_id)
        if not quiz_active:
            return
        
        # Delete previous poll if exists
        active_poll = get_active_poll(group_id)
        if active_poll:
            try:
                await client.delete_messages(group_id, active_poll[2])
            except:
                pass
            remove_active_poll(group_id)
        
        # Get next question
        question = get_next_question(group_id)
        if not question:
            print(f"❌ No questions available for group {group_id}")
            return
        
        question_id, question_text, opt_a, opt_b, opt_c, opt_d, correct_answer, category = question[:8]
        
        # Create poll options
        options = [opt_a, opt_b, opt_c, opt_d]
        
        # Send poll
        poll = await client.send_message(
            group_id,
            f"📚 **Quiz Time!** 📚\n\n**Category:** {category}\n\n{question_text}",
            poll=True,
            poll_question=question_text,
            poll_options=options
        )
        
        # Store active poll
        store_active_poll(group_id, poll.poll.id, question_id, poll.id)
        
        print(f"✅ Quiz question sent to group {group_id}")
        
    except Exception as e:
        print(f"❌ Error sending quiz to group {group_id}: {e}")

async def schedule_quiz_for_group(group_id):
    """Schedule recurring quiz for a group"""
    while True:
        try:
            quiz_active, interval_minutes, _ = get_group_settings(group_id)
            
            if quiz_active:
                await send_quiz_question(group_id)
            
            # Wait for the next interval
            await asyncio.sleep(interval_minutes * 60)
            
        except Exception as e:
            print(f"❌ Error in quiz scheduler for group {group_id}: {e}")
            await asyncio.sleep(300)  # Wait 5 minutes before retrying

def start_group_quiz_schedule(group_id):
    """Start quiz scheduling for a group"""
    if group_id not in group_tasks:
        task = asyncio.create_task(schedule_quiz_for_group(group_id))
        group_tasks[group_id] = task
        print(f"📅 Quiz scheduler started for group {group_id}")

def stop_group_quiz_schedule(group_id):
    """Stop quiz scheduling for a group"""
    if group_id in group_tasks:
        group_tasks[group_id].cancel()
        del group_tasks[group_id]
        print(f"🛑 Quiz scheduler stopped for group {group_id}")

# ---------------- EVENT HANDLERS ----------------
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handle /start command"""
    if event.is_group:
        group_id = event.chat_id
        group_name = event.chat.title or "Unknown Group"
        
        add_group(group_id, group_name)
        start_group_quiz_schedule(group_id)
        
        await event.respond(
            "🤖 **Quiz Bot Started!** 🤖\n\n"
            "🎯 I'll send quiz questions every **30 minutes** by default.\n"
            "📊 Answer polls to earn points: **+4** for correct, **-1** for wrong!\n\n"
            "**Admin Commands:**\n"
            "• `/quizstop` - Stop sending questions\n"
            "• `/quizstart` - Resume sending questions\n"
            "• `/setinterval <minutes>` - Set interval (5-1440 min)\n"
            "• `/leaderboard` - Show group rankings\n"
            "• `/resetboard` - Reset leaderboard\n\n"
            "Let the quiz begin! 🎉"
        )
    else:
        # Track user in database
        user = await event.get_sender()
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user.id, user.username, user.first_name))
        conn.commit()
        conn.close()

        await event.respond(
            "👋 Hi! I'm a Quiz Bot for groups.\n\n"
            "Add me to a group and use `/start` to begin the quiz fun! 🎯"
        )

@client.on(events.NewMessage(pattern='/quizstop'))
async def quiz_stop_handler(event):
    """Handle /quizstop command"""
    if not event.is_group:
        return
    
    # Check if user is admin
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this command!")
        return
    
    group_id = event.chat_id
    update_group_settings(group_id, quiz_active=False)
    
    # Delete active poll
    active_poll = get_active_poll(group_id)
    if active_poll:
        try:
            await client.delete_messages(group_id, active_poll[2])
        except:
            pass
        remove_active_poll(group_id)
    
    await event.respond("🛑 **Quiz stopped!** No more questions will be sent until you use `/quizstart`.")

@client.on(events.NewMessage(pattern='/quizstart'))
async def quiz_start_handler(event):
    """Handle /quizstart command"""
    if not event.is_group:
        return
    
    # Check if user is admin
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this command!")
        return
    
    group_id = event.chat_id
    update_group_settings(group_id, quiz_active=True)
    
    await event.respond("✅ **Quiz resumed!** Questions will be sent according to the set interval.")

@client.on(events.NewMessage(pattern=r'/setinterval (\d+)'))
async def set_interval_handler(event):
    """Handle /setinterval command"""
    if not event.is_group:
        return
    
    # Check if user is admin
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this command!")
        return
    
    try:
        minutes = int(event.pattern_match.group(1))
        
        if minutes < 5 or minutes > 1440:
            await event.respond("❌ Interval must be between 5 minutes and 24 hours (1440 minutes)!")
            return
        
        group_id = event.chat_id
        update_group_settings(group_id, interval_minutes=minutes)
        
        await event.respond(f"⏰ **Quiz interval updated!** Questions will now be sent every **{minutes} minutes**.")
        
    except (ValueError, AttributeError):
        await event.respond("❌ Please use: `/setinterval <minutes>` (5-1440)")

@client.on(events.NewMessage(pattern='/checkquestions'))
async def check_questions_handler(event):
    """Check if questions are available (admin only)"""
    if not event.is_group:
        return
    
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this command!")
        return
    
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM questions')
    count = cursor.fetchone()[0]
    conn.close()
    
    if count == 0:
        await event.respond("⚠️ **No questions in database!** Quiz will not work until questions are added.")
    else:
        await event.respond(f"✅ **{count} questions** available in database.")

@client.on(events.NewMessage(pattern='/leaderboard'))
async def leaderboard_handler(event):
    """Handle /leaderboard command"""
    if not event.is_group:
        return
    
    group_id = event.chat_id
    leaderboard = get_group_leaderboard(group_id)
    
    if not leaderboard:
        await event.respond("📊 **Group Leaderboard** 📊\n\nNo players yet! Answer some quiz questions to appear here! 🎯")
        return
    
    message = "🏆 **Group Leaderboard** 🏆\n\n"
    
    for i, (user_id, username, first_name, score, correct, wrong, current_streak, max_streak) in enumerate(leaderboard, 1):
        # Get user object for mention
        try:
            user = await client.get_entity(user_id)  # You'll need user_id from database
            name = mention_name(user)
        except:
            name = username if username else (first_name or "Anonymous")
        streak_info = f"({current_streak}🔥)" if current_streak > 0 else ""
        
        if i == 1:
            message += f"👑 **{name}** {streak_info}\n"
        elif i == 2:
            message += f"🥈 **{name}** {streak_info}\n"
        elif i == 3:
            message += f"🥉 **{name}** {streak_info}\n"
        else:
            message += f"{i}. **{name}** {streak_info}\n"
        
        message += f"    💯 Score: **{score}** | ✅ {correct} | ❌ {wrong} | 🔥 Max: {max_streak}\n\n"
    
    await event.respond(message, parse_mode='html')

@client.on(events.NewMessage(pattern='/resetboard'))
async def reset_board_handler(event):
    """Handle /resetboard command"""
    if not event.is_group:
        return
    
    # Check if user is admin
    if not await is_admin(event):
        await event.respond("❌ Only group admins can use this command!")
        return
    
    group_id = event.chat_id
    reset_group_leaderboard(group_id)
    
    await event.respond("🔄 **Leaderboard reset!** All scores and statistics have been cleared. Fresh start for everyone! 🎯")

@client.on(events.Raw(types=[UpdateMessagePollVote]))
async def poll_vote_handler(event):
    """Handle poll vote updates"""
    try:
        poll_update = event
        user_id = poll_update.user_id
        poll_id = poll_update.poll_id
        
        # Get the message to find group_id
        message = await client.get_messages(poll_update.peer, ids=poll_update.msg_id)
        if not message or not hasattr(message.peer_id, 'channel_id'):
            return
            
        group_id = -1000000000000 - message.peer_id.channel_id
        
        # Get user info
        try:
            user = await client.get_entity(user_id)
            username = user.username
            first_name = user.first_name or ""
        except:
            username = None
            first_name = "Unknown"
        
        # Add/update player
        add_or_update_player(user_id, group_id, username, first_name)
        
        # Get the question info from our database
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ap.question_id, q.correct_answer
            FROM active_polls ap
            JOIN questions q ON ap.question_id = q.id
            WHERE ap.group_id = ? AND ap.poll_id = ?
        ''', (group_id, str(poll_id)))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return
        
        question_id, correct_answer = result
        
        # Get selected options from the vote
        if not hasattr(poll_update, 'options') or not poll_update.options:
            return
            
        selected_option = poll_update.options[0]  # First selected option
        
        # Check if answer is correct
        is_correct = selected_option == correct_answer
        points = 4 if is_correct else -1
        
        # Update player score
        update_player_score(user_id, group_id, points, is_correct)
        
        print(f"📊 Player {username or first_name} answered {'✅ correctly' if is_correct else '❌ wrongly'} (+{points} points)")
        
    except Exception as e:
        print(f"❌ Error handling poll vote: {e}")

async def is_admin(event):
    """Check if user is group admin"""
    try:
        user = await event.get_sender()
        chat = await event.get_chat()
        
        # Get user permissions
        perms = await client.get_permissions(chat, user)
        return perms.is_admin or perms.is_creator
        
    except:
        return False
    
async def is_owner(event):
    """Check if user is bot owner"""
    return event.sender_id == OWNER_ID

@client.on(events.NewMessage(pattern='/addquestion'))
async def add_question_handler(event):
    """Show format for adding questions"""
    if not event.is_private or not await is_owner(event):
        return
    
    await event.respond(
        "📝 **Add New Question Format:**\n\n"
        "`/newq Question text here?|Option A|Option B|Option C|Option D|2|Category`\n\n"
        "Where `2` is the correct answer (0=A, 1=B, 2=C, 3=D)\n\n"
        "**Example:**\n"
        "`/newq What is 2+2?|3|4|5|6|1|Mathematics`"
    )

@client.on(events.NewMessage(pattern=r'/newq (.+)'))
async def new_question_handler(event):
    """Add new question to database"""
    if not event.is_private or not await is_owner(event):
        return
    
    try:
        parts = event.pattern_match.group(1).split('|')
        if len(parts) != 7:
            await event.respond("❌ Wrong format! Use: question|A|B|C|D|correct_num|category")
            return
        
        question, opt_a, opt_b, opt_c, opt_d, correct, category = parts
        correct_answer = int(correct.strip())
        
        if correct_answer not in [0, 1, 2, 3]:
            await event.respond("❌ Correct answer must be 0, 1, 2, or 3!")
            return
        
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO questions (question, option_a, option_b, option_c, option_d, correct_answer, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (question.strip(), opt_a.strip(), opt_b.strip(), opt_c.strip(), opt_d.strip(), correct_answer, category.strip()))
        
        question_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        await event.respond(f"✅ **Question added successfully!**\nQuestion ID: {question_id}")
        
    except Exception as e:
        await event.respond(f"❌ Error adding question: {str(e)}")

@client.on(events.NewMessage(pattern='/deleteallq'))
async def delete_all_questions_handler(event):
    """Delete all questions from database"""
    if not event.is_private or not await is_owner(event):
        return
    
    try:
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        
        # Get count first
        cursor.execute('SELECT COUNT(*) FROM questions')
        count = cursor.fetchone()[0]
        
        # Delete all questions
        cursor.execute('DELETE FROM questions')
        # Reset question usage for all groups
        cursor.execute('DELETE FROM question_usage')
        
        conn.commit()
        conn.close()
        
        await event.respond(f"🗑️ **All questions deleted!**\nRemoved {count} questions from database.\n\nDon't forget to add new questions!")
        
    except Exception as e:
        await event.respond(f"❌ Error deleting questions: {str(e)}")

@client.on(events.NewMessage(pattern='/questioncount'))
async def question_count_handler(event):
    """Show total questions count"""
    if not event.is_private or not await is_owner(event):
        return
    
    try:
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM questions')
        count = cursor.fetchone()[0]
        conn.close()
        
        await event.respond(f"📊 **Total Questions:** {count}")
        
    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")

def get_all_users():
    """Get all users who have used /start in PM"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT user_id FROM users')  # You need to create this table
    result = cursor.fetchall()
    conn.close()
    return [row[0] for row in result]

def get_all_groups():
    """Get all groups where bot is active"""
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT group_id FROM groups')
    result = cursor.fetchall()
    conn.close()
    return [row[0] for row in result]

@client.on(events.NewMessage(pattern=r'/broadcast (.+)', flags=re.DOTALL))
async def broadcast_handler(event):
    """Broadcast message to all users/groups"""
    if not event.is_private or not await is_owner(event):
        return
    
    message = event.pattern_match.group(1)
    
    await event.respond(
        f"📢 **Broadcasting message:**\n\n{message}\n\n"
        "Reply with:\n"
        "• `users` - Send to all users\n"
        "• `groups` - Send to all groups\n"
        "• `all` - Send to both users and groups"
    )

@client.on(events.NewMessage(pattern=r'^(users|groups|all)$'))
async def broadcast_confirm_handler(event):
    """Confirm and execute broadcast"""
    if not event.is_private or not await is_owner(event) or not event.is_reply:
        return
    
    # Get the message being replied to
    replied_msg = await event.get_reply_message()
    if not replied_msg.text.startswith("📢 **Broadcasting message:**"):
        return
    
    # Extract the broadcast message
    broadcast_text = replied_msg.text.split("\n\n")[1].split("\n\n")[0]
    target = event.text.lower()
    
    sent_count = 0
    failed_count = 0
    
    try:
        if target in ['users', 'all']:
            users = get_all_users()
            for user_id in users:
                try:
                    await client.send_message(user_id, broadcast_text, parse_mode='html')
                    sent_count += 1
                    await asyncio.sleep(0.1)  # Avoid flood limits
                except:
                    failed_count += 1
        
        if target in ['groups', 'all']:
            groups = get_all_groups()
            for group_id in groups:
                try:
                    await client.send_message(group_id, broadcast_text, parse_mode='html')
                    sent_count += 1
                    await asyncio.sleep(0.1)  # Avoid flood limits
                except:
                    failed_count += 1
        
        await event.respond(
            f"✅ **Broadcast Complete!**\n\n"
            f"📤 Sent: {sent_count}\n"
            f"❌ Failed: {failed_count}"
        )
        
    except Exception as e:
        await event.respond(f"❌ Broadcast failed: {str(e)}")

# ---------------- FLASK KEEP-ALIVE + MAIN LOOP ----------------
# Dummy Flask app for Render health-check
app = Flask(__name__)

@app.route('/')
def home():
    return "Quiz Bot is running!"

def run_web():
    # Run flask on separate process (not thread, avoids asyncio loop conflicts)
    app.run(host="0.0.0.0", port=10000)

async def keep_alive():
    """Periodically ping the Render URL to prevent sleeping"""
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                await session.get("https://nkquizbot.onrender.com")  # Replace with your actual URL
                print("🌍 Keep-alive ping sent!")
        except Exception as e:
            print(f"⚠️ Keep-alive failed: {e}")
        await asyncio.sleep(240)  # every 4 minutes

if __name__ == "__main__":
    # Initialize database
    init_quiz_db()
    print("📊 Database initialized!")
    
    # Run Flask in separate process
    multiprocessing.Process(target=run_web, daemon=True).start()
    print("🌐 Flask server started!")
    
    # Start keep-alive inside Telethon loop
    client.loop.create_task(keep_alive())
    
    print("🤖 Starting Quiz Bot...")
    print("✅ Bot is ready! Add to groups and use /start to begin!")
    
    # Run bot (manages its own event loop)
    client.run_until_disconnected()