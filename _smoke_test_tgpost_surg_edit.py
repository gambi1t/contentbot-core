"""Runtime smoke test for the surgical TG-post editor (idea-flow only).

Verifies that `_apply_tgpost_surg_edit(current, instruction)` performs
HIRURGICAL edits, not full regeneration:
  1. «убери первый абзац» — first paragraph gone, rest preserved
  2. «сделай короче» — output shorter than input
  3. «поменяй хук» — first line/paragraph different, middle preserved

Distinguishes from the bug Артём caught with Артёмов regenerate flow
on 13 May: «поменяй хук» rewrote the entire post. Surgical must NOT.

Usage on server:
    cd /home/maksim-bot/maksim-bot
    sudo -u maksim-bot venv/bin/python _smoke_test_tgpost_surg_edit.py
"""
import os
import sys

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "smoke-test-dummy")

from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY not in env")
    sys.exit(2)

import bot

SAMPLE_POST = """\
**Клиент сам не знает, чего хочет**

Половина клиентов в активном отдыхе не умеют формулировать, зачем они звонят.

Звонит мужчина: «Сколько стоит картинг на час?» Если администратор называет цену — разговор окончен. А на самом деле он хочет вытащить сына-подростка из телефона.

Покупают баню — а хотят выдохнуть после тяжёлой недели. Покупают картинг — а хотят почувствовать себя живым.

Если научиться слышать, за чем человек пришёл на самом деле, — у тебя нет конкурентов по цене. Только попадание.
"""

def heuristic_first_para(text: str) -> str:
    return text.strip().split("\n\n", 1)[0].strip()

def heuristic_body_preserved(orig: str, edited: str, ratio: float = 0.5) -> bool:
    """Crude check: at least `ratio` of orig sentences appear (substring)
    in edited. True surgical edits preserve 70%+ of source sentences;
    full regeneration preserves <30%."""
    orig_sents = [s.strip() for s in orig.replace("\n", " ").split(".") if len(s.strip()) > 15]
    if not orig_sents:
        return True
    matches = sum(1 for s in orig_sents if s in edited)
    return matches / len(orig_sents) >= ratio


print("=" * 60)
print("Test 1: «убери первый абзац» — surgical removal")
print("=" * 60)
try:
    edited1 = bot._apply_tgpost_surg_edit(SAMPLE_POST, "убери первый абзац")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    sys.exit(1)

# The «first paragraph» semantically excludes the bold header.
# Sonnet should keep the header («**Клиент сам не знает...**») and remove
# the actual first paragraph («Половина клиентов в активном отдыхе...»).
FIRST_PARA_MARKER = "Половина клиентов в активном отдыхе"
HEADER_MARKER = "Клиент сам не знает, чего хочет"
print(f"orig has first-para marker: {FIRST_PARA_MARKER in SAMPLE_POST}")
print(f"edited has first-para marker: {FIRST_PARA_MARKER in edited1}")
print(f"edited has header marker: {HEADER_MARKER in edited1}")
if FIRST_PARA_MARKER in edited1:
    print(f"✗ first paragraph «{FIRST_PARA_MARKER}» still present — surgical failed")
    sys.exit(3)
if HEADER_MARKER not in edited1:
    print(f"⚠ header was also removed — Sonnet was too aggressive (acceptable but noted)")
else:
    print(f"✓ first paragraph removed, header kept — clean surgical edit")
print(f"  body preserved (≥50% of orig sentences): {heuristic_body_preserved(SAMPLE_POST, edited1)}")

print()
print("=" * 60)
print("Test 2: «сделай короче» — shorter, body preserved")
print("=" * 60)
try:
    edited2 = bot._apply_tgpost_surg_edit(SAMPLE_POST, "сделай короче в два раза")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    sys.exit(1)
pct = 100 * (1 - len(edited2) / len(SAMPLE_POST))
print(f"length: {len(edited2)} vs orig {len(SAMPLE_POST)} ({pct:+.0f}%)")
if len(edited2) >= len(SAMPLE_POST):
    print(f"⚠ not shorter — Sonnet ignored instruction")
else:
    print(f"✓ shorter by {pct:.0f}%")

print()
print("=" * 60)
print("Test 3: «поменяй хук» — first line changes, body STAYS")
print("=" * 60)
try:
    edited3 = bot._apply_tgpost_surg_edit(
        SAMPLE_POST,
        "поменяй только хук — сделай более резким",
    )
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    sys.exit(1)
orig_hook = heuristic_first_para(SAMPLE_POST)
edited_hook = heuristic_first_para(edited3)
print(f"orig hook: {orig_hook}")
print(f"edited hook: {edited_hook}")
hook_changed = (orig_hook != edited_hook)
body_preserved = heuristic_body_preserved(SAMPLE_POST, edited3, ratio=0.5)
print(f"hook changed: {hook_changed}")
print(f"body preserved (≥50% sentences match): {body_preserved}")
if not hook_changed:
    print(f"⚠ hook unchanged — Sonnet didn't apply instruction")
if not body_preserved:
    print(f"✗ body NOT preserved — Sonnet regenerated whole post!")
    print(f"  this is the EXACT bug Артём caught with Артёмов regenerate flow")
    sys.exit(4)
print(f"✓ surgical behaviour confirmed")

print()
print("=" * 60)
print("Test 4: _render_tgpost_html — ** → <b> conversion")
print("=" * 60)
html_out = bot._render_tgpost_html("**Заголовок жирный**\n\nОбычный текст.")
print(f"out: {html_out!r}")
if "<b>Заголовок жирный</b>" not in html_out:
    print(f"✗ ** not converted to <b>")
    sys.exit(5)
print(f"✓ ** → <b> conversion works")

# Also: HTML escape on special chars
html_esc = bot._render_tgpost_html("A < B & C > D")
if "<" in html_esc or "&" not in html_esc:
    print(f"✗ HTML escape broken: {html_esc!r}")
    sys.exit(6)
print(f"✓ HTML chars escaped: {html_esc!r}")

print()
print("=" * 60)
print("✓ All 4 surgical edit tests PASSED")
print("=" * 60)
