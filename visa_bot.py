import os
import sys
import time
import json
import random
import requests
import configparser
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from embassy import *

# -------------------- CONFIG --------------------
config = configparser.ConfigParser()
config.read('config.ini')

# Personal Info
USERNAME = config['PERSONAL_INFO']['USERNAME']
PASSWORD = config['PERSONAL_INFO']['PASSWORD']
SCHEDULE_ID = config['PERSONAL_INFO']['SCHEDULE_ID']
PRIOD_START = config['PERSONAL_INFO']['PRIOD_START']
PRIOD_END = config['PERSONAL_INFO']['PRIOD_END']
YOUR_EMBASSY = config['PERSONAL_INFO']['YOUR_EMBASSY']
EMBASSY = Embassies[YOUR_EMBASSY][0]
FACILITY_ID = Embassies[YOUR_EMBASSY][1]
REGEX_CONTINUE = Embassies[YOUR_EMBASSY][2]

# Notification
SENDGRID_API_KEY = config['NOTIFICATION']['SENDGRID_API_KEY']
PUSHOVER_TOKEN = config['NOTIFICATION']['PUSHOVER_TOKEN']
PUSHOVER_USER = config['NOTIFICATION']['PUSHOVER_USER']
PERSONAL_SITE_USER = config['NOTIFICATION']['PERSONAL_SITE_USER']
PERSONAL_SITE_PASS = config['NOTIFICATION']['PERSONAL_SITE_PASS']
PUSH_TARGET_EMAIL = config['NOTIFICATION']['PUSH_TARGET_EMAIL']
PERSONAL_PUSHER_URL = config['NOTIFICATION']['PERSONAL_PUSHER_URL']

# Time
minute = 60
hour = 60 * minute
STEP_TIME = 0.5
RETRY_TIME_L_BOUND = int(config['TIME'].getfloat('RETRY_TIME_L_BOUND'))
RETRY_TIME_U_BOUND = int(config['TIME'].getfloat('RETRY_TIME_U_BOUND'))
WORK_LIMIT_TIME = config['TIME'].getfloat('WORK_LIMIT_TIME')
WORK_COOLDOWN_TIME = config['TIME'].getfloat('WORK_COOLDOWN_TIME')
BAN_COOLDOWN_TIME = config['TIME'].getfloat('BAN_COOLDOWN_TIME')

# Chrome Driver
LOCAL_USE = config['CHROMEDRIVER'].getboolean('LOCAL_USE')
HUB_ADDRESS = config['CHROMEDRIVER']['HUB_ADDRESS']

# URLs
SIGN_IN_LINK = f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_in"
APPOINTMENT_URL = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment"
DATE_URL = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false"
TIME_URL = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false"
SIGN_OUT_LINK = f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_out"

JS_SCRIPT = (
    "var req = new XMLHttpRequest();"
    "req.open('GET', '%s', false);"
    "req.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');"
    "req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');"
    "req.setRequestHeader('Cookie', '_yatri_session=%s');"
    "req.send(null);"
    "return req.responseText;"
)

# -------------------- DRIVER INIT (single place) --------------------
def make_driver():
    opts = Options()
    # Reliable headless on servers
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--remote-allow-origins=*")
    opts.add_argument("--window-size=1280,2000")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-data-dir=/tmp/selenium_{random.randint(1000,9999)}")
    opts.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


    if LOCAL_USE:
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    else:
        return webdriver.Remote(command_executor=HUB_ADDRESS, options=opts)

driver = make_driver()
print("Chrome session started:", driver.session_id, flush=True)
print("User-Agent:", driver.execute_script("return navigator.userAgent;"), flush=True)

