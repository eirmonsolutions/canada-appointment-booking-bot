import time
import json
import random
import requests
import configparser
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

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

JS_SCRIPT = ("var req = new XMLHttpRequest();"
             f"req.open('GET', '%s', false);"
             "req.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');"
             "req.setRequestHeader('X-Requested-With', 'XMLHttpRequest');"
             f"req.setRequestHeader('Cookie', '_yatri_session=%s');"
             "req.send(null);"
             "return req.responseText;")

# -------------------- FUNCTIONS --------------------

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
            requests.post(url, data)
        except Exception as e:
            print("Pushover Error:", e)
    # Personal Pusher
    if PERSONAL_SITE_USER:
        try:
            url = PERSONAL_PUSHER_URL
            data = {
                "title": "VISA - " + str(title),
                "user": PERSONAL_SITE_USER,
                "pass": PERSONAL_SITE_PASS,
                "email": PUSH_TARGET_EMAIL,
                "msg": msg,
            }
            requests.post(url, data)
        except Exception as e:
            print("Personal Pusher Error:", e)


def auto_action(label, find_by, el_type, action, value, sleep_time=0):
    print("\t"+ label +":", end="")
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


def start_process():
    driver.get(SIGN_IN_LINK)
    time.sleep(STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))
    auto_action("Click bounce", "xpath", '//a[@class="down-arrow bounce"]', "click", "", STEP_TIME)
    auto_action("Email", "id", "user_email", "send", USERNAME, STEP_TIME)
    auto_action("Password", "id", "user_password", "send", PASSWORD, STEP_TIME)
    auto_action("Privacy", "class", "icheckbox", "click", "", STEP_TIME)
    auto_action("Enter Panel", "name", "commit", "click", "", STEP_TIME)
    Wait(driver, 60).until(EC.presence_of_element_located((By.XPATH, "//a[contains(text(), '" + REGEX_CONTINUE + "')]")))
    print("\n\tLogin successful!\n")


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
    r = requests.post(APPOINTMENT_URL, headers=headers, data=data)
    if 'Successfully Scheduled' in r.text:
        return ["SUCCESS", f"Rescheduled Successfully! {date} {time_slot}"]
    return ["FAIL", f"Reschedule Failed! {date} {time_slot}"]


def get_date():
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(DATE_URL), session)
    content = driver.execute_script(script)
    return json.loads(content)


def get_time(date):
    time_url = TIME_URL % date
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(time_url), session)
    content = driver.execute_script(script)
    data = json.loads(content)
    time_slot = data.get("available_times")[-1]
    print(f"Got time successfully! {date} {time_slot}")
    return time_slot


def get_available_date(dates):
    PED = datetime.strptime(PRIOD_END, "%Y-%m-%d")
    PSD = datetime.strptime(PRIOD_START, "%Y-%m-%d")
    for d in dates:
        date = d.get('date')
        new_date = datetime.strptime(date, "%Y-%m-%d")
        if PSD < new_date < PED:
            return date
    return None


def info_logger(file_path, log):
    with open(file_path, "a") as file:
        file.write(str(datetime.now().time()) + ":\n" + log + "\n")


# -------------------- DRIVER INIT --------------------
if LOCAL_USE:
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
else:
    driver = webdriver.Remote(command_executor=HUB_ADDRESS, options=webdriver.ChromeOptions())


# -------------------- MAIN LOOP --------------------
# -------------------- MAIN LOOP --------------------
if __name__ == "__main__":
    first_loop = True
    while True:
        LOG_FILE_NAME = "log_" + str(datetime.now().date()) + ".txt"
        if first_loop:
            t0 = time.time()
            total_time = 0
            Req_count = 0
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
            all_dates_str = ", ".join([d.get('date') for d in dates])
            msg = "All available dates:\n" + all_dates_str
            print(msg)
            info_logger(LOG_FILE_NAME, msg)

            # Check if any date is in our period
            date_in_period = get_available_date(dates)
            if date_in_period:
                END_MSG_TITLE, msg = reschedule(date_in_period)
                print(msg)
                info_logger(LOG_FILE_NAME, msg)
                send_notification(END_MSG_TITLE, msg)
                break  # stop if successful
            else:
                print(f"No available dates in your period ({PRIOD_START} - {PRIOD_END}), retrying...")
                info_logger(LOG_FILE_NAME, f"No dates in target period ({PRIOD_START} - {PRIOD_END})")

            # Retry wait
            RETRY_WAIT_TIME = random.randint(RETRY_TIME_L_BOUND, RETRY_TIME_U_BOUND)
            time.sleep(RETRY_WAIT_TIME)

        except Exception as e:
            msg = f"Exception occurred: {e}"
            print(msg)
            info_logger(LOG_FILE_NAME, msg)
            send_notification("EXCEPTION", msg)
            time.sleep(RETRY_TIME_U_BOUND)
            continue


    driver.get(SIGN_OUT_LINK)
    driver.stop_client()
    driver.quit()
