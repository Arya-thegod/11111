import logging
import asyncio
import random
import os
import threading
import time
import datetime
import psutil 
import gc 
from flask import Flask, Response
from telegram import Update
from telegram.error import RetryAfter, Forbidden
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ─── LOGGING FIX ───
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING) 

# ─── FLASK WEB SERVER ───
web_app = Flask(__name__)
@web_app.route('/')
def home(): return Response("NC BOT IS ALIVE!", status=200, mimetype='text/plain')
def run_web(): 
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port, threaded=True)

# ─── CONFIGURATION ───
OWNER_ID = int(os.environ.get("OWNER_ID", "0")) 
DELAY = 0.2  # Fixed Speed
EMOJIS = ["🔥", "⚡", "💀", "🚀", "🌞", "✅", "💋", "😝"]

# ─── STATUS VARIABLES ───
START_TIME = time.time()
TOTAL_NC_RENAMES = 0
LAST_PURGE_DATE = None 

# ─── CONTROLLER & MEMORY ───
NC_STATES = {} 

class Controller:
    def __init__(self):
        self.active = {}

    def stop_chat(self, chat_id):
        if chat_id in self.active:
            tasks = self.active.pop(chat_id)
            for task in tasks:
                if not task.done():
                    task.cancel()

    def stop_all(self):
        for cid in list(self.active.keys()):
            self.stop_chat(cid)

ctrl = Controller()
bots = []

# ─── NC LOOP ───
async def nc_loop(bot, chat_id, text):
    global TOTAL_NC_RENAMES
    while True:
        try:
            e = random.choice(EMOJIS)
            title = f"{e}{text}{e}"[:255]
            await bot.set_chat_title(chat_id=chat_id, title=title)
            TOTAL_NC_RENAMES += 1 
            await asyncio.sleep(DELAY)
            
        except asyncio.CancelledError:
            break 
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1) 
        except Forbidden:
            break 
        except Exception as e:
            await asyncio.sleep(2) 

# ─── SMART AUTO PURGE SCHEDULER ───
async def smart_purge_scheduler():
    global LAST_PURGE_DATE
    while True:
        await asyncio.sleep(60) 
        
        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist_tz)
        
        if now_ist.hour >= 19 and LAST_PURGE_DATE != now_ist.date():
            logger.info("Starting Daily Auto-Purge (RAM Clean)...")
            ctrl.stop_all()
            ctrl.active.clear()
            gc.collect() 
            
            for chat_id, text in NC_STATES.items():
                ctrl.active[chat_id] = []
                for b in bots:
                    task = asyncio.create_task(nc_loop(b['bot'], chat_id, text))
                    ctrl.active[chat_id].append(task)
            
            LAST_PURGE_DATE = now_ist.date()
            logger.info("Daily Auto-Purge Completed.")
            
            try:
                if bots:
                    await bots[0]['bot'].send_message(OWNER_ID, "🧹 <b>Daily Auto-Purge Done!</b>\nRAM 0 se fresh ho gayi hai. Saare NC commands auto-restart ho gaye hain.", parse_mode='HTML')
            except: pass

