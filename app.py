# app.py (MySQL version)
import os
import sys
import json
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g

import pymysql
from pymysql.cursors import DictCursor

# ============ MYSQL SETTINGS ============
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASS = os.getenv("MYSQL_PASSWORD", "root1234")
MYSQL_DB   = os.getenv("MYSQL_DB", "visa_scheduler")  # will be auto-created

# ------------- FLASK SETUP -------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", "dev-secret")

Path("profiles").mkdir(exist_ok=True)

# Keep subprocess handles per user in memory
RUNNERS = {}  # user_id -> subprocess.Popen

# ------------- DB HELPERS (PyMySQL) -------------
def _connect_server_db(dbname=None):
    """Connect to MySQL server (optionally to a specific DB)."""
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=dbname,
        autocommit=True,
        cursorclass=DictCursor,
        charset="utf8mb4",
    )

def ensure_database_and_tables():
    # 1) Create database if not exists
    conn = _connect_server_db()
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn.close()

    # 2) Create tables if not exists
    db = _connect_server_db(MYSQL_DB)
    with db.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                password VARCHAR(255) NOT NULL,
                schedule_id VARCHAR(64) NOT NULL,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                embassy VARCHAR(128) NOT NULL,
                session_id VARCHAR(512),
                signed_in_at DATETIME,
                is_active TINYINT(1) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id INT,
                timestamp DATETIME NOT NULL,
                message TEXT NOT NULL,
                CONSTRAINT fk_logs_user
                  FOREIGN KEY (user_id) REFERENCES users(id)
                  ON DELETE CASCADE
                  ON UPDATE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    db.close()

def get_db():
    """Get a request-scoped connection to the application database."""
    if "db" not in g:
        g.db = _connect_server_db(MYSQL_DB)
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# Initialize DB at startup
ensure_database_and_tables()

# ------------- UTILS -------------
def write_user_config_ini(user_row):
    """
    Writes profiles/<id>/config.ini for visa_bot.py (uses your key names).
    You can enrich NOTIFICATION/TIME/CHROMEDRIVER defaults here.
    """
    uid = user_row["id"]
    prof_dir = Path("profiles") / str(uid)
    prof_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = prof_dir / "config.ini"

    ini = f"""[PERSONAL_INFO]
USERNAME={user_row["username"]}
PASSWORD={user_row["password"]}
SCHEDULE_ID={user_row["schedule_id"]}
PRIOD_START={user_row["period_start"]}
PRIOD_END={user_row["period_end"]}
YOUR_EMBASSY={user_row["embassy"]}

[NOTIFICATION]
SENDGRID_API_KEY=
PUSHOVER_TOKEN=
PUSHOVER_USER=
PERSONAL_SITE_USER=
PERSONAL_SITE_PASS=
PUSH_TARGET_EMAIL=
PERSONAL_PUSHER_URL=

[TIME]
RETRY_TIME_L_BOUND=5
RETRY_TIME_U_BOUND=12
WORK_LIMIT_TIME=8
WORK_COOLDOWN_TIME=1
BAN_COOLDOWN_TIME=24

[CHROMEDRIVER]
LOCAL_USE=true
HUB_ADDRESS=http://localhost:4444/wd/hub
"""
    cfg_path.write_text(ini, encoding="utf-8")
    return str(cfg_path)

def add_log(user_id, message):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO logs (user_id, timestamp, message) VALUES (%s, %s, %s)",
            (user_id, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), message),
        )

def _proc_reader(user_id, proc: subprocess.Popen):
    """Read stdout from the user’s bot and store in DB logs."""
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            add_log(user_id, line.rstrip("\n"))
    except Exception as e:
        add_log(user_id, f"[controller] log reader error: {e!r}")

