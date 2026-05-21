"""Static integration smoke for new idea→tgpost callback wiring.

Verifies that the new callback handlers I added don't have:
  * typos in callback_data prefixes (so handle_callback actually routes)
  * missing helper imports (so calls don't ImportError at runtime)
  * incorrect state transitions (so user doesn't get stuck mid-flow)

What I DON'T test here (and call out honestly at the end):
  * Real Telegram delivery (no bot token in test env)
  * Real Notion API calls (no test workspace)
  * Voice transcription (no audio fixture)
  * Race conditions (concurrent callbacks)

Exit code 0 = all static checks pass, non-zero = something would break.
"""
import os
import sys
import inspect

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "smoke-test-dummy")

from dotenv import load_dotenv
load_dotenv()

import bot
import crosspost
from telegram import InlineKeyboardMarkup

errors = []
warnings = []


# ── Test 1: _idea_tgpost_keyboard returns valid keyboard ──
print("=" * 60)
print("Test 1: _idea_tgpost_keyboard structure")
print("=" * 60)
try:
    kb_no_photos = bot._idea_tgpost_keyboard(idx=5, photos_count=0)
    kb_with_photos = bot._idea_tgpost_keyboard(idx=5, photos_count=3)
    assert isinstance(kb_no_photos, InlineKeyboardMarkup)
    assert isinstance(kb_with_photos, InlineKeyboardMarkup)
    rows_no = kb_no_photos.inline_keyboard
    rows_with = kb_with_photos.inline_keyboard
    assert len(rows_no) == len(rows_with), "row count must be stable"
    print(f"✓ keyboard returns {len(rows_no)} rows")

    # Find the photo button — must change label based on photos_count
    photo_label_no = None
    photo_label_with = None
    for row_no, row_with in zip(rows_no, rows_with):
        for btn_no, btn_with in zip(row_no, row_with):
            if "Фото" in btn_no.text or "Прикрепить" in btn_no.text:
                photo_label_no = btn_no.text
                photo_label_with = btn_with.text
    if not photo_label_no:
        errors.append("Photo button not found in keyboard")
    elif "(" in photo_label_no:
        errors.append(f"Photo button shows count when 0: «{photo_label_no}»")
    elif "3 выбрано" not in photo_label_with:
        errors.append(f"Photo button doesn't show count when 3: «{photo_label_with}»")
    else:
        print(f"✓ photo label adapts: «{photo_label_no}» → «{photo_label_with}»")

    # All callbacks must be valid strings — no None
    all_callbacks = []
    for row in rows_no:
        for btn in row:
            if btn.callback_data:
                all_callbacks.append(btn.callback_data)
    expected_prefixes = {
        "idea_tgpost_publish:", "idea_tgpost_photos:",
        "tgpost:regen", "tgpost:voice_edit",
        "tgpost_surg_edit_start:",
        "tgpost:notion", "tgpost:cancel",
    }
    found_prefixes = set()
    for cb in all_callbacks:
        for p in expected_prefixes:
            if cb.startswith(p) or cb == p:
                found_prefixes.add(p)
    missing = expected_prefixes - found_prefixes
    if missing:
        errors.append(f"Missing callback prefixes: {missing}")
    else:
        print(f"✓ all 7 expected callback prefixes present: {found_prefixes}")
except Exception as e:
    errors.append(f"keyboard test crashed: {type(e).__name__}: {e}")


# ── Test 2: _render_tgpost_html on edge cases ──
print()
print("=" * 60)
print("Test 2: _render_tgpost_html edge cases")
print("=" * 60)
try:
    cases = [
        ("**Bold only**", "<b>Bold only</b>"),
        ("Plain text", "Plain text"),
        ("Mix **bold** in middle", "Mix <b>bold</b> in middle"),
        ("HTML chars: A < B & C > D", "HTML chars: A &lt; B &amp; C &gt; D"),
        ("**Bold** with <b>HTML-like</b>", "<b>Bold</b> with &lt;b&gt;HTML-like&lt;/b&gt;"),
        ("", ""),
        ("**Multi**\n\n**Headers**", "<b>Multi</b>\n\n<b>Headers</b>"),
    ]
    for inp, expected in cases:
        out = bot._render_tgpost_html(inp)
        if out != expected:
            errors.append(f"_render_tgpost_html: input={inp!r}, expected={expected!r}, got={out!r}")
    print(f"✓ tested {len(cases)} edge cases — all match")
except Exception as e:
    errors.append(f"_render_tgpost_html test crashed: {type(e).__name__}: {e}")


