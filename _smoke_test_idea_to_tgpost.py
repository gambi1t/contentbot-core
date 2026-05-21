"""Runtime smoke test for the «idea → TG-post» pipeline branch.

Verifies that `tg_post_writer.generate_post(facts=thesis, brand="maksim")`
runs end-to-end on a thesis-style input (not a transcript). This is the
new code-path added by the «📝 Написать TG-пост сразу» button.

Usage on server:
    cd /home/maksim-bot/maksim-bot
    sudo -u maksim-bot venv/bin/python _smoke_test_idea_to_tgpost.py

Exit code 0 = OK, non-zero = something broke.
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY not in env")
    sys.exit(2)

import anthropic
import tg_post_writer

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Sample thesis from a real idea (smoke-test 5 ideas output earlier)
thesis = (
    "Рост без системы — это бег на тонком льду. Каждый новый объект, "
    "каждая новая услуга умножает хаос, если фундамент не готов. "
    "Сейчас я выбираю устойчивость, не рост любой ценой."
)
title = "Перестал гнаться за ростом — и бизнес впервые стал устойчивым"

print(f"→ calling tg_post_writer.generate_post(brand='maksim') on thesis...")
print(f"   title: {title}")
print(f"   thesis: {thesis[:80]}...")
print()

try:
    post_text = tg_post_writer.generate_post(
        tg_post_writer.PostInput(
            post_type="review_essay",
            facts=thesis,
        ),
        claude,
        brand="maksim",
    )
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print(f"✓ got post ({len(post_text)} chars)\n")
print("--- POST ---")
print(post_text)
print("--- END ---\n")

# Telegram message length check
TG_LIMIT = 4096
if len(post_text) > TG_LIMIT:
    print(f"⚠️  WARNING: post is {len(post_text) - TG_LIMIT} chars over Telegram limit")
    print(f"   Will need split-send in production")

# Quality smoke checks
checks = {
    "non-empty": len(post_text) >= 200,
    "has multiple paragraphs": post_text.count("\n\n") >= 2,
    "no markdown code blocks": "```" not in post_text,
    "no AI-marker phrases": all(
        m not in post_text.lower()
        for m in ["давайте разберёмся", "итак, ", "таким образом",
                  "в этой статье", "в данной статье"]
    ),
}

print("Quality checks:")
all_pass = True
for name, ok in checks.items():
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        all_pass = False

if not all_pass:
    print("\nSome quality checks failed — review manually before shipping")
    sys.exit(3)

print("\n✓ smoke test PASSED")
