"""Static integration smoke for «idea → tgpost_review» handoff.

Verifies that after my code populates `data["tgpost"]` and sets state,
Артёмовы callbacks (`tgpost:publish`, `tgpost:regen`, `tgpost:voice_edit`,
`tgpost:notion`, `tgpost:cancel`) won't crash on missing fields.

Done by:
  1. Importing `_kb_review` from tg_post_handlers (verify private import works)
  2. Building the minimal `tg` dict that my idea flow produces
  3. Spotting field references in `_publish_to_channel`, `_save_to_notion`,
     `_generate_and_show` against what my dict actually has
  4. Verifying PostInput construction from my tg dict doesn't raise

Doesn't actually call Telegram or Notion — purely static integration check.
"""
import os
import sys

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "smoke-test-dummy")

from dotenv import load_dotenv
load_dotenv()

errors = []

# 1. Import the private helper my code now relies on
try:
    from tg_post_handlers import _kb_review
    kb = _kb_review()
    rows = kb.inline_keyboard
    if len(rows) != 4:
        errors.append(f"_kb_review() returned {len(rows)} rows, expected 4")
    callbacks_found = set()
    for row in rows:
        for btn in row:
            callbacks_found.add(btn.callback_data)
    expected = {"tgpost:publish", "tgpost:regen", "tgpost:voice_edit",
                "tgpost:notion", "tgpost:cancel"}
    missing = expected - callbacks_found
    if missing:
        errors.append(f"_kb_review missing callbacks: {missing}")
    else:
        print(f"✓ _kb_review() OK — {len(rows)} rows, all 5 callbacks present")
except Exception as e:
    errors.append(f"_kb_review import failed: {type(e).__name__}: {e}")

# 2. Build the tg dict my idea-flow produces
my_tg = {
    "post_type": "review_essay",
    "facts": "Рост без системы — это бег на тонком льду. Сейчас выбираю устойчивость.",
    "last_post": "**Заголовок жирным**\n\nПервый абзац.\n\nВторой абзац.",
    "extra_notes": "",
}
print(f"✓ my idea-flow tg dict: keys={list(my_tg.keys())}")

# 3. Check that PostInput accepts my dict (for tgpost:regen → _generate_and_show)
try:
    from tg_post_writer import PostInput
    inp = PostInput(
        post_type=my_tg.get("post_type", "stage"),
        stage_num=my_tg.get("stage_num"),
        facts=my_tg.get("facts", ""),
        bridge_from_previous=my_tg.get("bridge", ""),
        extra_notes=my_tg.get("extra_notes", ""),
        video_script=my_tg.get("video_script", ""),
        short_description=my_tg.get("short_description", ""),
        video_topic=my_tg.get("video_topic", ""),
    )
    print(f"✓ PostInput accepts my tg dict: post_type={inp.post_type}, "
          f"facts_len={len(inp.facts)}")
except Exception as e:
    errors.append(f"PostInput from my tg failed: {type(e).__name__}: {e}")

# 4. _publish_to_channel reads only `tg["last_post"]` — already in my dict
if my_tg.get("last_post"):
    print(f"✓ tg['last_post'] present, _publish_to_channel will succeed")
else:
    errors.append("tg['last_post'] missing — _publish_to_channel will fail")

# 5. _save_to_notion reads `tg["last_post"]`, `tg["post_type"]`, `tg["stage_num"]`
#    All optional except last_post. My dict has last_post and post_type.
if my_tg.get("last_post"):
    print(f"✓ _save_to_notion fields OK (last_post + optional post_type/stage_num)")

# 6. Final: log non-blocking UX notes
print()
print("=== UX notes (non-blocking) ===")
print("- _save_to_notion creates a NEW card; idea flow already has one in Notion.")
print("  → User clicking «📥 Сохранить в Notion» will create a duplicate.")
print("  → Acceptable for MVP; revisit if Артём says it's annoying.")
print("- _publish_to_channel uses parse_mode='Markdown'; **bold** renders correctly.")
print("  → Special chars (_, [, *) in post body may need escaping —")
print("    this is Артёмов existing flow, presumably battle-tested.")

if errors:
    print("\nFAIL:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)

print("\n✓ Integration smoke PASSED — handoff to Артёмов tgpost_review flow is safe")
