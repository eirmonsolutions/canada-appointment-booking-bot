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

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By

from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS, cross_origin

from embassy import Embassies  # your mapping
from queue import Queue, Empty
import logging

# ===================== FLASK =====================
app = Flask(__name__)
CORS(app, resources={r"/submit": {"origins": "*"}})

# ===================== CONFIG =====================
PRIOD_START_DEFAULT = "2027-01-01"
PRIOD_END_DEFAULT = "2027-12-20"
CSV_FILE = "visa_appointments.csv"
LOG_FILE = f"log_{datetime.now().date()}.txt"

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = "manshusmartboy@gmail.com"
SMTP_PASSWORD = "cvvrefpzcxkqahen"

DEBUG_DUMPS = True

_file_lock = Lock()
_log_lock = Lock()

# ===================== LISTENERS =====================
_listeners = set()
_listeners_lock = Lock()

def _register_listener():
    q = Queue()
    with _listeners_lock:
        _listeners.add(q)
    return q

def _remove_listener(q):
    with _listeners_lock:
        _listeners.discard(q)

def _broadcast(msg: str):
    with _listeners_lock:
        dead = []
        for q in list(_listeners):
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _listeners.discard(q)

def log_info(user, msg):
    line = f"[{user}] {msg}"
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {line}\n")
    _broadcast(line)

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
        pass

def save_to_csv(data, status, result_msg):
    row = {
        "username": data.get("username", ""),
        "password": data.get("password", ""),
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
            writer = csv.DictWriter(f, fieldnames=["username","password","schedule_id","embassy",
                                                   "period_start","period_end","status","result","timestamp"])
            if write_header:
                writer.writeheader()
            writer.writerow(row)

def send_email(data, status, result_msg):
    if not (SMTP_EMAIL and SMTP_PASSWORD):
        log_info(data.get("username","?"), "SMTP creds missing; skipping email.")
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
    msg = f"Subject: {subject}\nFrom: {SMTP_EMAIL}\nTo: manshu.developer@gmail.com\n\n{body}"
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, 'manshu.developer@gmail.com', msg)
        log_info(data.get("username","?"), "Email sent to 'manshu.developer@gmail.com'")
    except Exception as e:
        log_info(data.get("username","?"), f"Email sending failed: {e}")

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
def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--lang=en-US,en")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--enable-javascript")
    chrome_options.page_load_strategy = "eager"

    chrome_options.binary_location = "/usr/bin/google-chrome"
    service = Service()
    driver = webdriver.Chrome(service=service, options=chrome_options)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"}
    )
    return driver

def _wait_cloudflare(driver, max_seconds=30):
    end = time.time() + max_seconds
    while time.time() < end:
        title = (driver.title or "").lower()
        body_text = (driver.page_source or "").lower()
        if "just a moment" in title or "checking your browser" in body_text:
            time.sleep(1)
            continue
        return

def _accept_cookies_if_present(driver):
    try:
        btn = driver.find_element(By.ID, "onetrust-accept-btn-handler")
        btn.click()
        time.sleep(0.5)
        return
    except Exception:
        pass
    try:
        el = driver.find_element(By.XPATH, "//button[contains(., 'Accept') or contains(., 'I Agree')]")
        el.click()
        time.sleep(0.5)
    except Exception:
        pass

# --- NEW: Robust iCheck Policy Checkbox ---
def _click_policy_checkbox(driver):
    # Strategy 1: Click the visible iCheck div
    try:
        ichk_div = driver.find_element(By.XPATH, "//div[contains(@class, 'icheckbox') and .//input[@id='policy_confirmed']]")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ichk_div)
        driver.execute_script("arguments[0].click();", ichk_div)
        time.sleep(0.7)
        return True
    except Exception as e:
        log_info("system", f"iCheck div click failed: {e}")

    # Strategy 2: JS force-check input
    try:
        inp = driver.find_element(By.ID, "policy_confirmed")
        driver.execute_script("""
            arguments[0].checked = true;
            arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
            arguments[0].dispatchEvent(new Event('click', {bubbles: true}));
        """, inp)
        time.sleep(0.5)
        return True
    except Exception as e:
        log_info("system", f"JS check failed: {e}")

    # Strategy 3: Click label
    try:
        label = driver.find_element(By.XPATH, "//label[@for='policy_confirmed']")
        driver.execute_script("arguments[0].click();", label)
        time.sleep(0.5)
        return True
    except Exception:
        pass

    return False

