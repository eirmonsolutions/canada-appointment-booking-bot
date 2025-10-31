# bot.py
import json
import logging
import os.path
import random
import re
import time
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import urlencode

import smtplib
from email.message import EmailMessage


import requests
from bs4 import BeautifulSoup
from requests import Response, HTTPError
from typing import Callable
# -------------------------- CONSTANTS --------------------------
HOST = "ais.usvisa-info.com"
REFERER = "Referer"
ACCEPT = "Accept"
SET_COOKIE = "set-cookie"
CONTENT_TYPE = "Content-Type"
CACHE_CONTROL_HEADERS = {
    "Cache-Control": "no-store"
}
DEFAULT_HEADERS = {
    "Host": HOST,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, "
                  "like Gecko) Chrome/120.0.0.0 YaBrowser/24.1.0.0 Safari/537.36",
    "sec-ch-ua": "\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\", "
                 "\"YaBrowser\";v=\"24.1\", \"Yowser\";v=\"2.5\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "Windows",
}
SEC_FETCH_USER_HEADERS = {
    "Sec-Fetch-User": "?1"
}
DOCUMENT_HEADERS = {
    **DEFAULT_HEADERS,
    **CACHE_CONTROL_HEADERS,
    ACCEPT: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "ru,en;q=0.9,de;q=0.8,bg;q=0.7",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Upgrade-Insecure-Requests": "1"
}
JSON_HEADERS = {
    **DEFAULT_HEADERS,
    ACCEPT: "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "ru,en;q=0.9,de;q=0.8,bg;q=0.7",
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin"
}
X_CSRF_TOKEN_HEADER = "X-CSRF-Token"
COOKIE_HEADER = "Cookie"
COUNTRIES = {
    "ar": "Argentina", "ec": "Ecuador", "bs": "The Bahamas", "gy": "Guyana",
    "bb": "Barbados", "jm": "Jamaica", "bz": "Belize", "mx": "Mexico",
    "br": "Brazil", "py": "Paraguay", "bo": "Bolivia", "pe": "Peru",
    "ca": "Canada", "sr": "Suriname", "cl": "Chile", "tt": "Trinidad and Tobago",
    "co": "Colombia", "uy": "Uruguay", "cw": "Curacao",
    "us": "United States (Domestic Visa Renewal)", "al": "Albania",
    "ie": "Ireland", "am": "Armenia", "kv": "Kosovo", "az": "Azerbaijan",
    "mk": "North Macedonia", "be": "Belgium", "nl": "The Netherlands",
    "ba": "Bosnia and Herzegovina", "pt": "Portugal", "hr": "Croatia",
    "rs": "Serbia", "cy": "Cyprus", "es": "Spain and Andorra",
    "fr": "France", "tr": "Turkiye", "gr": "Greece", "gb": "United Kingdom",
    "it": "Italy", "il": "Israel, Jerusalem, The West Bank, and Gaza",
    "ae": "United Arab Emirates", "ir": "Iran", "ao": "Angola",
    "rw": "Rwanda", "cm": "Cameroon", "sn": "Senegal", "cv": "Cabo Verde",
    "tz": "Tanzania", "cd": "The Democratic Republic of the Congo",
    "za": "South Africa", "et": "Ethiopia", "ug": "Uganda", "ke": "Kenya",
    "zm": "Zambia",
}
DATE_TIME_FORMAT = "%H:%M %Y-%m-%d"
DATE_FORMAT = "%d.%m.%Y"
HTML_PARSER = "html.parser"
NONE = "None"

# File names (used by both bot and Flask)
CONFIG_FILE = "config"
ASC_FILE = "asc"
LOG_FILE = "log.txt"
LOG_FORMAT = "%(asctime)s  %(message)s"


# -------------------------- HELPERS --------------------------
def parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


class NoScheduleIdException(Exception):
    def __init__(self):
        super().__init__("No schedule id")


class AppointmentDateLowerMinDate(Exception):
    def __init__(self):
        super().__init__("Current appointment date and time lower than specified minimal date")


EMAIL_SENDER = "manshusmartboy@gmail.com"
EMAIL_PASSWORD = "cvvrefpzcxkqahen"   # <-- YOUR APP PASSWORD
EMAIL_SMTP_SERVER = "smtp.gmail.com"
EMAIL_SMTP_PORT = 587


def send_email(to: str, subject: str, body: str):
    msg = EmailMessage()
    msg["From"] = EMAIL_SENDER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

# --- IN Bot.process(), replace the BOOKED block ---

