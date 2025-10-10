import os
import time
import json
import random
import threading
from threading import Lock
import csv
import smtplib
import traceback
from datetime import datetime
from collections import defaultdict, deque
from queue import Queue, Empty
import re

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By

from flask import Flask, request, jsonify, render_template, Response, stream_with_context
from flask_cors import CORS, cross_origin

from embassy import Embassies  # your mapping

# ===================== FLASK =====================
app = Flask(__name__, template_folder="templates")
# DEV: open CORS, PROD: tighten to your IP/origin
CORS(app, resources={r"/submit": {"origins": "*"}, r"/stream": {"origins": "*"}})

# ===================== CONFIG =====================
PRIOD_START_DEFAULT = "2025-12-01"
PRIOD_END_DEFAULT = "2025-12-20"
CSV_FILE = "visa_appointments.csv"
LOG_FILE = f"log_{datetime.now().date()}.txt"

# SMTP from ENV (do NOT hardcode in code)
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")      # e.g., "your@gmail.com"
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # e.g., "app_password"

# Debug dumps (screenshots + HTML on exception)
DEBUG_DUMPS = True

# File write locks (thread-safe)
_file_lock = Lock()
_log_lock = Lock()

# ===================== SSE PUBSUB =====================
_subscribers = defaultdict(list)     # user_key -> [Queue, ...]
MAX_RECENT = 200
_recent_events = defaultdict(lambda: deque(maxlen=MAX_RECENT))  # user_key -> deque json strings


def _user_key(username: str) -> str:
    return (username or "").strip().lower()


def _push_event(username: str, payload: dict):
    """Broadcast a dict payload to all EventSource clients of this user."""
    key = _user_key(username)
    msg = json.dumps(payload, ensure_ascii=False)
    _recent_events[key].append(msg)
    dead = []
    for q in _subscribers[key]:
        try:
            q.put_nowait(msg)
        except Exception:
            dead.append(q)
    if dead:
        _subscribers[key] = [q for q in _subscribers[key] if q not in dead]


# ===================== LOG & CSV =====================
def log_info(user, msg):
    line = f"[{user}] {msg}"
    print(line, flush=True)
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {line}\n")


