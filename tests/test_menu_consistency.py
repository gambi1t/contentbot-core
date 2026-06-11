"""Menu consistency checker — finds all callback_data in buttons
and verifies every one has a handler. Also checks that the same
action appears in all menus where it logically should.

Run: python tests/test_menu_consistency.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from collections import defaultdict

BOT_PY = Path(__file__).parent.parent / "bot.py"

# ── 1. Extract all callback_data from InlineKeyboardButton ──────────────────

RE_CALLBACK = re.compile(
    r'callback_data\s*=\s*[f]?["\']([^"\'{}]+(?:\{[^}]+\}[^"\'{}]*)*)["\']'
)

# Patterns that mark a handler: query.data == "X", query.data.startswith("X"),
# effective_action == "X"
RE_HANDLER_EQ = re.compile(
    r'(?:query\.data|effective_action)\s*==\s*["\']([^"\']+)["\']'
)
RE_HANDLER_SW = re.compile(
    r'query\.data\.startswith\(\s*["\']([^"\']+)["\']'
)


def normalize_callback(raw: str) -> str:
    """Strip f-string parts to get the static prefix.
    e.g. 'card_broll:{full_id[:20]}' -> 'card_broll:'
         'broll_approve' -> 'broll_approve'
    """
    # Remove {expressions}
    cleaned = re.sub(r'\{[^}]*\}', '', raw)
    return cleaned


def extract_callbacks(code: str) -> dict[str, list[int]]:
    """Return {callback_prefix: [line_numbers]}."""
    results = defaultdict(list)
    for i, line in enumerate(code.splitlines(), 1):
        for m in RE_CALLBACK.finditer(line):
            raw = m.group(1)
            prefix = normalize_callback(raw)
            results[prefix].append(i)
    return dict(results)


def extract_handlers(code: str) -> set[str]:
    """Return set of handled callback prefixes."""
    handlers = set()
    for m in RE_HANDLER_EQ.finditer(code):
        handlers.add(m.group(1))
    for m in RE_HANDLER_SW.finditer(code):
        handlers.add(m.group(1))
    return handlers


# ── 2. Check that related actions appear in all expected menus ──────────────

# Define groups of "menus" by their marker text/variable
# and which callbacks SHOULD appear in each
MENU_MARKERS = {
    "card_menu": {
        # The main /cards card detail menu
        "marker": "# Action buttons — always available",
        "expected_callbacks": [
            "card_voice:", "card_broll:", "card_avatar:", "card_assemble:",
            "crosspost:", "card_statuses:", "download_project",
        ],
    },
    "finish_menu": {
        # "Что дальше?" menu after pressing Готово
        "marker": '# Build full "what\'s next" menu',
        "expected_callbacks": [
            "heygen_looks", "voiceover", "broll", "card_assemble:",
            "crosspost:", "download_project", "finish_final",
        ],
    },
}


def check_menu_completeness(code: str) -> list[str]:
    """Check that expected callbacks exist in their respective menu sections."""
    warnings = []
    lines = code.splitlines()

    for menu_name, spec in MENU_MARKERS.items():
        marker = spec["marker"]
        expected = spec["expected_callbacks"]

        # Find the marker line
        marker_line = None
        for i, line in enumerate(lines):
            if marker in line:
                marker_line = i
                break

        if marker_line is None:
            warnings.append(f"WARN  Menu '{menu_name}': marker not found in code")
            continue

        # Look at the next 80 lines for callbacks
        section = "\n".join(lines[marker_line:marker_line + 80])
        section_callbacks = set()
        for m in RE_CALLBACK.finditer(section):
            prefix = normalize_callback(m.group(1))
            section_callbacks.add(prefix)

        for cb in expected:
            found = any(cb in sc for sc in section_callbacks)
            if not found:
                warnings.append(
                    f"WARN  Menu '{menu_name}' (line ~{marker_line+1}): "
                    f"missing callback '{cb}'"
                )

    return warnings


# ── 3. Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    if not BOT_PY.exists():
        print(f"FAIL bot.py not found at {BOT_PY}")
        return 1

    code = BOT_PY.read_text(encoding="utf-8")
    errors = []

    # Check 1: unhandled callbacks
    print("-- Checking callback handlers --")
    callbacks = extract_callbacks(code)
    handlers = extract_handlers(code)

    # Special callbacks that don't need handlers
    SKIP = {"noop", "dismiss", "cancel"}

    unhandled = []
    for cb, line_nums in sorted(callbacks.items()):
        # Check if this callback (or its prefix) is handled
        handled = False
        for h in handlers:
            if cb == h or cb.rstrip(":") == h or cb.startswith(h) or h.startswith(cb.rstrip(":")):
                handled = True
                break
        if cb.rstrip(":") in SKIP:
            handled = True
        # URL buttons (url=...) don't have callback handlers
        if not handled:
            unhandled.append((cb, line_nums))

    if unhandled:
        for cb, lines in unhandled:
            line_str = ", ".join(str(l) for l in lines[:3])
            errors.append(f"FAIL Unhandled callback '{cb}' (lines: {line_str})")
            print(f"  FAIL '{cb}' -> no handler found (lines: {line_str})")
    else:
        print("  OK All callbacks have handlers")

    # Check 2: menu completeness
    print("\n-- Checking menu completeness --")
    menu_warnings = check_menu_completeness(code)
    if menu_warnings:
        for w in menu_warnings:
            print(f"  {w}")
            errors.append(w)
    else:
        print("  OK All menus have expected callbacks")

    # Summary
    print(f"\n{'=' * 50}")
    if errors:
        print(f"Found {len(errors)} issue(s)")
        return 1
    else:
        print("OK All menu checks passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