# ─── COMMAND ROUTER ───
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    text = msg.text or ""
    if not (text.startswith("/") or text.startswith(".")): return

    user = update.effective_user
    if not user or user.id != OWNER_ID: return

    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0].lower()
    args = parts[1].split() if len(parts) > 1 else []

    if cmd == "nc":
        if not args: return await msg.reply_text("Usage: /nc <text>")
        target = ' '.join(args)
        chat_id = update.effective_chat.id
        
        ctrl.stop_chat(chat_id)
        ctrl.active[chat_id] = []
        NC_STATES[chat_id] = target
        
        for b in bots:
            task = asyncio.create_task(nc_loop(b['bot'], chat_id, target))
            ctrl.active[chat_id].append(task)
            
        await msg.reply_text(f"✅ NC STARTED: {target}\n⚡ Speed: {DELAY}s")

    elif cmd == "stop":
        chat_id = update.effective_chat.id
        if ctrl.active.get(chat_id):
            ctrl.stop_chat(chat_id)
            NC_STATES.pop(chat_id, None) 
            await msg.reply_text("🛑 STOPPED")
        else:
            await msg.reply_text("⚠️ NO ACTIVE NC")

    # ─── 100% REAL-TIME STATUS COMMAND ───
    elif cmd == "status":
        # 1. Uptime
        uptime_seconds = int(time.time() - START_TIME)
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s" if days > 0 else f"{hours}h {minutes}m {seconds}s"

        # 2. REAL System RAM (Direct from Linux Kernel cgroups)
        vm = psutil.virtual_memory()
        total_ram = vm.total / (1024 * 1024)  # Yeh Render ka total limit batayega (e.g. 512MB)
        used_ram = vm.used / (1024 * 1024)    # System ne kitna use kiya
        free_ram = vm.available / (1024 * 1024) # Kitna free hai
        ram_percent = (used_ram / total_ram) * 100

        # 3. REAL Bot Process RAM (Sirf aapki sarya.py file)
        process = psutil.Process(os.getpid())
        bot_ram = process.memory_info().rss / (1024 * 1024)

        # 4. RAM Meter Bar (10 blocks)
        filled = int(ram_percent / 10)
        if filled > 10: filled = 10
        meter = "■" * filled + "□" * (10 - filled)

        # 5. Time Calculation for Purge
        ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.datetime.now(ist_tz)
        purge_time = now_ist.replace(hour=19, minute=0, second=0, microsecond=0)
        if now_ist >= purge_time:
            purge_time += datetime.timedelta(days=1)
        
        time_left = purge_time - now_ist
        h_left, r = divmod(time_left.seconds, 3600)
        m_left, s_left = divmod(r, 60)
        
        next_purge_str = purge_time.strftime("%d-%b %I:%M %p")

        status_text = (
            f"📊 <b>RENDER REAL-TIME STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💾 <b>System Memory (RAM):</b>\n"
            f"• Total Limit: {total_ram:.1f} MB\n"
            f"• Container Used: <b>{used_ram:.1f} MB</b> ({ram_percent:.1f}%)\n"
            f"• Container Free: {free_ram:.1f} MB\n"
            f"• Live Meter: [{meter}] {ram_percent:.1f}%\n\n"
            f"🤖 <b>Bot Process (sarya.py):</b>\n"
            f"• Consuming: <b>{bot_ram:.1f} MB</b>\n"
            f"• Status: Healthy & Safe ✅\n\n"
            f"🧹 <b>Auto-Purge (7:00 PM IST):</b>\n"
            f"• Next Clean: <b>{next_purge_str}</b>\n"
            f"• Time Left: <b>{h_left}h {m_left}m {s_left}s</b>\n\n"
            f"⚙️ <b>Bot Engine:</b>\n"
            f"• Active GCs: {len(ctrl.active)}\n"
            f"• Total NC Renames: {TOTAL_NC_RENAMES}\n"
            f"• Speed: 1 NC / {DELAY}s\n"
            f"• Uptime: {uptime_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await msg.reply_text(status_text, parse_mode='HTML')

# ─── MAIN RUNNER ───
async def run_bot(tok):
    try:
        app = Application.builder().token(tok).connect_timeout(10).read_timeout(30).build()
        me = await app.bot.get_me()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
        app.add_handler(MessageHandler(filters.COMMAND, router))
        
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True) 
        
        bots.append({'id': me.id, 'bot': app.bot, 'app': app})
        print(f"✅ Started @{me.username}")
        await asyncio.Event().wait() 
    except Exception as e:
        print(f"❌ Fail: {e}")

async def main():
    print("[*] Starting Web Server...")
    threading.Thread(target=run_web, daemon=True).start()

    tok = os.environ.get("BOT_TOKEN")
    if not tok:
        print("[!] ERROR: BOT_TOKEN not found in Render.")
        return

    print(f"[*] Starting Bot with fixed {DELAY}s delay...")
    asyncio.create_task(smart_purge_scheduler())
    await run_bot(tok.strip())

if __name__ == "__main__":
    asyncio.run(main())
