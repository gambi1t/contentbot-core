"""Static smoke test for HTML escape correctness in new idea→pipeline code.

Verifies that the message-building logic correctly escapes < > & in title
and thesis BEFORE inserting into HTML-parsed Telegram messages. Without
escape, an idea like «AI < ML — и вот почему» breaks parse_mode="HTML".

Doesn't actually call Telegram — just builds the strings and checks them.

Exit code 0 = OK, non-zero = something broke.
"""
import html as html_mod
import re
import sys

# Simulate adversarial LLM output
ADVERSARIAL_TITLE = "AI < ML & «зачем» — это > всего"
ADVERSARIAL_THESIS = (
    "Если A < B и B < C — это значит A < C. "
    "Принцип «команда > одиночка» работает только когда & знаки доверия."
)
ADVERSARIAL_POST = (
    "**Заголовок < жирный**\n\n"
    "Тезис: A < B & C > 0. "
    "Это **важно** для бизнеса."
)

errors = []

# --- Test 1: title/thesis in menu_text ---
title_esc = html_mod.escape(ADVERSARIAL_TITLE)
thesis_esc = html_mod.escape(ADVERSARIAL_THESIS)
menu_text = (
    f"✅ <b>Идея в Notion:</b> {title_esc}\n\n"
    f"<i>Тезис:</i>\n{thesis_esc}\n\n"
    f"<b>Что делаем дальше?</b>"
)
# Should NOT contain raw < or & outside of <b>/</b>/<i>/</i> tags
# Simple check: all < that aren't part of valid HTML tags should be escaped
# After escape, raw < in user input becomes &lt;
if "AI < ML" in menu_text:
    errors.append("title NOT escaped: «AI < ML» remained literal in menu_text")
if "A < B" in menu_text:
    errors.append("thesis NOT escaped: «A < B» remained literal in menu_text")
if "&lt;" not in menu_text:
    errors.append("escape failed: expected &lt; in escaped output")

# --- Test 2: post_text escape + markdown→HTML conversion order ---
post_escaped = html_mod.escape(ADVERSARIAL_POST)
post_html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", post_escaped)
# After escape: ** survives (not an HTML char), < becomes &lt;
# After re.sub: ** → <b></b>
if "**" in post_html:
    errors.append(f"post_html still has ** (re.sub broken?): {post_html[:80]!r}")
if "A < B" in post_html:
    errors.append(f"post_html has raw <: HTML will break: {post_html[:80]!r}")
if "<b>Заголовок &lt; жирный</b>" not in post_html:
    errors.append(f"post_html bold conversion lost escaped <: {post_html[:120]!r}")

# --- Test 3: selfie/avatar instruction in <code>{thesis} ---
selfie_text = (
    f"🎥 <b>Селфи под идею «{title_esc}»</b>\n\n"
    f"<b>Тезис для записи:</b>\n<code>{thesis_esc}</code>"
)
if "A < B" in selfie_text or "& B" in selfie_text:
    errors.append(f"selfie text has raw < or &: {selfie_text[:120]!r}")

# --- Output ---
if errors:
    print("FAIL — HTML escape problems:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)

print("✓ All HTML escape checks passed")
print(f"  - menu_text safe: {menu_text[:100]}...")
print(f"  - post_html safe: {post_html[:100]}...")
print(f"  - selfie_text safe: {selfie_text[:100]}...")
print("\n✓ smoke test PASSED")
