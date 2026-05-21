"""Runtime smoke for the hook_draft injection fix.

13 May 2026 — Артём caught: «хук в превью банка идей лучше, чем хук
в готовом посте». Cause: idea_pipeline:tgpost extracted hook_draft but
never passed it to tg_post_writer. Fixed by injecting it via PostInput
extra_notes with an explicit «ОБЯЗАТЕЛЬНО используй» directive.

This test verifies the fix by:
  1. Calling tg_post_writer.generate_post with extra_notes containing
     a distinctive hook
  2. Checking that the generated post's opening contains a recognizable
     fragment of that hook (light adaptation allowed by the directive)

If the hook is reliably preserved → Opus is honoring the instruction.
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

# Distinctive hook with a memorable phrasing — easy to spot in output
HOOK = "Клиент сам не знает, чего хочет — и в этом твой шанс"
THESIS = (
    "Половина клиентов в активном отдыхе не умеют формулировать. "
    "Если научишься слышать что им НА САМОМ ДЕЛЕ нужно — у тебя "
    "нет конкурентов по цене."
)
extra_notes = (
    f"ОБЯЗАТЕЛЬНО используй этот хук в заголовке поста "
    f"(можно адаптировать формулировку, но СОХРАНИ силу "
    f"и смысл; не заменяй на свой generic-вариант): «{HOOK}»"
)

print(f"→ Hook to inject: «{HOOK}»")
print(f"→ Thesis: {THESIS[:60]}...")
print()

try:
    post_text = tg_post_writer.generate_post(
        tg_post_writer.PostInput(
            post_type="review_essay",
            facts=THESIS,
            extra_notes=extra_notes,
        ),
        claude,
        brand="maksim",
    )
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    sys.exit(1)

print(f"✓ got post ({len(post_text)} chars)")
print()
print("=== FIRST 400 chars of generated post ===")
print(post_text[:400])
print("=" * 50)
print()

# Heuristic check — does the OPENING (first 400 chars) contain a
# recognizable hook fragment? Look for distinctive content words:
#   «клиент», «не знает», «чего хочет», «шанс»
opening = post_text[:400].lower()
markers_found = []
for marker in ("клиент", "не знает", "чего хочет", "шанс"):
    if marker in opening:
        markers_found.append(marker)

print(f"Hook markers found in opening: {markers_found}")
if len(markers_found) >= 2:
    print(f"✓ Hook preserved (≥2 markers — Opus honored extra_notes directive)")
else:
    print(f"✗ Hook NOT preserved — Opus ignored extra_notes")
    print(f"  This means the fix didn't work as intended. Check tg_post_writer's")
    print(f"  prompt to ensure it injects extra_notes into Opus's input.")
    sys.exit(3)

print()
print("✓ smoke test PASSED — hook_draft successfully injected via extra_notes")
