import sys, os
sys.path.insert(0, '/root/tiktok-env/lib/python3.12/site-packages')
os.chdir('/root/content-bot')

# Test 1: can we read cookies?
from tiktokautouploader.function import read_cookies, check_expiry

cookie_file = "TK_cookies_panferov.ai.json"
if os.path.exists(cookie_file):
    cookies, ok = read_cookies(cookie_file)
    print(f"Cookies read: {ok}, count: {len(cookies)}")

    # Check expiry
    expired = check_expiry("panferov.ai")
    print(f"Cookies expired: {expired}")
else:
    print(f"Cookie file not found: {cookie_file}")

print("TEST_OK")
