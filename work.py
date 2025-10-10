import time
import json
import random
import threading
import configparser
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from embassy import *

# ===================== CONFIG =====================
config = configparser.ConfigParser()
config.read('config.ini')

# Load multiple users from config
accounts = json.loads(config['ACCOUNTS']['USERS'])

PRIOD_START = config['PERSONAL_INFO']['PRIOD_START']
PRIOD_END = config['PERSONAL_INFO']['PRIOD_END']
YOUR_EMBASSY = config['PERSONAL_INFO']['YOUR_EMBASSY']
EMBASSY = Embassies[YOUR_EMBASSY][0]
FACILITY_ID = Embassies[YOUR_EMBASSY][1]
REGEX_CONTINUE = Embassies[YOUR_EMBASSY][2]

# Time Section
minute = 60
hour = 60 * minute
STEP_TIME = 0.5
RETRY_TIME_L_BOUND = config['TIME'].getfloat('RETRY_TIME_L_BOUND')
RETRY_TIME_U_BOUND = config['TIME'].getfloat('RETRY_TIME_U_BOUND')
WORK_LIMIT_TIME = config['TIME'].getfloat('WORK_LIMIT_TIME')
WORK_COOLDOWN_TIME = config['TIME'].getfloat('WORK_COOLDOWN_TIME')
BAN_COOLDOWN_TIME = config['TIME'].getfloat('BAN_COOLDOWN_TIME')

LOCAL_USE = config['CHROMEDRIVER'].getboolean('LOCAL_USE')
HUB_ADDRESS = config['CHROMEDRIVER']['HUB_ADDRESS']

# Base URLs
def make_links(EMBASSY, SCHEDULE_ID, FACILITY_ID):
    return {
        "SIGN_IN_LINK": f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_in",
        "APPOINTMENT_URL": f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment",
        "DATE_URL": f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/days/{FACILITY_ID}.json?appointments[expedite]=false",
        "TIME_URL": f"https://ais.usvisa-info.com/{EMBASSY}/niv/schedule/{SCHEDULE_ID}/appointment/times/{FACILITY_ID}.json?date=%s&appointments[expedite]=false",
        "SIGN_OUT_LINK": f"https://ais.usvisa-info.com/{EMBASSY}/niv/users/sign_out"
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

# ===================== LOGGER =====================
def log_info(user, msg):
    LOG_FILE_NAME = f"log_{user}_{str(datetime.now().date())}.txt"
    print(f"[{user}] {msg}")
    with open(LOG_FILE_NAME, "a") as f:
        f.write(f"{datetime.now()} - {msg}\n")

# ===================== FUNCTIONS =====================
def auto_action(driver, label, find_by, el_type, action, value, sleep_time=0):
    print(f"\t{label}:", end="")
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
    print(" OK")
    if sleep_time:
        time.sleep(sleep_time)

def start_process(driver, USERNAME, PASSWORD, REGEX_CONTINUE, SIGN_IN_LINK, STEP_TIME):
    driver.get(SIGN_IN_LINK)
    time.sleep(STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))
    auto_action(driver, "Click bounce", "xpath", '//a[@class="down-arrow bounce"]', "click", "", STEP_TIME)
    auto_action(driver, "Email", "id", "user_email", "send", USERNAME, STEP_TIME)
    auto_action(driver, "Password", "id", "user_password", "send", PASSWORD, STEP_TIME)
    auto_action(driver, "Privacy", "class", "icheckbox", "click", "", STEP_TIME)
    auto_action(driver, "Enter Panel", "name", "commit", "click", "", STEP_TIME)
    Wait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, f"//a[contains(text(), '{REGEX_CONTINUE}')]"))
    )

def get_date(driver, DATE_URL):
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(DATE_URL), session)
    try:
        return json.loads(driver.execute_script(script))
    except:
        return []

def get_time(driver, date, TIME_URL):
    time_url = TIME_URL % date
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(time_url), session)
    try:
        data = json.loads(driver.execute_script(script))
        return data.get("available_times")[-1]
    except:
        return None

def reschedule(driver, date, FACILITY_ID, APPOINTMENT_URL):
    time_slot = get_time(driver, date, TIME_URL)
    if not time_slot:
        return "FAIL", f"No time available for {date}"
    driver.get(APPOINTMENT_URL)
    try:
        import requests
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
        r = requests.post(APPOINTMENT_URL, headers=headers, data=data)
        if "Successfully Scheduled" in r.text:
            return "SUCCESS", f"Rescheduled Successfully! {date} {time_slot}"
        else:
            return "FAIL", f"Reschedule Failed {date} {time_slot}"
    except Exception as e:
        return "EXCEPTION", str(e)

# ===================== THREAD TASK =====================
def process_user(user):
    USERNAME = user["USERNAME"]
    PASSWORD = user["PASSWORD"]
    SCHEDULE_ID = user["SCHEDULE_ID"]

    links = make_links(EMBASSY, SCHEDULE_ID, FACILITY_ID)
    SIGN_IN_LINK = links["SIGN_IN_LINK"]
    APPOINTMENT_URL = links["APPOINTMENT_URL"]
    DATE_URL = links["DATE_URL"]
    TIME_URL = links["TIME_URL"]
    SIGN_OUT_LINK = links["SIGN_OUT_LINK"]

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    driver.set_window_position(random.randint(0, 800), random.randint(0, 400))

    first_loop = True
    Req_count = 0

    while True:
        try:
            if first_loop:
                start_process(driver, USERNAME, PASSWORD, REGEX_CONTINUE, SIGN_IN_LINK, STEP_TIME)
                log_info(USERNAME, "Login successful.")
                first_loop = False

            Req_count += 1
            log_info(USERNAME, f"Request #{Req_count}")
            dates = get_date(driver, DATE_URL)

            available_dates = [d.get('date') for d in dates]
            log_info(USERNAME, f"All Available Dates 1: {', '.join(available_dates)}")
 

            available_dates = [d.get('date') for d in dates]
            log_info(USERNAME, f"All Available Dates 2: {', '.join(available_dates)}")

            if not dates:
                log_info(USERNAME, f"No dates found. Sleeping {BAN_COOLDOWN_TIME} hours...")
                time.sleep(BAN_COOLDOWN_TIME * hour)
                first_loop = True
                continue

            

            PSD = datetime.strptime(PRIOD_START, "%Y-%m-%d")
            PED = datetime.strptime(PRIOD_END, "%Y-%m-%d")
            valid_dates = [d for d in available_dates if PSD <= datetime.strptime(d, "%Y-%m-%d") <= PED]

            if valid_dates:
                date = valid_dates[0]
                status, msg = reschedule(driver, date, FACILITY_ID, APPOINTMENT_URL)
                log_info(USERNAME, f"{status} | {msg}")
                break
            else:
                log_info(USERNAME, "No valid dates found within range. Retrying...")

            RETRY_WAIT_TIME = random.randint(int(RETRY_TIME_L_BOUND), int(RETRY_TIME_U_BOUND))
            log_info(USERNAME, f"Retrying after {RETRY_WAIT_TIME} seconds")
            time.sleep(RETRY_WAIT_TIME)

        except Exception as e:
            log_info(USERNAME, f"Exception: {e}")
            break

    driver.get(SIGN_OUT_LINK)
    driver.quit()
    log_info(USERNAME, "Session finished.")

# ===================== RUN ALL USERS =====================
threads = []
for acc in accounts:
    t = threading.Thread(target=process_user, args=(acc,))
    t.start()
    threads.append(t)
    time.sleep(3)  # slight delay to avoid simultaneous login

for t in threads:
    t.join()

print("All accounts processed successfully.")
