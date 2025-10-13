# test.py
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--disable-gpu")
opts.add_argument("--remote-allow-origins=*")
opts.add_argument("--window-size=1280,2000")

# Option A: Selenium Manager (recommended; chromedriver path auto)
driver = webdriver.Chrome(options=opts)

# Option B: (agar aap direct chromedriver binary use karna chaho)
# from selenium.webdriver.chrome.service import Service
# driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=opts)

print("Session:", driver.session_id)
driver.get("https://example.com")
print("Title:", driver.title)
print("UA:", driver.execute_script("return navigator.userAgent;"))

# Ek screenshot bhi save karke dekh lo
driver.save_screenshot("ok.png")
driver.quit()
print("OK - screenshot saved as ok.png")