class Logger:
    def __init__(self, log_file: str, log_format: str, user_prefix: str = "", callback: Callable[[str], None] | None = None):
        self.user_prefix = user_prefix
        self.callback = callback

        log_formatter = logging.Formatter(log_format)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # file
        fh = logging.FileHandler(log_file)
        fh.setFormatter(log_formatter)
        root_logger.addHandler(fh)

        # console (keeps old behaviour)
        ch = logging.StreamHandler()
        ch.setFormatter(log_formatter)
        root_logger.addHandler(ch)

        self.root_logger = root_logger

    def __call__(self, message: str | Exception):
        txt = str(message)
        if self.user_prefix:
            txt = f"[{self.user_prefix}]: {txt}"
        self.root_logger.debug(txt, exc_info=isinstance(message, Exception))
        if self.callback:
            self.callback(txt)



class Appointment:
    def __init__(self, schedule_id: str, description: str, appointment_datetime: Optional[datetime]):
        self.schedule_id = schedule_id
        self.description = description
        self.appointment_datetime = appointment_datetime


# -------------------------- CONFIG --------------------------
class Config:
    def __init__(self, config_file: str):
        self.config_file = config_file
        config_data = {}
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as f:
                for line in f.readlines():
                    param = line.strip().split("=", 1)
                    if len(param) == 2:
                        key, value = param[0].strip(), param[1].strip()
                        config_data[key] = None if value == NONE else value

        # Required
        self.email: str = config_data.get("EMAIL") or ""
        self.password: str = config_data.get("PASSWORD") or ""
        self.country: str = config_data.get("COUNTRY") or ""

        # Optional
        self.facility_id: Optional[str] = config_data.get("FACILITY_ID")
        self.asc_facility_id: Optional[str] = config_data.get("ASC_FACILITY_ID")
        self.schedule_id: Optional[str] = config_data.get("SCHEDULE_ID")

        # Dates
        min_date = config_data.get("MIN_DATE")
        self.min_date: date = datetime.strptime(min_date, DATE_FORMAT).date() if min_date else datetime.now().date()

        max_date = config_data.get("MAX_DATE")
        self.max_date: Optional[date] = datetime.strptime(max_date, DATE_FORMAT).date() if max_date and max_date != NONE else None

        # ASC
        self.need_asc: bool = config_data.get("NEED_ASC") == "True"

        self.__save()

    def set_facility_id(self, locations: dict[str, str]):
        self.facility_id = self.__choose_location(locations, "consul")
        self.__save()

    def set_asc_facility_id(self, locations: dict[str, str]):
        self.asc_facility_id = self.__choose_location(locations, "asc")
        self.__save()

    def set_schedule_id(self, schedule_ids: dict[str, Appointment]):
        self.schedule_id = Config.__choose(
            schedule_ids,
            f"Choose schedule id (enter number): \n" +
            "\n".join([f"{k}  {v.description}" for k, v in schedule_ids.items()]) + "\n"
        )
        self.__save()

    @staticmethod
    def __choose_location(locations: dict[str, str], location_name: str) -> str:
        return Config.__choose(
            locations,
            f"Choose {location_name} location (enter number): \n" +
            "\n".join([f"{k}  {v}" for k, v in locations.items()]) + "\n"
        )

    @staticmethod
    def __choose(values: dict, message: str) -> str:
        if len(values) == 1:
            return next(iter(values))
        value = None
        while not value:
            value = input(message)
            if value not in values:
                value = None
        return value

    def __save(self):
        with open(self.config_file, "w") as f:
            f.write(
                f"EMAIL={self.email}\n"
                f"PASSWORD={self.password}\n"
                f"COUNTRY={self.country}\n"
                f"FACILITY_ID={self.facility_id or NONE}\n"
                f"MIN_DATE={self.min_date.strftime(DATE_FORMAT)}\n"
                f"MAX_DATE={self.max_date.strftime(DATE_FORMAT) if self.max_date else NONE}\n"
                f"NEED_ASC={self.need_asc}\n"
                f"ASC_FACILITY_ID={self.asc_facility_id or NONE}\n"
                f"SCHEDULE_ID={self.schedule_id or NONE}"
            )