def start_process(driver, username, password, regex_continue, sign_in_link, step_time=0.5):
    driver.get(sign_in_link)
    _wait_cloudflare(driver, 45)
    _accept_cookies_if_present(driver)

    try:
        Wait(driver, 30).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "form[action*='sign_in'] input#user_email")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "input#user_email"))
            )
        )
    except Exception:
        dump_debug(driver, prefix="login_wait_failed")
        raise TimeoutError("Login form (email field) did not appear — likely cookie/CF/splash blocking the page.")

    try:
        arrow = driver.find_element(By.XPATH, '//a[contains(@class,"down-arrow") or contains(@class,"accordion")]')
        arrow.click()
        time.sleep(0.3)
    except Exception:
        pass

    email_el = driver.find_element(By.CSS_SELECTOR, "input#user_email")
    pwd_el = driver.find_element(By.CSS_SELECTOR, "input#user_password")
    email_el.clear(); email_el.send_keys(username); time.sleep(step_time)
    pwd_el.clear(); pwd_el.send_keys(password); time.sleep(step_time)

    # --- Policy Checkbox ---
    if not _click_policy_checkbox(driver):
        dump_debug(driver, "policy_fail")
        raise RuntimeError("Could not check policy_confirmed (iCheck checkbox)")

    # --- Submit Button ---
    submit = None
    for locator in [
        (By.CSS_SELECTOR, "form[action*='sign_in'] button[type='submit']"),
        (By.CSS_SELECTOR, "form[action*='sign_in'] input[type='submit']"),
        (By.XPATH, "//button[@type='submit' and (contains(.,'Sign in') or contains(.,'Log in'))]"),
        (By.XPATH, "//input[@type='submit' and (contains(@value,'Sign in') or contains(@value,'Log in'))]"),
        (By.NAME, "commit"),
    ]:
        try:
            submit = driver.find_element(*locator)
            break
        except Exception:
            continue

    if not submit:
        dump_debug(driver, prefix="login_no_submit")
        raise TimeoutError("Could not find the Sign In submit button.")

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", submit)
        submit.click()
    except Exception:
        driver.execute_script("arguments[0].click();", submit)

    time.sleep(3)
    page_text = driver.page_source.lower()
    if any(x in page_text for x in ["hcaptcha", "captcha", "verify you are human", "security check"]):
        dump_debug(driver, prefix="captcha_detected")
        raise RuntimeError("hCaptcha or anti-bot challenge detected. Manual intervention or proxy rotation needed.")

    # --- Dashboard Detection ---
    try:
        Wait(driver, 60).until(
            EC.presence_of_element_located((By.XPATH, f"//a[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{regex_continue.lower()}')]"))
        )
    except Exception:
        try:
            Wait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'appointment') or contains(., 'Continue') or contains(., 'Schedule')]"))
            )
        except Exception:
            dump_debug(driver, prefix="dashboard_detection_failed")
            raise TimeoutError("Dashboard loaded but no Continue/Appointment link found.")

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

# ===================== THREAD TASK =====================
def process_user(user_data):
    username = user_data["username"]
    password = user_data["password"]
    schedule_id = user_data["schedule_id"]
    embassy_key = user_data["embassy"]
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
    ban_cooldown_time = 5 * 3600

    try:
        driver = create_driver()

        while True:
            try:
                if first_loop:
                    start_process(driver, username, password, regex_continue, SIGN_IN_LINK)
                    log_info(username, "Login successful.")
                    first_loop = False

                req_count += 1
                log_info(username, f"Request #{req_count}")
                dates_payload = get_date(driver, DATE_URL)
                if dates_payload and isinstance(dates_payload, list) and isinstance(dates_payload[0], dict) and "date" in dates_payload[0]:
                    available_dates = [d.get('date') for d in dates_payload]
                elif isinstance(dates_payload, list):
                    available_dates = dates_payload
                else:
                    available_dates = []

                log_info(username, f"Available Dates: {', '.join(available_dates) if available_dates else 'NONE'}")

                if not available_dates:
                    log_info(username, f"No dates found. Sleeping {ban_cooldown_time/3600} hours...")
                    save_to_csv(user_data, "FAIL", "No dates found")
                    time.sleep(ban_cooldown_time)
                    first_loop = True
                    continue

                psd = datetime.strptime(period_start, "%Y-%m-%d")
                ped = datetime.strptime(period_end, "%Y-%m-%d")
                valid_dates = [d for d in available_dates if psd <= datetime.strptime(d, "%Y-%m-%d") <= ped]

                if valid_dates:
                    date = valid_dates[0]
                    status, msg = reschedule(driver, date, facility_id, APPOINTMENT_URL, TIME_URL_TPL)
                    log_info(username, f"{status} | {msg}")
                    save_to_csv(user_data, status, msg)
                    if status == "SUCCESS":
                        send_email(user_data, status, msg)
                    break
                else:
                    log_info(username, "No valid dates in range. Retrying...")
                    save_to_csv(user_data, "FAIL", "No valid dates in range")

                retry_wait_time = random.randint(retry_time_l_bound, retry_time_u_bound)
                log_info(username, f"Retry after {retry_wait_time}s")
                time.sleep(retry_wait_time)

            except Exception as e:
                dump_debug(driver, prefix=username)
                log_info(username, f"Exception: {e}\n{traceback.format_exc()}")
                save_to_csv(user_data, "EXCEPTION", str(e))
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

    for student in students:
        t = threading.Thread(target=process_user, args=(student,), daemon=True)
        t.start()
        time.sleep(2)

    return jsonify({"message": "Processing started", "count": len(students)}), 202

@app.route("/logs/stream")
@cross_origin()
def stream_logs():
    q = _register_listener()

    def gen():
        yield "event: hello\ndata: connected\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {msg}\n\n"
                except Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            _remove_listener(q)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"
    }
    return Response(gen(), mimetype="text/event-stream", headers=headers)

# ===================== MAIN =====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5008)