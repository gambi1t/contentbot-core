"""
TikTok login — opens browser, you log in, cookies are saved.
Uses copied Chrome profile for comfort.
"""
from playwright.sync_api import sync_playwright
import json
import time

COOKIES_FILE = "TK_cookies_panferov.ai.json"

print("=" * 50)
print("  TIKTOK LOGIN")
print("=" * 50)
print()
print("Сейчас откроется браузер.")
print("Залогинься в TikTok (аккаунт panferov.ai).")
print("После логина подожди пока откроется лента.")
print()

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=r"D:\AI\tiktok-profile",
        channel="chrome",
        headless=False,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.tiktok.com/login")

    print("Жду логин... (браузер закроется автоматически)")

    # Wait until we see sessionid cookie (means login successful)
    for i in range(300):  # 5 min max
        cookies = context.cookies("https://www.tiktok.com")
        session = [c for c in cookies if c["name"] == "sessionid"]
        if session and session[0]["value"]:
            break
        time.sleep(1)

    time.sleep(3)  # Let cookies settle

    # Save cookies
    cookies = context.cookies("https://www.tiktok.com")
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)

    session_cookies = [c for c in cookies if c["name"] in ("sessionid", "sid_tt", "uid_tt")]
    print(f"\nCookies saved to {COOKIES_FILE}")
    print(f"Total cookies: {len(cookies)}")
    print(f"Session cookies: {len(session_cookies)}")
    for c in session_cookies:
        print(f"  {c['name']}: {c['value'][:20]}...")

    context.close()

print("\nDone!")