# -------------------------- BOT --------------------------
class Bot:
    def __init__(self, config: Config, logger: Logger, asc_file: str):
        self.logger = logger
        self.config = config
        self.asc_file = asc_file
        self.url = f"https://{HOST}/en-{config.country}/niv"

        self.appointment_datetime: Optional[datetime] = None
        self.csrf: Optional[str] = None
        self.cookie: Optional[str] = None
        self.session = requests.session()
        self.asc_dates = {}

    @staticmethod
    def get_csrf(response: Response) -> str:
        return BeautifulSoup(response.text, HTML_PARSER).find("meta", {"name": "csrf-token"})["content"]

    def headers(self) -> dict[str, str]:
        headers = {}
        if self.cookie:
            headers[COOKIE_HEADER] = self.cookie
        if self.csrf:
            headers[X_CSRF_TOKEN_HEADER] = self.csrf
        return headers

    def init(self):
        try:
            self.session.close()
        except Exception:
            pass
        self.session = requests.Session()

        self.login()
        self.init_current_data()
        self.init_csrf_and_cookie()

        if not self.config.facility_id:
            self.logger("Not found facility_id")
            self.config.set_facility_id(self.get_available_facility_id())

        if self.config.need_asc and not self.config.asc_facility_id:
            self.logger("Not found asc_facility_id")
            self.config.set_asc_facility_id(self.get_available_asc_facility_id())

        self.init_asc_dates()

        self.logger(
            "Current appointment date and time: "
            f"{self.appointment_datetime.strftime(DATE_TIME_FORMAT) if self.appointment_datetime else 'No date'}"
        )

    def login(self):
        self.logger("Get sign in")
        response = self.session.get(
            f"{self.url}/users/sign_in",
            headers={
                COOKIE_HEADER: "",
                REFERER: f"{self.url}/users/sign_in",
                **DOCUMENT_HEADERS
            }
        )
        response.raise_for_status()
        cookies = response.headers.get(SET_COOKIE)

        self.logger("Post sign in")
        response = self.session.post(
            f"{self.url}/users/sign_in",
            headers={
                **DEFAULT_HEADERS,
                X_CSRF_TOKEN_HEADER: Bot.get_csrf(response),
                COOKIE_HEADER: cookies,
                ACCEPT: "*/*;q=0.5, text/javascript, application/javascript, application/ecmascript, "
                        "application/x-ecmascript",
                REFERER: f"{self.url}/users/sign_in",
                CONTENT_TYPE: "application/x-www-form-urlencoded; charset=UTF-8"
            },
            data=urlencode({
                "user[email]": self.config.email,
                "user[password]": self.config.password,
                "policy_confirmed": "1",
                "commit": "Sign In"
            })
        )
        response.raise_for_status()
        self.cookie = response.headers.get(SET_COOKIE)

    def init_current_data(self):
        self.logger("Get current appointment")
        response = self.session.get(
            self.url,
            headers={**self.headers(), **DOCUMENT_HEADERS}
        )
        response.raise_for_status()

        applications = BeautifulSoup(response.text, HTML_PARSER).find_all("div", {"class": "application"})
        if not applications:
            raise NoScheduleIdException()

        schedule_ids = {}
        for app in applications:
            sid_match = re.search(r"\d+", str(app.find("a")))
            if not sid_match:
                continue
            sid = sid_match.group(0)
            desc = ' '.join([x.get_text() for x in app.find_all("td")][0:4])
            appt_p = app.find("p", {"class": "consular-appt"})
            appt_dt = None
            if appt_p:
                m = re.search(r"\d{1,2} \w+?, \d{4}, \d{1,2}:\d{1,2}", appt_p.get_text())
                if m:
                    appt_dt = datetime.strptime(m.group(0), "%d %B, %Y, %H:%M")
            schedule_ids[sid] = Appointment(sid, desc, appt_dt)

        if not self.config.schedule_id:
            self.config.set_schedule_id(schedule_ids)

        self.appointment_datetime = schedule_ids[self.config.schedule_id].appointment_datetime

        if self.appointment_datetime and self.appointment_datetime.date() <= self.config.min_date:
            raise AppointmentDateLowerMinDate()

    def init_asc_dates(self):
        if not self.config.need_asc or not self.config.asc_facility_id:
            return

        if not os.path.exists(self.asc_file):
            open(self.asc_file, 'w').close()
        with open(self.asc_file) as f:
            try:
                self.asc_dates = json.load(f)
            except:
                pass

        try:
            dates_temp = self.get_asc_available_dates()
        except:
            dates_temp = None

        if dates_temp:
            dates = []
            for x in dates_temp:
                d = parse_date(x)
                if self.config.min_date <= d <= (self.config.max_date or d):
                    dates.append(x)

            if dates:
                self.asc_dates = {}
                for x in dates:
                    try:
                        self.asc_dates[x] = self.get_asc_available_times(x)
                    except:
                        pass

        with open(self.asc_file, 'w') as f:
            json.dump(self.asc_dates, f)

    def init_csrf_and_cookie(self):
        self.logger("Init csrf")
        response = self.load_change_appointment_page()
        self.cookie = response.headers.get(SET_COOKIE)
        self.csrf = Bot.get_csrf(response)

    def get_available_locations(self, element_id: str) -> dict[str, str]:
        self.logger("Get location list")
        locations = (BeautifulSoup(self.load_change_appointment_page().text, HTML_PARSER)
                     .find("select", {"id": element_id})
                     .find_all("option"))
        return {loc["value"]: loc.text for loc in locations if loc["value"]}

    def get_available_facility_id(self) -> dict[str, str]:
        return self.get_available_locations("appointments_consulate_appointment_facility_id")

    def get_available_asc_facility_id(self) -> dict[str, str]:
        return self.get_available_locations("appointments_asc_appointment_facility_id")

    def load_change_appointment_page(self) -> Response:
        self.logger("Get new appointment")
        response = self.session.get(
            f"{self.url}/schedule/{self.config.schedule_id}/appointment",
            headers={
                **self.headers(),
                **DOCUMENT_HEADERS,
                **SEC_FETCH_USER_HEADERS,
                REFERER: f"{self.url}/schedule/{self.config.schedule_id}/continue_actions"
            }
        )
        response.raise_for_status()
        return response

    def get_available_dates(self) -> list[str]:
        self.logger("Get available date")
        response = self.session.get(
            f"{self.url}/schedule/{self.config.schedule_id}/appointment/days/"
            f"{self.config.facility_id}.json?appointments[expedite]=false",
            headers={**self.headers(), **JSON_HEADERS, REFERER: f"{self.url}/schedule/{self.config.schedule_id}/appointment"}
        )
        response.raise_for_status()
        data = response.json()
        self.logger(f"Response: {data}")
        dates = [x["date"] for x in data]
        dates.sort()
        return dates

    def get_available_times(self, available_date: str) -> list[str]:
        self.logger("Get available time")
        response = self.session.get(
            f"{self.url}/schedule/{self.config.schedule_id}/appointment/times/{self.config.facility_id}.json?"
            f"date={available_date}&appointments[expedite]=false",
            headers={**self.headers(), **JSON_HEADERS, REFERER: f"{self.url}/schedule/{self.config.schedule_id}/appointment"}
        )
        response.raise_for_status()
        data = response.json()
        self.logger(f"Response: {data}")
        times = data.get("available_times") or data.get("business_times") or []
        times.sort()
        return times

    def get_asc_available_dates(self, available_date: Optional[str] = None, available_time: Optional[str] = None) -> list[str]:
        self.logger("Get available dates ASC")
        response = self.session.get(
            f"{self.url}/schedule/{self.config.schedule_id}/appointment/days/"
            f"{self.config.asc_facility_id}.json?&consulate_id={self.config.facility_id}"
            f"&consulate_date={available_date or ''}&consulate_time={available_time or ''}"
            f"&appointments[expedite]=false",
            headers={**self.headers(), **JSON_HEADERS, REFERER: f"{self.url}/schedule/{self.config.schedule_id}/appointment"}
        )
        response.raise_for_status()
        data = response.json()
        self.logger(f"Response: {data}")
        dates = [x["date"] for x in data]
        dates.sort()
        return dates

    def get_asc_available_times(self, asc_date: str, cons_date: Optional[str] = None, cons_time: Optional[str] = None) -> list[str]:
        self.logger("Get available times ASC")
        response = self.session.get(
            f"{self.url}/schedule/{self.config.schedule_id}/appointment/times/{self.config.asc_facility_id}.json?"
            f"date={asc_date}&consulate_id={self.config.schedule_id}"
            f"&consulate_date={cons_date or ''}&consulate_time={cons_time or ''}"
            f"&appointments[expedite]=false",
            headers={**self.headers(), **JSON_HEADERS, REFERER: f"{self.url}/schedule/{self.config.schedule_id}/appointment"}
        )
        response.raise_for_status()
        data = response.json()
        self.logger(f"Response: {data}")
        times = data.get("available_times") or data.get("business_times") or []
        times.sort()
        return times

    def book(self, cons_date: str, cons_time: str, asc_date: Optional[str], asc_time: Optional[str]):
        self.logger("Book")
        body = {
            "authenticity_token": self.csrf,
            "confirmed_limit_message": "1",
            "use_consulate_appointment_capacity": "true",
            "appointments[consulate_appointment][facility_id]": self.config.facility_id,
            "appointments[consulate_appointment][date]": cons_date,
            "appointments[consulate_appointment][time]": cons_time
        }
        if asc_date and asc_time:
            self.logger("Add ASC date and time to request")
            body.update({
                "appointments[asc_appointment][facility_id]": self.config.asc_facility_id,
                "appointments[asc_appointment][date]": asc_date,
                "appointments[asc_appointment][time]": asc_time
            })

        self.logger(f"Request {body}")
        return self.session.post(
            f"{self.url}/schedule/{self.config.schedule_id}/appointment",
            headers={
                **self.headers(),
                **DOCUMENT_HEADERS,
                **SEC_FETCH_USER_HEADERS,
                CONTENT_TYPE: "application/x-www-form-urlencoded",
                "Origin": f"https://{HOST}",
                REFERER: f"{self.url}/schedule/{self.config.schedule_id}/appointment"
            },
            data=urlencode(body)
        )

    def process(self):
        self.init()
        while True:
            time.sleep(1.5)
            try:
                now = datetime.now()
                if now.minute % 5 != 0 or now.second >= 10:
                    if now.second % 10 == 0:
                        self.logger("Wait")
                    continue

                try:
                    available_dates = self.get_available_dates()
                except HTTPError as err:
                    if err.response.status_code == 401:
                        self.logger("Get 401 - Re-login")
                        self.init()
                        available_dates = self.get_available_dates()
                    else:
                        raise

                if not available_dates:
                    self.logger("No available dates")
                    continue

                self.logger(f"All available dates: {available_dates}")

                reinit_asc = False
                for date_str in available_dates:
                    self.logger(f"Checking date: {date_str}")
                    adate = parse_date(date_str)

                    if adate <= self.config.min_date:
                        self.logger(f"Skip: below min {self.config.min_date}")
                        continue
                    if self.config.max_date and adate > self.config.max_date:
                        self.logger(f"Skip: above max {self.config.max_date}")
                        break
                    if self.appointment_datetime and adate >= self.appointment_datetime.date():
                        self.logger("Skip: not earlier than current")
                        break

                    times = self.get_available_times(date_str)
                    if not times:
                        self.logger("No times")
                        continue

                    self.logger(f"Times: {times}")
                    booked = False
                    for t in times:
                        asc_d, asc_t = None, None
                        if self.config.need_asc:
                            min_asc = adate - timedelta(days=7)
                            for k, v in self.asc_dates.items():
                                if min_asc <= parse_date(k) < adate and v:
                                    asc_d, asc_t = k, random.choice(v)
                                    break
                            if not asc_d:
                                asc_dates = self.get_asc_available_dates(date_str, t)
                                if asc_dates:
                                    asc_d = asc_dates[0]
                                    asc_times = self.get_asc_available_times(asc_d, date_str, t)
                                    asc_t = random.choice(asc_times) if asc_times else None
                            if not asc_d or not asc_t:
                                self.logger("No ASC slot")
                                continue

                        log_msg = (
                            "=====================\n"
                            f"# Trying: {t} {date_str} #\n"
                        )
                        if asc_d:
                            log_msg += f"# ASC: {asc_t} {asc_d} #\n"
                        log_msg += "====================="
                        self.logger(log_msg)

                        self.book(date_str, t, asc_d, asc_t)
                        old_dt = self.appointment_datetime
                        self.init_current_data()

                        if old_dt != self.appointment_datetime:
                            new_dt_str = self.appointment_datetime.strftime(DATE_TIME_FORMAT)
                            success = (
                                "=====================\n"
                                "#     BOOKED!      #\n"
                                f"# {self.appointment_datetime.strftime(DATE_TIME_FORMAT)} #\n"
                            )
                            if asc_d:
                                success += f"# ASC: {asc_t} {asc_d} #\n"
                            email_body = (
                                f"US VISA APPOINTMENT BOOKED!\n\n"
                                f"Email: {self.config.email}\n"
                                f"Date & Time: {new_dt_str}\n"
                            )
                            success += "====================="
                            self.logger(success)
                            if asc_d:
                                email_body += f"ASC: {asc_t} on {asc_d}\n"
                                email_body += f"\nLogin: https://ais.usvisa-info.com/en-{self.config.country}/niv"

                        if send_email(self.config.email, "VISA BOOKED!", email_body):
                            self.logger("Email sent!")
                        else:
                            self.logger("Email failed.")
                            booked = True
                            break

                    reinit_asc = True
                    if booked:
                        break

                if reinit_asc and self.config.need_asc:
                    self.init_asc_dates()

            except KeyboardInterrupt:
                self.logger("Stopped by user")
                break
            except AppointmentDateLowerMinDate as e:
                self.logger(e)
                break
            except Exception as e:
                self.logger(e)


# -------------------------- MAIN --------------------------
def main():
    config = Config(CONFIG_FILE)
    logger = Logger(LOG_FILE, LOG_FORMAT)
    Bot(config, logger, ASC_FILE).process()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass