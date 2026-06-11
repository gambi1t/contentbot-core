"""Patch tiktokautouploader to fix tutorial popup dismissal."""
import sys

path = '/root/content-bot/venv/lib/python3.12/site-packages/tiktokautouploader/function.py'

with open(path) as f:
    content = f.read()

# The old code that causes DOM detachment issues
old_block = """    # [PATCH] Remove React Joyride overlay that blocks Playwright clicks
    page.evaluate("document.querySelectorAll('#react-joyride-portal, .react-joyride__overlay').forEach(e => e.remove())")
    time.sleep(0.3)
    if page.locator("button:has-text('Cancel')").is_visible():
        print("Tutorial pop-up detected, dismissing...")
        page.click("button:has-text('Cancel')")
    if page.locator("button:has-text('Got it')").is_visible():
        page.click("button:has-text('Got it')")"""

new_block = """    # [PATCH] Dismiss tutorial popups via JS to avoid DOM detachment issues
    try:
        page.evaluate('''
            document.querySelectorAll("#react-joyride-portal, .react-joyride__overlay").forEach(e => e.remove());
            for (const btn of document.querySelectorAll("button")) {
                const txt = btn.textContent.trim();
                if (txt === "Cancel" || txt === "Got it" || txt === "Skip") {
                    btn.click();
                }
            }
        ''')
        time.sleep(0.5)
    except Exception:
        pass"""

if old_block in content:
    content = content.replace(old_block, new_block)
    with open(path, 'w') as f:
        f.write(content)
    print('Patched successfully')
else:
    print('Old block not found. Checking for react-joyride...')
    idx = content.find('react-joyride')
    if idx >= 0:
        print(f'Found at position {idx}')
        print(repr(content[max(0,idx-200):idx+400]))
    else:
        print('react-joyride not found')
    sys.exit(1)
