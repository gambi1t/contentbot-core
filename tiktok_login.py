"""
Simple TikTok login — opens browser, you log in, cookies are saved.
"""
import json
import time
from playwright.sync_api import sync_playwright

COOKIES_FILE = "TK_cookies_panferov.ai.json"

print("=" * 50)
print("  TIKTOK LOGIN")
print("=" * 50)
print()
print("Opening browser... Log in to TikTok.")
print("After login, wait for the For You page to load.")
print()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, channel="chrome")
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://www.tiktok.com/login")

    print("Waiting for you to log in...")
    print("(Browser will close automatically after login)")

    # Wait until redirected to /foryou (means login successful)
    while True:
        url = page.url
        if "/foryou" in url or "/@" in url:
            break
        time.sleep(1)

    time.sleep(3)  # Let cookies settle

    # Save cookies
    cookies = context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)

    print(f"\nCookies saved to {COOKIES_FILE}")
    print(f"Total cookies: {len(cookies)}")

    browser.close()

print("\nDone! Now run the copy script to send cookies to the server.")
