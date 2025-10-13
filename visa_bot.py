import os
import sys
import time
import json
import random
import signal
import requests
import configparser
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from embassy import Embassies

# --------- graceful shutdown ---------
_SHUTDOWN = False
def _handle_stop(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
signal.signal(signal.SIGINT, _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)

# --------- helpers ---------
def log_line(msg: str, file_name: str):
    print(msg, flush=True)
    with open(file_name, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().time()}:\n{msg}\n")

def build_driver(local_use: bool, hub_address: str, headless: bool=True) -> webdriver.Chrome:
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--remote-allow-origins=*")
    chrome_options.add_argument(f"--user-data-dir=/tmp/selenium_{random.randint(1000,9999)}")

    if local_use:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=chrome_options)
    else:
        remote_opts = webdriver.ChromeOptions()
        for arg in chrome_options.arguments:
            remote_opts.add_argument(arg)
        return webdriver.Remote(command_executor=hub_address, options=remote_opts)

def run_bot(cfg_path: str = "config.ini"):
    # -------------------- CONFIG --------------------
    config = configparser.ConfigParser()
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    config.read(cfg_path)

    # Personal Info
    USERNAME      = config['PERSONAL_INFO']['USERNAME']
    PASSWORD      = config['PERSONAL_INFO']['PASSWORD']
    SCHEDULE_ID   = config['PERSONAL_INFO']['SCHEDULE_ID']
    PRIOD_START   = config['PERSONAL_INFO']['PRIOD_START']
    PRIOD_END     = config['PERSONAL_INFO']['PRIOD_END']
    YOUR_EMBASSY  = config['PERSONAL_INFO']['YOUR_EMBASSY']

    # Embassy mapping (allow optional overrides)
    emb_tuple     = Embassies[YOUR_EMBASSY]
    EMBASSY       = emb_tuple[0]
    FACILITY_ID   = config['PERSONAL_INFO'].get('FACILITY_ID', str(emb_tuple[1]))
    REGEX_CONTINUE= config['PERSONAL_INFO'].get('REGEX_CONTINUE', str(emb_tuple[2]))

    # Notification
    SENDGRID_API_KEY   = config['NOTIFICATION'].get('SENDGRID_API_KEY', '')
    PUSHOVER_TOKEN     = config['NOTIFICATION'].get('PUSHOVER_TOKEN', '')
    PUSHOVER_USER      = config['NOTIFICATION'].get('PUSHOVER_USER', '')
    PERSONAL_SITE_USER = config['NOTIFICATION'].get('PERSONAL_SITE_USER', '')
    PERSONAL_SITE_PASS = config['NOTIFICATION'].get('PERSONAL_SITE_PASS', '')
    PUSH_TARGET_EMAIL  = config['NOTIFICATION'].get('PUSH_TARGET_EMAIL', '')
    PERSONAL_PUSHER_URL= config['NOTIFICATION'].get('PERSONAL_PUSHER_URL', '')

    # Time
    STEP_TIME           = 0.5
    RETRY_TIME_L_BOUND  = int(config['TIME'].getfloat('RETRY_TIME_L_BOUND'))
    RETRY_TIME_U_BOUND  = int(config['TIME'].getfloat('RETRY_TIME_U_BOUND'))
    # These are defined but unused in your original. Keep for future:
    WORK_LIMIT_TIME     = config['TIME'].getfloat('WORK_LIMIT_TIME')
    WORK_COOLDOWN_TIME  = config['TIME'].getfloat('WORK_COOLDOWN_TIME')
    BAN_COOLDOWN_TIME   = config['TIME'].getfloat('BAN_COOLDOWN_TIME')

    # Chrome Driver
    LOCAL_USE   = config['CHROMEDRIVER'].getboolean('LOCAL_USE')
    HUB_ADDRESS = config['CHROMEDRIVER'].get('HUB_ADDRESS', '')

    # URLs
    SIGN_IN_LINK    = f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_in"
    APPOINTMENT_URL = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment"
    DATE_URL        = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false"
    TIME_URL        = f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false"
    SIGN_OUT_LINK   = f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_out"

    JS_SCRIPT = ("var req = new XMLHttpRequest();"
                 "req.open('GET', '%s', false);"
                 "req.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');"
                 "req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');"
                 "req.setRequestHeader('Cookie', '_yatri_session=%s');"
                 "req.send(null);"
                 "return req.responseText;")

    LOG_FILE_NAME = f"log_{datetime.now().date()}.txt"

    def send_notification(title, msg):
        log_line(f"Sending notification: {title}", LOG_FILE_NAME)
        # SendGrid
        if SENDGRID_API_KEY:
            try:
                message = Mail(from_email=USERNAME, to_emails=USERNAME, subject=title, html_content=msg)
                sg = SendGridAPIClient(SENDGRID_API_KEY)
                sg.send(message)
            except Exception as e:
                log_line(f"SendGrid Error: {e}", LOG_FILE_NAME)
        # Pushover
        if PUSHOVER_TOKEN:
            try:
                url = "https://api.pushover.net/1/messages.json"
                data = {"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER, "message": msg}
                requests.post(url, data, timeout=15)
            except Exception as e:
                log_line(f"Pushover Error: {e}", LOG_FILE_NAME)
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
                log_line(f"Personal Pusher Error: {e}", LOG_FILE_NAME)

    # Selenium helpers
    def auto_action(label, find_by, el_type, action, value, sleep_time=0):
        try:
            if find_by.lower() == 'id':
                item = driver.find_element(By.ID, el_type)
            elif find_by.lower() == 'name':
                item = driver.find_element(By.NAME, el_type)
            elif find_by.lower() == 'class':
                item = driver.find_element(By.CLASS_NAME, el_type)
            elif find_by.lower() == 'xpath':
                item = driver.find_element(By.XPATH, el_type)
            else:
                return
            if action.lower() == 'send':
                item.send_keys(value)
            elif action.lower() == 'click':
                item.click()
            if sleep_time:
                time.sleep(sleep_time)
            print(f"\t{label}: Check!", flush=True)
        except Exception as e:
            print(f"\t{label}: skipped ({e})", flush=True)

    def start_process():
        driver.get(SIGN_IN_LINK)
        time.sleep(STEP_TIME)
        Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))
        auto_action("Click bounce", "xpath", '//a[contains(@class,"down-arrow")]', "click", "", STEP_TIME)
        auto_action("Email", "id", "user_email", "send", USERNAME, STEP_TIME)
        auto_action("Password", "id", "user_password", "send", PASSWORD, STEP_TIME)
        auto_action("Privacy", "xpath", '//input[@type="checkbox"]', "click", "", STEP_TIME)
        auto_action("Enter Panel", "name", "commit", "click", "", STEP_TIME)
        # Wait for the "Continue" anchor text
        Wait(driver, 60).until(EC.presence_of_element_located((By.XPATH, f"//a[contains(text(), '{REGEX_CONTINUE}')]")))
        log_line("\n\tLogin successful!\n", LOG_FILE_NAME)

    def get_date():
        session = driver.get_cookie("_yatri_session")["value"]
        script = JS_SCRIPT % (str(DATE_URL), session)
        content = driver.execute_script(script)
        return json.loads(content)

    def get_time(date_str):
        session = driver.get_cookie("_yatri_session")["value"]
        time_url = TIME_URL % date_str
        script = JS_SCRIPT % (str(time_url), session)
        content = driver.execute_script(script)
        data = json.loads(content)
        times = data.get("available_times") or []
        if not times:
            raise RuntimeError("No available times for selected date")
        # choose earliest slot instead of last
        time_slot = sorted(times)[0]
        log_line(f"Got time successfully! {date_str} {time_slot}", LOG_FILE_NAME)
        return time_slot

    def reschedule(date_str):
        try:
            time_slot = get_time(date_str)
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
            "appointments[consulate_appointment][date]": date_str,
            "appointments[consulate_appointment][time]": time_slot,
        }
        r = requests.post(APPOINTMENT_URL, headers=headers, data=data, timeout=30)
        if 'Successfully Scheduled' in r.text:
            return ["SUCCESS", f"Rescheduled Successfully! {date_str} {time_slot}"]
        return ["FAIL", f"Reschedule Failed! {date_str} {time_slot}"]

    def get_available_date(dates):
        PED = datetime.strptime(PRIOD_END, "%Y-%m-%d")
        PSD = datetime.strptime(PRIOD_START, "%Y-%m-%d")
        for d in dates:
            date_str = d.get('date')
            if not date_str:
                continue
            new_date = datetime.strptime(date_str, "%Y-%m-%d")
            if PSD <= new_date <= PED:  # inclusive
                return date_str
        return None

    # -------------------- DRIVER INIT --------------------
    HEADLESS = config['CHROMEDRIVER'].get('HEADLESS', 'true').lower() != 'false'
    driver = build_driver(LOCAL_USE, HUB_ADDRESS, headless=HEADLESS)
    print("Chrome session started:", driver.session_id, flush=True)
    print("User-Agent:", driver.execute_script("return navigator.userAgent;"), flush=True)



    # -------------------- MAIN LOOP --------------------
    try:
        first_loop = True
        Req_count = 0
        while not _SHUTDOWN:
            LOG_FILE_NAME_LOOP = f"log_{datetime.now().date()}.txt"  # roll daily
            if first_loop:
                start_process()
                first_loop = False

            Req_count += 1
            try:
                msg = "-" * 60 + f"\nRequest count: {Req_count}, Log time: {datetime.today()}\n"
                log_line(msg, LOG_FILE_NAME_LOOP)

                dates = get_date()
                if not dates:
                    msg = f"No available dates! Sleeping {RETRY_TIME_L_BOUND}-{RETRY_TIME_U_BOUND} seconds before retry..."
                    log_line(msg, LOG_FILE_NAME_LOOP)
                    time.sleep(random.randint(RETRY_TIME_L_BOUND, RETRY_TIME_U_BOUND))
                    continue

                # Log all dates
                all_dates_str = ", ".join([d.get('date') for d in dates if d.get('date')])
                log_line("All available dates:\n" + all_dates_str, LOG_FILE_NAME_LOOP)

                # Pick a date in period
                date_in_period = get_available_date(dates)
                if date_in_period:
                    END_MSG_TITLE, final_msg = reschedule(date_in_period)
                    log_line(final_msg, LOG_FILE_NAME_LOOP)
                    send_notification(END_MSG_TITLE, final_msg)
                    break  # stop if successful
                else:
                    log_line(f"No available dates in your period ({PRIOD_START} - {PRIOD_END}), retrying...", LOG_FILE_NAME_LOOP)

                time.sleep(random.randint(RETRY_TIME_L_BOUND, RETRY_TIME_U_BOUND))

            except Exception as e:
                emsg = f"Exception occurred: {e}"
                log_line(emsg, LOG_FILE_NAME_LOOP)
                send_notification("EXCEPTION", emsg)
                time.sleep(RETRY_TIME_U_BOUND)
                continue

    finally:
        try:
            driver.get(SIGN_OUT_LINK)
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    # allow optional config path for multi-user controllers
    cfg = sys.argv[1] if len(sys.argv) > 1 else "config.ini"
    run_bot(cfg)
