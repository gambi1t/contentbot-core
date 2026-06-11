"""Unit tests for subtitle_burner: brand fix-ups + punctuation merging.

Two post-processing passes after whisper transcription:

1. ``fix_brand_names`` — greedy windowed matcher that tries to combine 1..4
   adjacent tokens against a brand dictionary. Only merges when the combined
   normalized form literally matches a known brand, so natural speech is
   NEVER glued together by accident.
2. ``merge_whisper_fragments`` — attaches standalone punctuation tokens to
   the previous word. Nothing else is merged (earlier versions tried to use
   timestamp gaps, which catastrophically glued whole sentences into one
   flash — see the Apr 15 2026 regression with "ЙВОСЬМЕРКИАІСНОВАСТ").

Regression cases:
- "Мед" + "жорни" → "Midjourney" (whisper mis-hears the brand)
- "V" + "8" + "." + "1" → "V8.1" (version split into 4 tokens)
- 50 natural words with ~0 gaps must stay 50 separate words

Run: python tests/test_subtitle_merge.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")

sys.path.insert(0, str(Path(__file__).parent.parent))

from subtitle_burner import fix_brand_names, merge_whisper_fragments  # noqa: E402


def _assert(cond: bool, msg: str, errors: list[str]) -> None:
    if not cond:
        errors.append(f"FAIL {msg}")
        print(f"  FAIL {msg}")
    else:
        print(f"  OK {msg}")


def w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


# ── merge_whisper_fragments (punctuation-only) tests ──────────────────────────

def test_merge_empty(errors: list[str]) -> None:
    print("\n-- merge: empty input --")
    _assert(merge_whisper_fragments([]) == [], "empty → empty", errors)


def test_merge_single(errors: list[str]) -> None:
    print("\n-- merge: single word --")
    result = merge_whisper_fragments([w("Привет", 0.0, 0.5)])
    _assert(len(result) == 1 and result[0]["word"] == "Привет",
            "single word preserved", errors)


def test_merge_punctuation_attached(errors: list[str]) -> None:
    """Comma/period as their own tokens should stick to previous word."""
    print("\n-- merge: standalone punctuation → attached --")
    words = [
        w("Привет", 0.0, 0.5),
        w(",",      0.5, 0.52),
        w("мир",    0.6, 0.9),
        w("!",      0.9, 0.92),
    ]
    result = merge_whisper_fragments(words)
    _assert(len(result) == 2, f"4 tokens → 2 words (got {len(result)})", errors)
    _assert(result[0]["word"] == "Привет,",
            f"comma attached (got {result[0]['word']!r})", errors)
    _assert(result[1]["word"] == "мир!",
            f"bang attached (got {result[1]['word']!r})", errors)


def test_merge_natural_speech_stays_split(errors: list[str]) -> None:
    """CRITICAL: natural whisper output with ~0 gaps between words must NOT
    be merged. This is the regression from the Apr 15 2026 broken deploy
    where "й" + "восьмерки" + "и" + "снова" + "ст..." got glued into a
    single on-screen blob 'ЙВОСЬМЕРКИАІСНОВАСТ'.
    """
    print("\n-- merge: natural speech (tight timestamps) stays split --")
    # Simulate 12 real whisper words with end-of-N == start-of-N+1
    raw = [
        ("Разбираем",  0.00, 0.48),
        ("новую",      0.48, 0.72),
        ("версию",     0.72, 1.05),
        ("восьмерки",  1.05, 1.55),
        ("и",          1.55, 1.62),
        ("снова",      1.62, 1.95),
        ("ставим",     1.95, 2.30),
        ("лайки",      2.30, 2.70),
        ("потому",     2.70, 3.05),
        ("что",        3.05, 3.20),
        ("это",        3.20, 3.40),
        ("бомба",      3.40, 3.80),
    ]
    words = [w(t, s, e) for t, s, e in raw]
    result = merge_whisper_fragments(words)
    _assert(len(result) == 12,
            f"12 real words stay 12 flashes (got {len(result)})", errors)
    _assert(all(r["word"] == t for r, (t, _, _) in zip(result, raw)),
            "words unchanged by merger", errors)


def test_merge_no_mutation(errors: list[str]) -> None:
    print("\n-- merge: input not mutated --")
    words = [w("Привет", 0.0, 0.5), w(",", 0.5, 0.52), w("мир", 0.6, 0.9)]
    snapshot = [dict(x) for x in words]
    _ = merge_whisper_fragments(words)
    _assert(words == snapshot, "input unchanged", errors)


# ── fix_brand_names (windowed brand matcher) tests ────────────────────────────

def test_brand_single_token_direct(errors: list[str]) -> None:
    """Single-token mis-hearing: 'Меджорни' → 'Midjourney'."""
    print("\n-- brand: single-token Меджорни → Midjourney --")
    result = fix_brand_names([w("Меджорни", 0.5, 1.1)])
    _assert(result[0]["word"] == "Midjourney",
            f"got {result[0]['word']!r}", errors)
    _assert(result[0]["start"] == 0.5 and result[0]["end"] == 1.1,
            "timestamps preserved", errors)


def test_brand_two_token_window(errors: list[str]) -> None:
    """Whisper fragment split: 'Мед' + 'жорни' → single 'Midjourney'."""
    print("\n-- brand: 2-token window 'Мед'+'жорни' → 'Midjourney' --")
    words = [
        w("Разбираем", 0.0, 0.6),
        w("Мед",       0.7, 0.9),
        w("жорни",     0.9, 1.3),
        w("восемь",    1.5, 1.9),
    ]
    result = fix_brand_names(words)
    texts = [r["word"] for r in result]
    _assert(len(result) == 3, f"4 tokens → 3 (got {len(result)}: {texts})", errors)
    _assert("Midjourney" in texts, f"brand present (got {texts})", errors)
    mj = [r for r in result if r["word"] == "Midjourney"][0]
    _assert(mj["start"] == 0.7 and mj["end"] == 1.3,
            f"outer timestamps (got {mj['start']}..{mj['end']})", errors)


def test_brand_four_token_version(errors: list[str]) -> None:
    """Version split: 'V' + '8' + '.' + '1' → single 'V8.1'."""
    print("\n-- brand: 4-token window 'V'+'8'+'.'+'1' → 'V8.1' --")
    words = [
        w("версия",   0.0, 0.4),
        w("V",        0.5, 0.6),
        w("8",        0.6, 0.75),
        w(".",        0.75, 0.78),
        w("1",        0.78, 0.9),
        w("доступна", 1.1, 1.6),
    ]
    result = fix_brand_names(words)
    texts = [r["word"] for r in result]
    _assert(len(result) == 3, f"6 → 3 (got {len(result)}: {texts})", errors)
    _assert("V8.1" in texts, f"V8.1 present (got {texts})", errors)


def test_brand_trailing_punctuation(errors: list[str]) -> None:
    """Trailing comma on single-token brand is preserved."""
    print("\n-- brand: 'Меджорни,' → 'Midjourney,' --")
    result = fix_brand_names([w("Меджорни,", 0.5, 1.1)])
    _assert(result[0]["word"] == "Midjourney,",
            f"got {result[0]['word']!r}", errors)


def test_brand_case_insensitive(errors: list[str]) -> None:
    print("\n-- brand: case-insensitive match --")
    result = fix_brand_names([w("МЕДЖОРНИ", 0.0, 0.4)])
    _assert(result[0]["word"] == "Midjourney",
            f"got {result[0]['word']!r}", errors)


def test_brand_correct_transliteration(errors: list[str]) -> None:
    """Correct Russian transliteration 'Миджорни' is also normalized."""
    print("\n-- brand: 'Миджорни' (correct) → 'Midjourney' --")
    result = fix_brand_names([w("Миджорни", 0.0, 0.4)])
    _assert(result[0]["word"] == "Midjourney",
            f"got {result[0]['word']!r}", errors)


def test_brand_heygen(errors: list[str]) -> None:
    print("\n-- brand: 'хейген' → 'HeyGen' --")
    result = fix_brand_names([w("хейген", 0.0, 0.4)])
    _assert(result[0]["word"] == "HeyGen",
            f"got {result[0]['word']!r}", errors)


def test_brand_leaves_normal_speech_alone(errors: list[str]) -> None:
    """CRITICAL: regular Russian words must NOT be merged or replaced.

    This is the regression for the broken Apr 15 2026 deploy. The windowed
    matcher should NEVER glue together unrelated words just because they
    are consecutive.
    """
    print("\n-- brand: 10 normal words stay 10 words --")
    raw_words = [
        "Сегодня", "мы", "разбираем", "новую", "модель",
        "и", "сравниваем", "её", "с", "прошлой",
    ]
    words = [w(t, i * 0.3, i * 0.3 + 0.25) for i, t in enumerate(raw_words)]
    result = fix_brand_names(words)
    texts = [r["word"] for r in result]
    _assert(len(result) == 10,
            f"10 words stay 10 (got {len(result)}: {texts})", errors)
    _assert(texts == raw_words, f"words unchanged (got {texts})", errors)


def test_brand_longest_window_wins(errors: list[str]) -> None:
    """Greedy longest-match: 'V' + '8' should prefer 'V8' window over
    two single-token matches."""
    print("\n-- brand: longest window wins --")
    words = [w("V", 0.0, 0.1), w("8", 0.1, 0.2)]
    result = fix_brand_names(words)
    _assert(len(result) == 1 and result[0]["word"] == "V8",
            f"2 → 1 V8 (got {[r['word'] for r in result]})", errors)


def test_brand_no_mutation(errors: list[str]) -> None:
    print("\n-- brand: input not mutated --")
    words = [w("Меджорни", 0.0, 0.4)]
    snapshot = [dict(x) for x in words]
    _ = fix_brand_names(words)
    _assert(words == snapshot, "input unchanged", errors)


# ── end-to-end pipeline tests ─────────────────────────────────────────────────

def test_pipeline_midjourney(errors: list[str]) -> None:
    """Full pipeline: brand fix + punct merge on real-ish whisper data."""
    print("\n-- pipeline: brand fix + punct merge (Midjourney) --")
    words = [
        w("Разбираем", 0.0, 0.6),
        w("Мед",       0.6, 0.85),
        w("жорни",     0.85, 1.3),
        w(",",         1.3, 1.32),
        w("восемь",    1.4, 1.8),
        w(".",         1.8, 1.82),
    ]
    out = merge_whisper_fragments(fix_brand_names(words))
    texts = [r["word"] for r in out]
    _assert(len(out) == 3, f"6 → 3 (got {len(out)}: {texts})", errors)
    _assert(texts == ["Разбираем", "Midjourney,", "восемь."],
            f"expected brand+punct attached (got {texts})", errors)


def test_pipeline_natural_sentence(errors: list[str]) -> None:
    """CRITICAL regression: a natural sentence with 12 words and zero gaps
    must produce 12 flashes, not one merged blob."""
    print("\n-- pipeline: natural sentence stays split --")
    raw = [
        ("Посмотри",   0.00, 0.40),
        ("какой",      0.40, 0.65),
        ("классный",   0.65, 1.10),
        ("эффект",     1.10, 1.50),
        ("получился",  1.50, 2.00),
        ("у",          2.00, 2.08),
        ("меня",       2.08, 2.35),
        ("вчера",      2.35, 2.75),
        ("вечером",    2.75, 3.20),
        ("в",          3.20, 3.28),
        ("студии",     3.28, 3.75),
        (".",          3.75, 3.77),
    ]
    words = [w(t, s, e) for t, s, e in raw]
    out = merge_whisper_fragments(fix_brand_names(words))
    texts = [r["word"] for r in out]
    _assert(len(out) == 11,
            f"12 tokens (incl dot) → 11 words (got {len(out)}: {texts})", errors)
    _assert(out[-1]["word"] == "студии.",
            f"final dot attached (got {out[-1]['word']!r})", errors)
    # No brand canonical leaked in
    _assert(all("Midjourney" not in t for t in texts),
            "no false brand hits", errors)


def main() -> int:
    print("=" * 60)
    print("subtitle_burner: brand fix-ups + punctuation merging")
    print("=" * 60)

    errors: list[str] = []

    # merge_whisper_fragments
    test_merge_empty(errors)
    test_merge_single(errors)
    test_merge_punctuation_attached(errors)
    test_merge_natural_speech_stays_split(errors)
    test_merge_no_mutation(errors)

    # fix_brand_names
    test_brand_single_token_direct(errors)
    test_brand_two_token_window(errors)
    test_brand_four_token_version(errors)
    test_brand_trailing_punctuation(errors)
    test_brand_case_insensitive(errors)
    test_brand_correct_transliteration(errors)
    test_brand_heygen(errors)
    test_brand_leaves_normal_speech_alone(errors)
    test_brand_longest_window_wins(errors)
    test_brand_no_mutation(errors)

    # pipeline
    test_pipeline_midjourney(errors)
    test_pipeline_natural_sentence(errors)

    print("\n" + "=" * 60)
    if errors:
        print(f"Found {len(errors)} failure(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK all subtitle merge tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
