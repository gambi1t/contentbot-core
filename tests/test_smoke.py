"""Smoke test — verify bot.py parses without syntax errors
and key functions/variables exist.

Run: python tests/test_smoke.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BOT_PY = Path(__file__).parent.parent / "bot.py"
ASSEMBLER_PY = Path(__file__).parent.parent / "video_assembler.py"
SUBTITLE_PY = Path(__file__).parent.parent / "subtitle_burner.py"


def check_syntax(filepath: Path) -> list[str]:
    """Parse file as AST, return errors if any."""
    errors = []
    try:
        source = filepath.read_text(encoding="utf-8")
        ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        errors.append(f"FAIL Syntax error in {filepath.name}: line {e.lineno}: {e.msg}")
    return errors


def check_required_names(filepath: Path, names: list[str]) -> list[str]:
    """Check that top-level names (functions, variables) exist in the AST."""
    errors = []
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source)

    top_names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    top_names.add(target.id)

    for name in names:
        if name not in top_names:
            errors.append(f"FAIL Required name '{name}' not found in {filepath.name}")

    return errors


def main() -> int:
    errors = []

    # 1. Syntax check all Python files
    print("-- Syntax checks --")
    for f in [BOT_PY, ASSEMBLER_PY, SUBTITLE_PY]:
        if f.exists():
            errs = check_syntax(f)
            if errs:
                errors.extend(errs)
                for e in errs:
                    print(f"  {e}")
            else:
                print(f"  OK {f.name} parses correctly")
        else:
            print(f"  SKIP {f.name} not found")

    # 2. Check required names in bot.py
    print("\n-- Required names in bot.py --")
    required_bot = [
        "notion",                    # Notion client (global)
        "generate_voiceover",        # TTS dispatcher
        "generate_speech_fish",      # Fish Audio TTS
        "trim_long_silences",        # Silence trimmer
        "_project_dir",              # Project dir resolver
        "_save_to_project",          # Save file to project
    ]
    errs = check_required_names(BOT_PY, required_bot)
    if errs:
        errors.extend(errs)
        for e in errs:
            print(f"  {e}")
    else:
        print(f"  OK All {len(required_bot)} required names found")

    # 3. Check required names in video_assembler.py
    print("\n-- Required names in video_assembler.py --")
    required_asm = [
        "assemble_auto_montage",
        "_assemble_split",
        "_assemble_dynamic",
    ]
    if ASSEMBLER_PY.exists():
        errs = check_required_names(ASSEMBLER_PY, required_asm)
        if errs:
            errors.extend(errs)
            for e in errs:
                print(f"  {e}")
        else:
            print(f"  OK All {len(required_asm)} required names found")

    # 4. Check required names in subtitle_burner.py
    print("\n-- Required names in subtitle_burner.py --")
    required_sub = [
        "transcribe_words",
        "generate_ass",
        "burn_subtitles",
        "add_subtitles_to_video",
    ]
    if SUBTITLE_PY.exists():
        errs = check_required_names(SUBTITLE_PY, required_sub)
        if errs:
            errors.extend(errs)
            for e in errs:
                print(f"  {e}")
        else:
            print(f"  OK All {len(required_sub)} required names found")

    # Summary
    print(f"\n{'=' * 50}")
    if errors:
        print(f"Found {len(errors)} issue(s)")
        return 1
    else:
        print("OK All smoke checks passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
