"""
One-time TikTok login script.
Run this on your PC (not server) — it opens a browser window for you to log in.
After login, session data is saved and can be copied to the server.
"""
from tiktokautouploader import upload_tiktok
import tempfile
import os

# Create a tiny test video (1 second black screen) — we won't actually upload it
# We just need to trigger the login flow
test_video = os.path.join(tempfile.gettempdir(), "tiktok_test.mp4")

# Create minimal mp4 using ffmpeg
os.system(f'ffmpeg -y -f lavfi -i color=c=black:s=1080x1920:d=1 -c:v libx264 -t 1 "{test_video}" 2>nul')

print("=" * 50)
print("  TIKTOK FIRST LOGIN")
print("=" * 50)
print()
print("A browser window will open.")
print("Log in to TikTok with your account: panferov.ai")
print("After login, the session will be saved.")
print()

try:
    upload_tiktok(
        video=test_video,
        description="test - delete me",
        accountname="panferov.ai",
        headless=False,  # VISIBLE browser for first login
    )
    print()
    print("Login successful! Session saved.")
except Exception as e:
    error_msg = str(e)
    if "login" in error_msg.lower() or "auth" in error_msg.lower():
        print(f"Login needed: {e}")
    else:
        print(f"Note: {e}")
        print("If you logged in successfully, the session should be saved anyway.")

print()
print("Now we need to copy the session to the server.")