def dump_debug(driver, prefix="debug"):
    if not DEBUG_DUMPS:
        return
    try:
        os.makedirs("debug", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        driver.save_screenshot(f"debug/{prefix}_{ts}.png")
        with open(f"debug/{prefix}_{ts}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass  # best effort


def _mask(s: str) -> str:
    """Mask sensitive strings for CSV/logs."""
    if not s:
        return ""
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def save_to_csv(data, status, result_msg):
    row = {
        "username": data.get("username", ""),
        # Mask password to avoid storing plaintext
        "password": _mask(data.get("password", "")),
        "schedule_id": data.get("schedule_id", ""),
        "embassy": data.get("embassy", ""),
        "period_start": data.get("period_start", PRIOD_START_DEFAULT),
        "period_end": data.get("period_end", PRIOD_END_DEFAULT),
        "status": status,
        "result": result_msg,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with _file_lock:
        write_header = not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "username", "password", "schedule_id", "embassy",
                    "period_start", "period_end", "status", "result", "timestamp"
                ]
            )
            if write_header:
                writer.writeheader()
            writer.writerow(row)


def send_email(data, status, result_msg):
    if not (SMTP_EMAIL and SMTP_PASSWORD):
        log_info(data.get("username", "?"), "SMTP creds missing; skipping email.")
        return
    subject = f"Visa Appointment Status: {status}"
    body = f"""Appointment Details:
Username: {data.get('username')}

Embassy: {data.get('embassy')}
Schedule ID: {data.get('schedule_id')}
Period: {data.get('period_start')} to {data.get('period_end')}
Status: {status}
Result: {result_msg}
Timestamp: {datetime.now():%Y-%m-%d %H:%M:%S}
"""
    msg = f"Subject: {subject}\nFrom: {SMTP_EMAIL}\nTo: {SMTP_EMAIL}\n\n{body}"
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            # send to yourself by default; change to user if needed
            server.sendmail(SMTP_EMAIL, SMTP_EMAIL, msg)
        log_info(data.get("username", "?"), f"Email sent to '{SMTP_EMAIL}'")
    except Exception as e:
        log_info(data.get("username", "?"), f"Email sending failed: {e}")


# ===================== LINKS & JS =====================
def make_links(embassy, schedule_id, facility_id):
    base = f"https://ais.usvisa-info.com/{embassy}/niv"
    return {
        "SIGN_IN_LINK": f"{base}/users/sign_in",
        "APPOINTMENT_URL": f"{base}/schedule/{schedule_id}/appointment",
        "DATE_URL": f"{base}/schedule/{schedule_id}/appointment/days/{facility_id}.json?appointments[expedite]=false",
        "TIME_URL_TPL": f"{base}/schedule/{schedule_id}/appointment/times/{{facility}}.json?date=%s&appointments[expedite]=false".replace("{facility}", str(facility_id)),
        "SIGN_OUT_LINK": f"{base}/users/sign_out"
    }


JS_SCRIPT = (
    "var req = new XMLHttpRequest();"
    "req.open('GET', '%s', false);"
    "req.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');"
    "req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');"
    "req.setRequestHeader('Cookie', '_yatri_session=%s');"
    "req.send(null);"
    "return req.responseText;"
)

# ===================== SELENIUM HELPERS =====================
def auto_action(driver, label, find_by, el, action, value="", sleep_time=0):
    print(f"\t{label}:", end="", flush=True)
    if find_by == "id":
        item = driver.find_element(By.ID, el)
    elif find_by == "name":
        item = driver.find_element(By.NAME, el)
    elif find_by == "class":
        item = driver.find_element(By.CLASS_NAME, el)
    elif find_by == "xpath":
        item = driver.find_element(By.XPATH, el)
    else:
        print(" BAD_LOCATOR")
        return
    if action == "send":
        item.clear()
        item.send_keys(value)
    elif action == "click":
        item.click()
    else:
        print(" BAD_ACTION")
        return
    print(" OK")
    if sleep_time:
        time.sleep(sleep_time)


def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")                  # if running as root
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--disable-features=BlinkGenPropertyTrees")
    chrome_options.binary_location = "/usr/bin/google-chrome"    # adjust if different
    service = Service()  # Selenium Manager resolves chromedriver
    return webdriver.Chrome(service=service, options=chrome_options)


def start_process(driver, username, password, regex_continue, sign_in_link, step_time=0.5):
    driver.get(sign_in_link)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))
    # optional arrow; ignore if not present
    try:
        auto_action(driver, "Bounce", "xpath", '//a[contains(@class,"down-arrow")]', "click", "", step_time)
    except Exception:
        pass
    auto_action(driver, "Email", "id", "user_email", "send", username, step_time)
    auto_action(driver, "Password", "id", "user_password", "send", password, step_time)
    # privacy checkbox: stable selector first, fallback second
    try:
        driver.find_element(By.CSS_SELECTOR, "input#policy_confirmed").click()
    except Exception:
        try:
            auto_action(driver, "Privacy-fb", "class", "icheckbox", "click", "", step_time)
        except Exception:
            pass
    auto_action(driver, "Enter Panel", "name", "commit", "click", "", step_time)
    Wait(driver, 60).until(EC.presence_of_element_located((By.XPATH, f"//a[contains(text(), '{regex_continue}')]")))


def _require_session_cookie(driver, who=""):
    c = driver.get_cookie("_yatri_session")
    if not c or "value" not in c:
        raise RuntimeError(f"_yatri_session cookie missing ({who}) — login likely failed")
    return c["value"]


def get_date(driver, date_url):
    session = _require_session_cookie(driver, "get_date")
    script = JS_SCRIPT % (str(date_url), session)
    try:
        raw = driver.execute_script(script)
        data = json.loads(raw) if raw else []
        if isinstance(data, dict) and "available_dates" in data:
            # some endpoints wrap in a key
            return data.get("available_dates") or []
        return data or []
    except Exception as e:
        raise RuntimeError(f"get_date JSON parse failed: {e}")


def get_time(driver, date, time_url_tpl):
    session = _require_session_cookie(driver, "get_time")
    time_url = time_url_tpl % date
    script = JS_SCRIPT % (str(time_url), session)
    raw = driver.execute_script(script)
    try:
        data = json.loads(raw) if raw else {}
        times = data.get("available_times") or []
        return times[-1] if times else None
    except Exception as e:
        raise RuntimeError(f"get_time JSON parse failed: {e}")


