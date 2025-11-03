# app.py
import os
import sys
import threading
import queue
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, Response
from pathlib import Path
from datetime import datetime
# ----------------------------------------------------------------------
# Add project root so we can import bot.py / web.config_manager
# ----------------------------------------------------------------------
BASE = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE))

from web.config_manager import get_config, save_config
from bot import Bot, Config, Logger, ASC_FILE, LOG_FILE, CONFIG_FILE, LOG_FORMAT



app = Flask(__name__)
app.secret_key = os.urandom(24)

# ----------------------------------------------------------------------
# Global objects
# ----------------------------------------------------------------------
bot_thread: threading.Thread | None = None
bot_instance: Bot | None = None
log_queue: queue.Queue = queue.Queue()          # thread-safe buffer for SSE


# ----------------------------------------------------------------------
# Helper – push a line to the browser (called from Logger)
# ----------------------------------------------------------------------
def push_log(line: str):
    log_queue.put(line)


# ----------------------------------------------------------------------
# Bot starter (now creates a Logger with user-prefix + SSE callback)
# ----------------------------------------------------------------------



# -------------------------------------------------
# Bot starter – use dict from config_manager
# -------------------------------------------------
@app.route("/start", methods=["POST"])
def start():
    global bot_instance, bot_thread

    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    country = data.get("embassy")   # same as "en-ca" etc.
    period_start = data.get("period_start")
    period_end = data.get("period_end")

    # Prepare config
    cfg = Config(CONFIG_FILE)
    cfg.email = username
    cfg.password = password
    cfg.country = country
    cfg.min_date = datetime.strptime(period_start, "%Y-%m-%d").date()

    cfg.max_date = datetime.strptime(period_end, "%Y-%m-%d").date()
    cfg.schedule_id = data.get("schedule_id")
    cfg._Config__save()

    logger = Logger(LOG_FILE, LOG_FORMAT, username, push_log)
    bot_instance = Bot(cfg, logger, ASC_FILE)

    def run_bot():
        try:
            bot_instance.process()
        except Exception as e:
            push_log(f"{username}|Error: {e}")

    if bot_thread and bot_thread.is_alive():
        push_log(f"{username}|Bot already running.")
        return jsonify({"status": "running"})

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    push_log(f"{username}|Bot started for {country}")
    return jsonify({"status": "ok"})


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    cfg = get_config()                     # dict from config_manager

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save":
            save_config(request.form)
            flash("Configuration saved", "success")
            return redirect(url_for("index"))

        if action == "start":
            start_bot()
            return redirect(url_for("index"))

    return render_template("index.html", cfg=cfg)


# ----------------------------------------------------------------------
# Server-Sent Events – live log tail
# ----------------------------------------------------------------------
@app.route("/log_stream")
def log_stream() -> Response:
    def event_stream():
        # drain any backlog first
        while not log_queue.empty():
            try:
                yield f"data: {log_queue.get_nowait()}\n\n"
            except queue.Empty:
                break
        # then wait for new lines
        while True:
            line = log_queue.get()          # blocks
            yield f"data: {line}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


# ----------------------------------------------------------------------
# JSON endpoint – current available dates (used by the UI)
# ----------------------------------------------------------------------
@app.route("/available_dates")
def available_dates():
    if bot_instance and hasattr(bot_instance, "get_available_dates"):
        try:
            dates = bot_instance.get_available_dates()
            return jsonify(dates)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify([])


# ----------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)          # Flask needs a static folder
    app.run(host="0.0.0.0", port=5005, debug=False, threaded=True)