# ── Test 3: telegram_post_to_channel signature ──
print()
print("=" * 60)
print("Test 3: telegram_post_to_channel accepts photos parameter")
print("=" * 60)
try:
    sig = inspect.signature(crosspost.telegram_post_to_channel)
    params = list(sig.parameters.keys())
    if "photos" not in params:
        errors.append(f"telegram_post_to_channel missing `photos` param. Has: {params}")
    else:
        print(f"✓ telegram_post_to_channel signature: {params}")
except Exception as e:
    errors.append(f"signature check crashed: {type(e).__name__}: {e}")


# ── Test 4: handle_callback contains all my new callback prefixes ──
print()
print("=" * 60)
print("Test 4: handle_callback routes new callbacks")
print("=" * 60)
try:
    src = inspect.getsource(bot.handle_callback)
    new_prefixes = [
        '"idea_tgpost_photos:"',
        '"idea_tgpost_publish:"',
        '"tgpost_surg_edit_start:"',
        '"tgpost_surg_edit_cancel"',
    ]
    for prefix in new_prefixes:
        if prefix not in src:
            errors.append(f"handle_callback doesn't reference {prefix}")
        else:
            print(f"✓ handle_callback handles {prefix}")
except Exception as e:
    errors.append(f"handle_callback inspection crashed: {type(e).__name__}: {e}")


# ── Test 5: tgphoto_done branching logic exists ──
print()
print("=" * 60)
print("Test 5: tgphoto_done knows about idea-flow return")
print("=" * 60)
try:
    src = inspect.getsource(bot.handle_callback)
    if 'tgphoto_return_idea_idx' not in src:
        errors.append("tgphoto_done missing idea-flow branch (`tgphoto_return_idea_idx`)")
    else:
        print(f"✓ tgphoto_done references tgphoto_return_idea_idx")
    # Critical: must POP the key so subsequent flows don't accidentally
    # re-route (state hygiene)
    if 'data.pop("tgphoto_return_idea_idx"' not in src:
        warnings.append("tgphoto_done may not pop tgphoto_return_idea_idx — state could leak")
    else:
        print(f"✓ tgphoto_done pops tgphoto_return_idea_idx (state hygiene)")
except Exception as e:
    errors.append(f"tgphoto_done check crashed: {type(e).__name__}: {e}")


# ── Test 6: state transitions don't collide ──
print()
print("=" * 60)
print("Test 6: state names don't collide with existing")
print("=" * 60)
try:
    # My new state name (renamed 13 May 2026 after Test 6 caught collision:
    # `tgpost_surg_editing` started with `tgpost_` and was wrongly
    # intercepted by Артёмов is_tgpost_state)
    my_state = "idea_post_surg_edit"
    # Must NOT match Артёмов is_tgpost_state regex (`tgpost_wait_*`)
    from tg_post_handlers import is_tgpost_state
    if is_tgpost_state(my_state):
        errors.append(f"State `{my_state}` would be intercepted by Артёмов is_tgpost_state — collision!")
    else:
        print(f"✓ state `{my_state}` doesn't collide with Артёмов tgpost_wait_*")
except Exception as e:
    errors.append(f"state collision check crashed: {type(e).__name__}: {e}")


# ── Test 7: keyboard callbacks within Telegram 64-char limit ──
print()
print("=" * 60)
print("Test 7: callback_data length within Telegram limit (64 bytes)")
print("=" * 60)
try:
    kb = bot._idea_tgpost_keyboard(idx=999999, photos_count=10)
    over_limit = []
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data and len(btn.callback_data.encode("utf-8")) > 64:
                over_limit.append((btn.text, btn.callback_data, len(btn.callback_data)))
    if over_limit:
        for text, cb, n in over_limit:
            errors.append(f"callback over 64 bytes: «{text}» → {cb} ({n} bytes)")
    else:
        print(f"✓ all callbacks under 64-byte Telegram limit")
except Exception as e:
    errors.append(f"length check crashed: {type(e).__name__}: {e}")


# ── Final report ──
print()
print("=" * 60)
print("FINAL REPORT")
print("=" * 60)
if warnings:
    print("Warnings (non-fatal):")
    for w in warnings:
        print(f"  ⚠ {w}")
if errors:
    print("Errors:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
print("✓ All static integration checks PASSED")
print()
print("=== What this test does NOT verify (honest disclosure) ===")
print("- Real Telegram callback delivery (no bot token here)")
print("- Real telegram_post_to_channel send (no actual channel write)")
print("- Voice intake → Whisper → surgical edit (no audio fixture)")
print("- tgphoto_menu rendering (requires `query` mock with chat_id)")
print("- Concurrent callback handling (race conditions)")
print()
print("These need either UI testing in real bot or much heavier mocks.")