# -------------------- HELPERS --------------------
def send_notification(title, msg):
    print(f"Sending notification: {title}")
    # SendGrid Email
    if SENDGRID_API_KEY:
        message = Mail(from_email=USERNAME, to_emails=USERNAME, subject=title, html_content=msg)
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            print("SendGrid Response:", response.status_code)
        except Exception as e:
            print("SendGrid Error:", e)
    # Pushover
    if PUSHOVER_TOKEN:
        try:
            url = "https://api.pushover.net/1/messages.json"
            data = {"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER, "message": msg}
            requests.post(url, data, timeout=15)
        except Exception as e:
            print("Pushover Error:", e)
    # Personal Pusher
    if PERSONAL_SITE_USER and PERSONAL_PUSHER_URL:
        try:
            data = {
                "title": "VISA - " + str(title),
                "user": PERSONAL_SITE_USER,
                "pass": PERSONAL_SITE_PASS,
                "email": PUSH_TARGET_EMAIL,
                "msg": msg,
            }
            requests.post(PERSONAL_PUSHER_URL, data, timeout=15)
        except Exception as e:
            print("Personal Pusher Error:", e)

def info_logger(file_path, log):
    with open(file_path, "a", encoding="utf-8") as file:
        file.write(str(datetime.now().time()) + ":\n" + log + "\n")

def dump_failure(prefix: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = f"{prefix}_{ts}.png"
    html = f"{prefix}_{ts}.html"
    try:
        driver.save_screenshot(png)
    except Exception:
        pass
    try:
        Path("html_dump").mkdir(exist_ok=True)
        with open(Path("html_dump") / html, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass
    return png, html

# -------------------- CORE ACTIONS --------------------
def auto_action(label, find_by, el_type, action, value, sleep_time=0):
    print("\t"+ label +":", end="")
    try:
        match find_by.lower():
            case 'id':
                item = driver.find_element(By.ID, el_type)
            case 'name':
                item = driver.find_element(By.NAME, el_type)
            case 'class':
                item = driver.find_element(By.CLASS_NAME, el_type)
            case 'xpath':
                item = driver.find_element(By.XPATH, el_type)
            case _:
                return 0
        match action.lower():
            case 'send':
                item.send_keys(value)
            case 'click':
                item.click()
            case _:
                return 0
        print("\t\tCheck!")
        if sleep_time:
            time.sleep(sleep_time)
    except Exception as e:
        print(f"\t\tskipped ({e})")

def _find_login_inputs():
    # wait until at least one locator appears
    Wait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, '//*[@name="user[email]" or @id="user_email"]'))
    )
    email_el, pass_el = None, None
    for how, sel in [(By.NAME, 'user[email]'), (By.ID, 'user_email')]:
        try:
            email_el = driver.find_element(how, sel)
            break
        except Exception:
            pass
    for how, sel in [(By.NAME, 'user[password]'), (By.ID, 'user_password')]:
        try:
            pass_el = driver.find_element(how, sel)
            break
        except Exception:
            pass
    if not email_el or not pass_el:
        raise RuntimeError("Login inputs not found. See signin dump.")
    return email_el, pass_el

def start_process():
    driver.get(SIGN_IN_LINK)
    time.sleep(STEP_TIME)
    print(">> Page title:", driver.title, flush=True)
    print(">> Current URL:", driver.current_url, flush=True)
    # initial dump for debugging
    try:
        driver.save_screenshot("01_signin_loaded.png")
        with open("01_signin_loaded.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass

    # cookie/consent banners (best-effort)
    for xpath in [
        '//button[contains(.,"Accept")]',
        '//button[contains(.,"I Agree")]',
        '//button[contains(@id,"accept")]',
        '//input[@type="submit" and contains(@value,"Accept")]',
        '//button[contains(.,"OK")]',
    ]:
        try:
            btn = Wait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            btn.click(); time.sleep(0.3)
            print(">> Cookie banner clicked:", xpath)
            break
        except Exception:
            pass

    try:
        email_el, pass_el = _find_login_inputs()
        email_el.clear(); email_el.send_keys(USERNAME)
        pass_el.clear();  pass_el.send_keys(PASSWORD)
        print(">> Filled email & password", flush=True)

        # privacy checkbox (if present)
        clicked_policy = False
        for sel in [
            '//input[@type="checkbox"]',
            '//input[contains(@id,"policy")]',
            '//input[contains(@name,"policy")]'
        ]:
            try:
                driver.find_element(By.XPATH, sel).click()
                clicked_policy = True
                print(">> Policy checkbox clicked")
                break
            except Exception:
                pass
        if not clicked_policy:
            print(">> Policy checkbox not found (maybe not required)")

        # submit
        try:
            Wait(driver, 15).until(EC.element_to_be_clickable((By.NAME, "commit"))).click()
        except Exception:
            driver.execute_script('const b=document.querySelector("[name=commit]"); if(b) b.click();')
        print(">> Submitted login form", flush=True)

        # Continue anchor (exact text may vary by region)
        continue_text = REGEX_CONTINUE or "Continue"
        print(f">> Waiting for continue: '{continue_text}'", flush=True)
        try:
            Wait(driver, 60).until(
                EC.presence_of_element_located(
                    (By.XPATH,
                     f"//a[contains(normalize-space(.), '{continue_text}') "
                     f"or contains(normalize-space(.), 'Continue >') "
                     f"or contains(normalize-space(.), 'Continue')]")
                )
            )
        except Exception:
            # fallback: any schedule/appointment link after login
            Wait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//a[contains(@href,'/schedule/') or contains(@href,'/appointment')]")
                )
            )

        print(">> Login success signals detected", flush=True)
        print("\n\tLogin successful!\n")

    except Exception as e:
        png, html = dump_failure("login_fail")
        raise RuntimeError(f"Login failed: {e}. Screenshot:{png} HTML:{html}")

def get_date():
    sess = driver.get_cookie("_yatri_session")
    if not sess:
        raise RuntimeError("Missing _yatri_session cookie after login")
    script = JS_SCRIPT % (str(DATE_URL), sess["value"])
    content = driver.execute_script(script)
    if not content or not content.strip().startswith('['):
        png, html = dump_failure("dates_fetch_fail")
        raise RuntimeError(f"DATE_URL returned non-JSON: {content[:200]!r} (dump: {png} {html})")
    return json.loads(content)

