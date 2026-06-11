"""Patch haziq TikTokAutoUploader to handle react-joyride overlay."""

path = '/root/content-bot/venv/lib/python3.12/site-packages/tiktokautouploader/function.py'

with open(path) as f:
    content = f.read()

changes = 0

# Patch 1: Add overlay removal before file input
old1 = '''def _set_video_input(page, video):
    try:
        page.set_input_files('input[type="file"][accept="video/*"]', f"{video}")'''

new1 = '''def _set_video_input(page, video):
    # [PATCH] Remove react-joyride overlay before file input
    try:
        page.evaluate("""
            document.querySelectorAll('#react-joyride-portal, .react-joyride__overlay').forEach(e => e.remove());
            for (const btn of document.querySelectorAll('button')) {
                if (['Cancel','Got it','Skip'].includes(btn.textContent.trim())) btn.click();
            }
        """)
    except Exception:
        pass
    import time as _time; _time.sleep(0.5)
    try:
        page.set_input_files('input[type="file"][accept="video/*"]', f"{video}")'''

if old1 in content:
    content = content.replace(old1, new1)
    changes += 1
    print('Patch 1 applied: overlay removal before file input')

# Patch 2: Fix tutorial popup dismissal in _add_description_and_hashtags
old2 = '''    if page.locator("button:has-text('Cancel')").is_visible():
        print("Tutorial pop-up detected, dismissing...")
        page.click("button:has-text('Cancel')")
    if page.locator("button:has-text('Got it')").is_visible():
        page.click("button:has-text('Got it')")'''

new2 = '''    # [PATCH] Dismiss tutorial popups via JS to avoid overlay/DOM issues
    try:
        page.evaluate("""
            document.querySelectorAll('#react-joyride-portal, .react-joyride__overlay').forEach(e => e.remove());
            for (const btn of document.querySelectorAll('button')) {
                if (['Cancel','Got it','Skip'].includes(btn.textContent.trim())) btn.click();
            }
        """)
        time.sleep(0.5)
    except Exception:
        pass'''

if old2 in content:
    content = content.replace(old2, new2)
    changes += 1
    print('Patch 2 applied: JS-based tutorial dismissal')

if changes > 0:
    with open(path, 'w') as f:
        f.write(content)
    print(f'Done: {changes} patches applied')
else:
    print('No patches matched!')