def start_runner(user_id):
    """Launch visa_bot.py as subprocess with this user's config.ini."""
    if user_id in RUNNERS and RUNNERS[user_id].poll() is None:
        return  # already running

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
        user = cur.fetchone()
    if not user:
        return

    cfg_path = write_user_config_ini(user)
    add_log(user_id, f"[controller] starting bot with {cfg_path}")

    # -u for unbuffered so logs stream
    proc = subprocess.Popen(
        [sys.executable, "-u", "visa_bot.py", cfg_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    RUNNERS[user_id] = proc

    t = threading.Thread(target=_proc_reader, args=(user_id, proc), daemon=True)
    t.start()

def stop_runner(user_id):
    proc = RUNNERS.get(user_id)
    if proc and proc.poll() is None:
        add_log(user_id, "[controller] stopping bot")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    RUNNERS.pop(user_id, None)

# ------------- SIGN-IN PLACEHOLDER -------------
def signin_and_save_session(user_id):
    # TODO: Integrate real Selenium login; this is a placeholder.
    fake_session = f"SESSION_{user_id}_{int(datetime.utcnow().timestamp())}"
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE users SET session_id=%s, signed_in_at=%s WHERE id=%s",
            (fake_session, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), user_id),
        )
    add_log(user_id, "[controller] sign-in successful and session saved")

# ------------- ROUTES -------------
@app.route("/")
def index():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users ORDER BY id DESC")
        users = cur.fetchall()
        cur.execute(
            "SELECT l.*, u.username AS user "
            "FROM logs l LEFT JOIN users u ON u.id=l.user_id "
            "ORDER BY l.id DESC LIMIT 200"
        )
        logs = cur.fetchall()
    return render_template("index.html", users=users, logs=logs)

@app.route("/add", methods=["GET", "POST"])
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        schedule_id = request.form.get("schedule_id", "").strip()
        period_start = request.form.get("period_start", "").strip()
        period_end = request.form.get("period_end", "").strip()
        embassy = request.form.get("embassy", "").strip()

        if not (username and password and schedule_id and period_start and period_end and embassy):
            flash("All fields are required.", "error")
            return redirect(url_for("add_user"))

        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password, schedule_id, period_start, period_end, embassy) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (username, password, schedule_id, period_start, period_end, embassy),
            )
        flash("User added.", "success")
        return redirect(url_for("index"))

    return render_template("add_user.html")

@app.post("/signin/<int:user_id>")
def signin_user(user_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
        user = cur.fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("index"))
    try:
        signin_and_save_session(user_id)
        flash("Signed in & session saved.", "success")
    except Exception as e:
        add_log(user_id, f"[controller] sign-in error: {e!r}")
        flash(f"Sign-in failed: {e}", "error")
    return redirect(url_for("index"))

@app.post("/toggle/<int:user_id>")
def toggle_user(user_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
        user = cur.fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("index"))

    if user["is_active"]:
        # stop
        stop_runner(user_id)
        with db.cursor() as cur:
            cur.execute("UPDATE users SET is_active=0 WHERE id=%s", (user_id,))
        flash("Stopped.", "success")
    else:
        # start
        start_runner(user_id)
        with db.cursor() as cur:
            cur.execute("UPDATE users SET is_active=1 WHERE id=%s", (user_id,))
        flash("Started.", "success")

    return redirect(url_for("index"))

@app.post("/delete/<int:user_id>")
def delete_user(user_id):
    stop_runner(user_id)
    db = get_db()
    with db.cursor() as cur:
        cur.execute("DELETE FROM logs WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    flash("User deleted.", "success")
    return redirect(url_for("index"))

@app.get("/logs")
def api_logs():
    """Return recent logs as JSON for the UI poller."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT l.id, l.user_id, l.timestamp, l.message, u.username as user "
            "FROM logs l LEFT JOIN users u ON u.id=l.user_id "
            "ORDER BY l.id DESC LIMIT 200"
        )
        rows = cur.fetchall()
    return jsonify(rows)

if __name__ == "__main__":
    # In dev: reloader spawns another process; keep it off to avoid runner duplication
    app.run(host="0.0.0.0", port=5005, debug=False)
