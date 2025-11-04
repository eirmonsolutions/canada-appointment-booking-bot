# app.py
import os
import sys
import threading
import queue
from flask import Flask, render_template, request, jsonify, Response
from pathlib import Path
from datetime import datetime

# ----------------------------------------------------------------------
# Add project root so we can import bot.py
# ----------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
sys.path.append(str(BASE))



from bot import Bot, Config, Logger, ASC_FILE, LOG_FILE, CONFIG_FILE, LOG_FORMAT

app = Flask(__name__, template_folder="templates")   # <-- TEMPLATES
app.secret_key = os.urandom(24)

# ----------------------------------------------------------------------
# Global objects
# ----------------------------------------------------------------------
bots: dict[str, Bot] = {}                # email → Bot instance
bot_threads: dict[str, threading.Thread] = {}
log_queue: queue.Queue = queue.Queue()   # SSE buffer

EMBASSY_TO_FACILITY = {
    "en-ca-cal": "89",   # Calgary
    "en-ca-hal": "90",   # Halifax
    "en-ca-mon": "91",   # Montreal
    "en-ca-ott": "92",   # Ottawa
    "en-ca-que": "93",   # Quebec City
    "en-ca-tor": "94",   # Toronto
    "en-ca-van": "95",   # Vancouver
}

# ----------------------------------------------------------------------
# Helper – push a line to the browser (called from Logger)
# ----------------------------------------------------------------------
def push_log(prefix: str, line: str):
    safe_line = line.replace("|", "¦")
    log_queue.put(f"{prefix}|{safe_line}")


# ----------------------------------------------------------------------
# Bot starter – one bot per e-mail
# ----------------------------------------------------------------------
@app.route("/start", methods=["POST"])
def start():
    global bots, bot_threads

    data = request.get_json()
    email = data.get("username").strip()
    password = data.get("password")
    embassy_key = data.get("embassy")          # e.g. "en-ca-tor"
    period_start = data.get("period_start")
    period_end = data.get("period_end")
    schedule_id = data.get("schedule_id")

    if not email or not password or not schedule_id or not embassy_key:
        return jsonify({"error": "email, password, schedule_id and embassy required"}), 400

    # ------------------------------------------------------------------
    # Per-user config file
    # ------------------------------------------------------------------
    user_cfg_file = f"config_{email.replace('@', '_').replace('.', '_')}.txt"
    cfg = Config(user_cfg_file)

    cfg.email = email
    cfg.password = password
    cfg.country = embassy_key.split("-")[1]                # "ca"
    cfg.min_date = datetime.strptime(period_start, "%Y-%m-%d").date()
    cfg.max_date = datetime.strptime(period_end, "%Y-%m-%d").date() if period_end else None
    cfg.schedule_id = schedule_id
    cfg.need_asc = False

    # SET FACILITY_ID AUTOMATICALLY FROM UI SELECTION
    cfg.facility_id = EMBASSY_TO_FACILITY[embassy_key]

    cfg.save()          # now works (you added the public method)

    # ------------------------------------------------------------------
    # Logger + Bot (unchanged)
    # ------------------------------------------------------------------
    logger = Logger(LOG_FILE, LOG_FORMAT, user_prefix=email,
                    callback=lambda txt: push_log(email, txt))

    bot = Bot(cfg, logger, ASC_FILE)

    def run_bot():
        try:
            bot.process()
        except Exception as e:
            push_log(email, f"CRASH: {e}")

    if email in bots:
        push_log(email, "Stopping previous bot instance")
        bots[email].session.close()

    bots[email] = bot
    bot_threads[email] = threading.Thread(target=run_bot, daemon=True)
    bot_threads[email].start()

    push_log(email, f"Bot STARTED – embassy {embassy_key} (facility {cfg.facility_id})")
    return jsonify({"status": "ok"})
# ----------------------------------------------------------------------
# Stop a specific bot
# ----------------------------------------------------------------------
@app.route("/stop/<email>", methods=["POST"])
def stop(email):
    email = email.strip()
    if email not in bots:
        return jsonify({"error": "not running"}), 404

    push_log(email, "Stop requested by UI")
    bots[email].session.close()
    del bots[email]
    del bot_threads[email]
    return jsonify({"status": "stopped"})


# ----------------------------------------------------------------------
# Server-Sent Events – live log tail
# ----------------------------------------------------------------------
@app.route("/log_stream")
def log_stream() -> Response:
    def event_stream():
        while not log_queue.empty():
            try:
                yield f"data: {log_queue.get_nowait()}\n\n"
            except queue.Empty:
                break
        while True:
            line = log_queue.get()
            yield f"data: {line}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


# ----------------------------------------------------------------------
# Return available dates for a specific user (optional UI helper)
# ----------------------------------------------------------------------
@app.route("/available_dates/<email>")
def available_dates(email):
    bot = bots.get(email)
    if not bot:
        return jsonify([])

    try:
        dates = bot.get_available_dates()
        return jsonify(dates)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------------------------
# Home page – render index.html from templates/
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ----------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)      # <-- TEMPLATES
    app.run(host="0.0.0.0", port=5005, debug=False, threaded=True)