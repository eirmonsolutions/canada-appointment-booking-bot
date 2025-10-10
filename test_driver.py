from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

o = Options()
o.add_argument("--headless=new")
o.add_argument("--no-sandbox")
o.add_argument("--disable-dev-shm-usage")
o.add_argument("--disable-gpu")
o.add_argument("--window-size=1280,1024")
o.binary_location = "/usr/bin/google-chrome"

try:
    d = webdriver.Chrome(service=Service(), options=o)
    d.get("https://example.com")
    print("OK Title:", d.title)
    d.quit()
except Exception as e:
    print("DRIVER ERROR:", e)
