import time
import json
import random
import threading
import csv
import smtplib
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as Wait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS, cross_origin
from embassy import Embassies

app = Flask(__name__)
# Configure CORS to allow requests from any origin for simplicity during development
CORS(app, resources={r"/submit": {"origins": "*"}})

# ===================== CONFIG =====================
PRIOD_START_DEFAULT = "2025-12-01"
PRIOD_END_DEFAULT = "2025-12-20"
CSV_FILE = "visa_appointments.csv"
LOG_FILE = f"log_{datetime.now().date()}.txt"

# SMTP Configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = "manshusmartboy@gmail.com"  # Replace with your email
SMTP_PASSWORD = "cvvrefpzcxkqahen"  # Replace with your app-specific password

# ===================== LOGGER =====================
def log_info(user, msg):
    print(f"[{user}] {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now()} - {msg}\n")

# ===================== CSV HANDLER =====================
def save_to_csv(data, status, result_msg):
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["username", "password", "schedule_id", "embassy", "period_start", "period_end", "status", "result", "timestamp"])
        if f.tell() == 0:  # Write header if file is empty
            writer.writeheader()
        writer.writerow({
            "username": data["username"],
            "password": data["password"],
            "schedule_id": data["schedule_id"],
            "embassy": data["embassy"],
            "period_start": data["period_start"],
            "period_end": data["period_end"],
            "status": status,
            "result": result_msg,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

# ===================== EMAIL NOTIFICATION =====================
def send_email(data, status, result_msg):
    subject = f"Visa Appointment Status: {status}"
    body = f"""
    Appointment Details:
    Username: {data['username']}

    Embassy: {data['embassy']}
    Schedule ID: {data['schedule_id']}
    Period: {data['period_start']} to {data['period_end']}
    Status: {status}
    Result: {result_msg}
    Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    msg = f"Subject: {subject}\n\n{body}"
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, 'manshu.developer@gmail.com', msg)
        log_info(data["username"], f"Email sent to 'manshu.developer@gmail.com'")
    except Exception as e:
        log_info(data["username"], f"Email sending failed: {str(e)}")

# ===================== WEB SCRAPER FUNCTIONS =====================
def make_links(embassy, schedule_id, facility_id):
    return {
        "SIGN_IN_LINK": f"https://ais.usvisa-info.com/{embassy}/niv/users/sign_in",
        "APPOINTMENT_URL": f"https://ais.usvisa-info.com/{embassy}/niv/schedule/{schedule_id}/appointment",
        "DATE_URL": f"https://ais.usvisa-info.com/{embassy}/niv/schedule/{schedule_id}/appointment/days/{facility_id}.json?appointments[expedite]=false",
        "TIME_URL": f"https://ais.usvisa-info.com/{embassy}/niv/schedule/{schedule_id}/appointment/times/{facility_id}.json?date=%s&appointments[expedite]=false",
        "SIGN_OUT_LINK": f"https://ais.usvisa-info.com/{embassy}/niv/users/sign_out"
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

def start_process(driver, username, password, regex_continue, sign_in_link, step_time=0.5):
    driver.get(sign_in_link)
    time.sleep(step_time)
    Wait(driver, 60).until(EC.presence_of_element_located((By.NAME, "commit")))
    auto_action(driver, "Click bounce", "xpath", '//a[@class="down-arrow bounce"]', "click", "", step_time)
    auto_action(driver, "Email", "id", "user_email", "send", username, step_time)
    auto_action(driver, "Password", "id", "user_password", "send", password, step_time)
    auto_action(driver, "Privacy", "class", "icheckbox", "click", "", step_time)
    auto_action(driver, "Enter Panel", "name", "commit", "click", "", step_time)
    Wait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, f"//a[contains(text(), '{regex_continue}')]"))
    )

def get_date(driver, date_url):
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(date_url), session)
    try:
        return json.loads(driver.execute_script(script))
    except:
        return []

def get_time(driver, date, time_url):
    time_url = time_url % date
    session = driver.get_cookie("_yatri_session")["value"]
    script = JS_SCRIPT % (str(time_url), session)
    try:
        data = json.loads(driver.execute_script(script))
        return data.get("available_times")[-1]
    except:
        return None

def reschedule(driver, date, facility_id, appointment_url):
    time_slot = get_time(driver, date, TIME_URL)
    if not time_slot:
        return "FAIL", f"No time available for {date}"
    driver.get(appointment_url)
    try:
        import requests
        headers = {
            "User-Agent": driver.execute_script("return navigator.userAgent;"),
            "Referer": appointment_url,
            "Cookie": "_yatri_session=" + driver.get_cookie("_yatri_session")["value"]
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
        r = requests.post(appointment_url, headers=headers, data=data)
        if "Successfully Scheduled" in r.text:
            return "SUCCESS", f"Rescheduled Successfully! {date} {time_slot}"
        else:
            return "FAIL", f"Reschedule Failed {date} {time_slot}"
    except Exception as e:
        return "EXCEPTION", str(e)

# ===================== THREAD TASK =====================
def process_user(user_data):
    username = user_data["username"]
    password = user_data["password"]
    schedule_id = user_data["schedule_id"]
    embassy_key = user_data["embassy"]
    period_start = user_data["period_start"]
    period_end = user_data["period_end"]

    embassy_info = Embassies.get(embassy_key, ["en-ca", 95, "Continue"])
    embassy, facility_id, regex_continue = embassy_info
    links = make_links(embassy, schedule_id, facility_id)
    SIGN_IN_LINK = links["SIGN_IN_LINK"]
    APPOINTMENT_URL = links["APPOINTMENT_URL"]
    DATE_URL = links["DATE_URL"]
    global TIME_URL
    TIME_URL = links["TIME_URL"]
    SIGN_OUT_LINK = links["SIGN_OUT_LINK"]

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    driver.set_window_position(random.randint(0, 800), random.randint(0, 400))

    first_loop = True
    req_count = 0
    retry_time_l_bound = 10
    retry_time_u_bound = 120
    ban_cooldown_time = 5 * 3600  # 5 hours in seconds

    while True:
        try:
            if first_loop:
                start_process(driver, username, password, regex_continue, SIGN_IN_LINK)
                log_info(username, "Login successful.")
                first_loop = False

            req_count += 1
            log_info(username, f"Request #{req_count}")
            dates = get_date(driver, DATE_URL)
            available_dates = [d.get('date') for d in dates]
            log_info(username, f"Available Dates: {', '.join(available_dates)}")

            if not dates:
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
                status, msg = reschedule(driver, date, facility_id, APPOINTMENT_URL)
                log_info(username, f"{status} | {msg}")
                save_to_csv(user_data, status, msg)
                if status == "SUCCESS":  # Only send email on successful booking
                    send_email(user_data, status, msg)
                break
            else:
                log_info(username, "No valid dates found within range. Retrying...")
                save_to_csv(user_data, "FAIL", "No valid dates in range")

            retry_wait_time = random.randint(retry_time_l_bound, retry_time_u_bound)
            log_info(username, f"Retrying after {retry_wait_time} seconds")
            time.sleep(retry_wait_time)

        except Exception as e:
            log_info(username, f"Exception: {e}")
            save_to_csv(user_data, "EXCEPTION", str(e))
            break

    driver.get(SIGN_OUT_LINK)
    driver.quit()
    log_info(username, "Session finished.")

# ===================== FLASK ROUTES =====================
@app.route('/')
def serve_index():
    return render_template('index.html')

@app.route('/submit', methods=['POST', 'OPTIONS'])
@cross_origin()  # Explicitly allow CORS for this route
def submit():
    if request.method == 'OPTIONS':
        return '', 200  # Handle preflight request
    data = request.json
    num_students = int(data.get("num_students", 1))
    students = data.get("students", [])
    
    if len(students) != num_students:
        return jsonify({"error": "Invalid number of students provided"}), 400

    threads = []
    for student in students:
        t = threading.Thread(target=process_user, args=(student,))
        t.start()
        threads.append(t)
        time.sleep(3)  # Avoid simultaneous login

    for t in threads:
        t.join()

    return jsonify({"message": "All accounts processed successfully"})

# ===================== MAIN =====================
if __name__ == "__main__":
    import webbrowser
    webbrowser.open("http://148.230.86.132:5000")
    app.run(host="148.230.86.132", port=5000, debug=True)