def reschedule(driver, date, facility_id, appointment_url, time_url_tpl):
    time_slot = get_time(driver, date, time_url_tpl)
    if not time_slot:
        return "FAIL", f"No time available for {date}"
    driver.get(appointment_url)
    try:
        import requests
        headers = {
            "User-Agent": driver.execute_script("return navigator.userAgent;"),
            "Referer": appointment_url,
            "Cookie": "_yatri_session=" + _require_session_cookie(driver, "reschedule")
        }
        data = {
            "utf8": driver.find_element(By.NAME, 'utf8').get_attribute('value'),
            "authenticity_token": driver.find_element(By.NAME, 'authenticity_token').get_attribute('value'),
            "confirmed_limit_message": driver.find_element(By.NAME, 'confirmed_limit_message').get_attribute('value'),
            "use_consulate_appointment_capacity": driver.find_element(By.NAME, 'use_consulate_appointment_capacity').get_attribute('value'),
            "appointments[consulate_appointment][facility_id]": facility_id,
            "appointments[consulate_appointment][date]": date,
            "appointments[consulate_appointment][time]": time_slot,
        }
        r = requests.post(appointment_url, headers=headers, data=data, timeout=30)
        if "Successfully Scheduled" in r.text:
            return "SUCCESS", f"Rescheduled Successfully! {date} {time_slot}"
        return "FAIL", f"Reschedule Failed {date} {time_slot}"
    except Exception as e:
        return "EXCEPTION", str(e)


# ===================== ERROR CLASSIFIER =====================
INVALID_SESSION_PAT = re.compile(
    r"invalid session id|target window already closed|web view not found|disconnected: not connected to DevTools",
    re.I
)


def classify_error(text: str) -> str:
    if INVALID_SESSION_PAT.search(text or ""):
        return "INVALID_SESSION"
    return "GENERIC"