def get_time(date):
    sess = driver.get_cookie("_yatri_session")
    if not sess:
        raise RuntimeError("Missing _yatri_session cookie before time fetch")
    time_url = TIME_URL % date
    script = JS_SCRIPT % (str(time_url), sess["value"])
    content = driver.execute_script(script)
    if not content or not content.strip().startswith('{'):
        png, html = dump_failure("times_fetch_fail")
        raise RuntimeError(f"TIME_URL non-JSON for {date}: {content[:200]!r} (dump: {png} {html})")
    data = json.loads(content)
    times = data.get("available_times") or []
    if not times:
        raise RuntimeError(f"No available times for {date}")
    # choose earliest slot (safer)
    time_slot = sorted(times)[0]
    print(f"Got time successfully! {date} {time_slot}")
    return time_slot

def reschedule(date):
    try:
        time_slot = get_time(date)
    except Exception as e:
        return ["FAIL", f"Failed to get time: {e}"]

    driver.get(APPOINTMENT_URL)
    headers = {
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
        "Referer": APPOINTMENT_URL,
        "Cookie": "_yatri_session=" + driver.get_cookie("_yatri_session")["value"]
    }
    data = {
        "utf8": driver.find_element(By.NAME, 'utf8').get_attribute('value'),
        "authenticity_token": driver.find_element(By.NAME, 'authenticity_token').get_attribute('value'),
        "confirmed_limit_message": driver.find_element(By.NAME, 'confirmed_limit_message').get_attribute('value'),
        "use_consulate_appointment_capacity": driver.find_element(By.NAME, 'use_consulate_appointment_capacity').get_attribute('value'),
        "appointments[consulate_appointment][facility_id]": FACILITY_ID,
        "appointments[consulate_appointment][date]": date,
        "appointments[consulate_appointment][time]": time_slot,
    }
    r = requests.post(APPOINTMENT_URL, headers=headers, data=data, timeout=30)
    if 'Successfully Scheduled' in r.text:
        return ["SUCCESS", f"Rescheduled Successfully! {date} {time_slot}"]
    return ["FAIL", f"Reschedule Failed! {date} {time_slot}"]

def get_available_date(dates):
    PED = datetime.strptime(PRIOD_END, "%Y-%m-%d")
    PSD = datetime.strptime(PRIOD_START, "%Y-%m-%d")
    for d in dates:
        date_str = d.get('date')
        if not date_str:
            continue
        new_date = datetime.strptime(date_str, "%Y-%m-%d")
        # inclusive bounds (<= >=)
        if PSD <= new_date <= PED:
            return date_str
    return None

# -------------------- MAIN LOOP --------------------
if __name__ == "__main__":
    first_loop = True
    Req_count = 0
    while True:
        LOG_FILE_NAME = "log_" + str(datetime.now().date()) + ".txt"
        if first_loop:
            start_process()
            first_loop = False

        Req_count += 1
        try:
            msg = "-" * 60 + f"\nRequest count: {Req_count}, Log time: {datetime.today()}\n"
            print(msg)
            info_logger(LOG_FILE_NAME, msg)

            dates = get_date()
            if not dates:
                msg = f"No available dates! Sleeping {RETRY_TIME_L_BOUND}-{RETRY_TIME_U_BOUND} seconds before retry..."
                print(msg)
                info_logger(LOG_FILE_NAME, msg)
                time.sleep(random.randint(RETRY_TIME_L_BOUND, RETRY_TIME_U_BOUND))
                continue

            # Log all available dates
            all_dates_str = ", ".join([d.get('date') for d in dates if d.get('date')])
            msg = "All available dates:\n" + all_dates_str
            print(msg)
            info_logger(LOG_FILE_NAME, msg)

            # Check if any date is in our period
            date_in_period = get_available_date(dates)
            if date_in_period:
                END_MSG_TITLE, final_msg = reschedule(date_in_period)
                print(final_msg)
                info_logger(LOG_FILE_NAME, final_msg)
                send_notification(END_MSG_TITLE, final_msg)
                break  # stop if successful
            else:
                msg = f"No available dates in your period ({PRIOD_START} - {PRIOD_END}), retrying..."
                print(msg)
                info_logger(LOG_FILE_NAME, msg)

            # Retry wait
            time.sleep(random.randint(RETRY_TIME_L_BOUND, RETRY_TIME_U_BOUND))

        except Exception as e:
            msg = f"Exception occurred: {e}"
            print(msg)
            info_logger(LOG_FILE_NAME, msg)
            send_notification("EXCEPTION", msg)
            time.sleep(RETRY_TIME_U_BOUND)
            continue

    # graceful sign-out
    try:
        driver.get(SIGN_OUT_LINK)
    except Exception:
        pass
    try:
        driver.quit()
    except Exception:
        pass
