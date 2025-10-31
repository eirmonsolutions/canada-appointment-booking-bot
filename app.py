# app.py
import os
import sys
import threading
import queue
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, Response
from pathlib import Path

# ----------------------------------------------------------------------
# Add project root so we can import bot.py / web.config_manager
# ----------------------------------------------------------------------
BASE = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE))

from web.config_manager import get_config, save_config
from bot import Bot, Config, Logger, ASC_FILE, LOG_FILE, CONFIG_FILE

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
def start_bot():
    global bot_thread, bot_instance

    if bot_thread and bot_thread.is_alive():
        flash("Bot is already running!", "error")
        return

    cfg = Config(CONFIG_FILE)                     # reads the file again
    logger = Logger(
        LOG_FILE,
        "%(asctime)s  %(message)s",
        user_prefix=cfg.email,                    # <-- [john@example.com]:
        callback=push_log                         # <-- send to Flask
    )

    bot_instance = Bot(cfg, logger, ASC_FILE)

    def run():
        try:
            bot_instance.process()
        except Exception as e:
            logger(e)
        finally:
            push_log("[SYSTEM]: Bot stopped")

    bot_thread = threading.Thread(target=run, daemon=True)
    bot_thread.start()
    flash("Bot started in background", "success")


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