# ===================== THREAD TASK =====================
def process_user(user_data):
    username = user_data.get("username", "")
    password = user_data.get("password", "")
    schedule_id = user_data.get("schedule_id", "")
    embassy_key = user_data.get("embassy", "")
    period_start = user_data.get("period_start", PRIOD_START_DEFAULT)
    period_end = user_data.get("period_end", PRIOD_END_DEFAULT)

    embassy_info = Embassies.get(embassy_key, ["en-ca", 95, "Continue"])
    embassy, facility_id, regex_continue = embassy_info
    links = make_links(embassy, schedule_id, facility_id)

    SIGN_IN_LINK = links["SIGN_IN_LINK"]
    APPOINTMENT_URL = links["APPOINTMENT_URL"]
    DATE_URL = links["DATE_URL"]
    TIME_URL_TPL = links["TIME_URL_TPL"]
    SIGN_OUT_LINK = links["SIGN_OUT_LINK"]

    driver = None
    first_loop = True
    req_count = 0
    retry_time_l_bound = 10
    retry_time_u_bound = 120
    ban_cooldown_time = 5 * 3600  # 5 hours

    try:
        driver = create_driver()
        _push_event(username, {"type": "STATUS", "text": "Driver started."})

        while True:
            try:
                if first_loop:
                    _push_event(username, {"type": "STATUS", "text": "Signing in..."})
                    start_process(driver, username, password, regex_continue, SIGN_IN_LINK)
                    log_info(username, "Login successful.")
                    _push_event(username, {"type": "STATUS", "text": "Login successful."})
                    first_loop = False

                req_count += 1
                log_info(username, f"Request #{req_count}")
                _push_event(username, {"type": "TICK", "text": f"Polling (#{req_count})..."})

                dates_payload = get_date(driver, DATE_URL)
                # normalize to list of "YYYY-MM-DD"
                if dates_payload and isinstance(dates_payload, list) and isinstance(dates_payload[0], dict) and "date" in dates_payload[0]:
                    available_dates = [d.get('date') for d in dates_payload]
                elif isinstance(dates_payload, list):
                    available_dates = dates_payload
                else:
                    available_dates = []

                log_info(username, f"Available Dates: {', '.join(available_dates) if available_dates else 'NONE'}")
                _push_event(username, {"type": "DATES_RAW", "dates": available_dates})

                if not available_dates:
                    msg = "No dates found"
                    log_info(username, f"{msg}. Cooling down.")
                    save_to_csv(user_data, "FAIL", msg)
                    _push_event(username, {"type": "NO_DATES", "text": msg})
                    time.sleep(ban_cooldown_time)
                    first_loop = True
                    continue

                psd = datetime.strptime(period_start, "%Y-%m-%d")
                ped = datetime.strptime(period_end, "%Y-%m-%d")
                valid_dates = [d for d in available_dates if psd <= datetime.strptime(d, "%Y-%m-%d") <= ped]

                if valid_dates:
                    date = valid_dates[0]
                    _push_event(username, {"type": "DATES", "dates": valid_dates, "picked": date})
                    status, msg = reschedule(driver, date, facility_id, APPOINTMENT_URL, TIME_URL_TPL)
                    log_info(username, f"{status} | {msg}")
                    save_to_csv(user_data, status, msg)

                    if status == "SUCCESS":
                        _push_event(username, {"type": "SUCCESS", "text": msg, "date": date})
                        send_email(user_data, status, msg)
                    else:
                        _push_event(username, {"type": "FAIL", "text": msg, "date": date})
                    break
                else:
                    msg = "No valid dates in range"
                    log_info(username, msg)
                    save_to_csv(user_data, "FAIL", msg)
                    _push_event(username, {"type": "NO_VALID", "text": msg})

                retry_wait_time = random.randint(retry_time_l_bound, retry_time_u_bound)
                _push_event(username, {"type": "SLEEP", "seconds": retry_wait_time})
                time.sleep(retry_wait_time)

            except Exception as e:
                dump_debug(driver, prefix=username)
                tb = traceback.format_exc()
                log_info(username, f"Exception: {e}\n{tb}")
                save_to_csv(user_data, "EXCEPTION", str(e))
                _push_event(username, {
                    "type": "ERROR",
                    "kind": classify_error(f"{e}\n{tb}"),
                    "text": f"{e}",
                })
                break

    finally:
        try:
            if driver:
                try:
                    driver.get(SIGN_OUT_LINK)
                except Exception:
                    pass
                driver.quit()
        except Exception:
            pass
        log_info(username, "Session finished.")
        _push_event(username, {"type": "DONE", "text": "Session finished."})


# ===================== FLASK ROUTES =====================
@app.route('/')
def serve_index():
    try:
        return render_template('index.html')
    except Exception:
        return "OK", 200


@app.route('/submit', methods=['POST', 'OPTIONS'])
@cross_origin()
def submit():
    if request.method == 'OPTIONS':
        return '', 200

    data = request.json or {}
    num_students = int(data.get("num_students", 1))
    students = data.get("students", [])

    if len(students) != num_students:
        return jsonify({"error": "Invalid number of students provided"}), 400

    # Non-blocking: daemon threads; return immediately
    for student in students:
        t = threading.Thread(target=process_user, args=(student,), daemon=True)
        t.start()
        time.sleep(2)  # slight stagger to avoid simultaneous login

    return jsonify({"message": "Processing started", "count": len(students)}), 202


@app.get("/stream")
def stream():
    """
    Subscribe to live events for a user:
    /stream?username=someone@example.com
    """
    username = request.args.get("username", "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400

    key = _user_key(username)
    q = Queue(maxsize=1000)
    _subscribers[key].append(q)

    def gen():
        # send recent backlog first so the card can hydrate
        for msg in list(_recent_events[key]):
            yield f"data: {msg}\n\n"

        # then stream new events
        try:
            while True:
                try:
                    msg = q.get(timeout=20)
                    yield f"data: {msg}\n\n"
                except Empty:
                    # keep-alive
                    yield "data: {\"type\":\"PING\"}\n\n"
        finally:
            # unsubscribe on disconnect
            try:
                _subscribers[key].remove(q)
            except ValueError:
                pass

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # for nginx
    }
    return Response(stream_with_context(gen()), headers=headers)


# ===================== MAIN =====================
if __name__ == "__main__":
    # DEV only. In prod use gunicorn/uwsgi behind nginx (disable buffering for /stream).
    app.run(host="0.0.0.0", port=5000, threaded=True)
