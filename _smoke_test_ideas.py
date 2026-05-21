"""Runtime smoke test for idea_generator.

Runs a minimal 2-idea generation against the real Anthropic API to
verify the model contract (model id, prefill behaviour, output format)
BEFORE the user clicks the button in Telegram.

Usage on server:
    cd /home/maksim-bot/maksim-bot
    sudo -u maksim-bot venv/bin/python _smoke_test_ideas.py

Exit code 0 = OK, non-zero = something broke.
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY not in env")
    sys.exit(2)

import anthropic
import idea_generator

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# n=5 — same as production button (Артём уменьшил 10→5 13 May 2026).
# Still useful as length check — though smaller, rendered output stays
# safely under Telegram 4096 limit.
print("→ calling generate_ideas(brand='maksim', n=5)...")
try:
    ideas = idea_generator.generate_ideas(
        claude, "maksim", exclude_titles=[], n=5,
    )
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    sys.exit(1)

print(f"\n✓ got {len(ideas)} ideas\n")
for i, idea in enumerate(ideas, 1):
    print(f"--- #{i} ---")
    print(json.dumps(idea, ensure_ascii=False, indent=2))
    print()

# Telegram message length check (production-realistic)
rendered = idea_generator.format_ideas_message(ideas)
TELEGRAM_HARD_LIMIT = 4096
TELEGRAM_SAFE_LIMIT = 3800  # leave headroom for ZWJ emoji etc
print(f"\n→ rendered message length: {len(rendered)} chars "
      f"(hard limit {TELEGRAM_HARD_LIMIT}, safe {TELEGRAM_SAFE_LIMIT})")
if len(rendered) > TELEGRAM_HARD_LIMIT:
    print(f"FAIL: rendered message exceeds Telegram limit by "
          f"{len(rendered) - TELEGRAM_HARD_LIMIT} chars")
    print(f"   (this is what caused «Message_too_long» in production)")
    sys.exit(4)
if len(rendered) > TELEGRAM_SAFE_LIMIT:
    print(f"⚠️  WARNING: message is in the unsafe zone "
          f"({len(rendered)} > {TELEGRAM_SAFE_LIMIT}). "
          f"Will work today but vulnerable to longer titles.")
else:
    print(f"✓ rendered length fits with margin")

# Validate schema fields
required = ("title", "hook_draft", "central_thesis", "niche",
            "format_type", "format_subtype", "audience",
            "cta_hint", "why_works")
missing_report = []
for i, idea in enumerate(ideas, 1):
    miss = [f for f in required if f not in idea or not str(idea.get(f, "")).strip()]
    if miss:
        missing_report.append((i, miss))

if missing_report:
    print("⚠️  Schema validation issues:")
    for i, miss in missing_report:
        print(f"   idea #{i} missing/empty: {miss}")
    sys.exit(3)

print("✓ all ideas pass schema validation")
print("✓ smoke test PASSED")
