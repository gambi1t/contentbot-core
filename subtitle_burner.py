"""Word-by-word animated subtitles in CapCut style.

Pipeline:
1. Extract audio from video (or use provided audio file)
2. Transcribe with faster-whisper → word-level timestamps
3. Generate ASS subtitle file with pop-in animation
4. Burn subtitles onto video via ffmpeg

Style reference (user's CapCut setup):
- One word at a time, uppercase, bold
- White text, black outline
- Center-bottom of 9:16 frame
- Subtle pop-in (scale 120% → 100%) animation
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("subtitle_burner")

# ── Style defaults (CapCut-inspired) ──────────────────────────────────────────
# ASS uses PlayRes-relative sizes; at PlayResX=1080, PlayResY=1920:
#   fontsize 90 ≈ large single word that fills ~50% of screen width
DEFAULT_FONT = "NT Somic"             # Same font as user's CapCut setup (NT Somic Bold)
DEFAULT_FONTSIZE = 90
DEFAULT_PRIMARY_COLOR = "&H00FFFFFF"  # White (ASS uses &HAABBGGRR)
DEFAULT_OUTLINE_COLOR = "&H00000000"  # Black
DEFAULT_OUTLINE_WIDTH = 5
DEFAULT_SHADOW = 0                   # No shadow, clean look
# 10 июня (фидбэк со встречи с Максимом): при 480 (≈75% высоты) слово ложится
# НА ЛИЦО в близком селфи (кадры 3с/12с IG «5 двигателей»). 300 ≈ 84% высоты —
# ниже подбородка даже в близком кадре, но выше нижнего UI Instagram/Reels.
DEFAULT_MARGIN_V = 300
# Split-лейаут: нижняя половина = крупный кроп головы (подбородок ~86% высоты),
# 300 попадает на губы (проверено кадром «24»). 150 — под подбородком
# (визуальный подбор по кадрам margin 150 vs 200, 10 июня).
SPLIT_MARGIN_V = 150

WHISPER_MODEL = "small"              # Good Russian accuracy vs speed
POP_DURATION_MS = 80                 # Pop-in animation duration


_PUNCT_ONLY = set(".,!?;:-—«»\"'…()[]")


# ── Brand-name canonicalization ───────────────────────────────────────────────
# Whisper mis-hears English AI-tool brand names as Russian phonetic garbage
# ("меджорни" for *Midjourney*, "хейген" for *HeyGen*). Even after merging
# fragments we end up with non-existent Russian words on screen. We normalize
# each merged token and replace it with the canonical English brand if it
# matches a known mis-hearing. Uppercase CapCut style preserves the brand
# (e.g. "Midjourney" → "MIDJOURNEY", which is still a valid recognizable brand).
#
# Keys must be the lowercase alphanumeric form (punctuation/spaces stripped)
# so the same entry catches "Меджорни", "меджорни,", "МЕДЖОРНИ!" etc.
_BRAND_CANONICAL: dict[str, str] = {
    # Midjourney — the original reason this layer exists
    "midjourney":  "Midjourney",
    "меджорни":    "Midjourney",
    "миджорни":    "Midjourney",
    "меджорны":    "Midjourney",
    "миджорны":    "Midjourney",
    "меджерни":    "Midjourney",
    "миджерни":    "Midjourney",
    # HeyGen
    "heygen":      "HeyGen",
    "хейген":      "HeyGen",
    "хэйген":      "HeyGen",
    "хейджен":     "HeyGen",
    "хэйджен":     "HeyGen",
    # Sora
    "sora":        "Sora",
    # Runway
    "runway":      "Runway",
    "ранвей":      "Runway",
    "рэнвей":      "Runway",
    "ранвэй":      "Runway",
    # ChatGPT
    "chatgpt":     "ChatGPT",
    "чатгпт":      "ChatGPT",
    "чатжпт":      "ChatGPT",
    # Claude
    "claude":      "Claude",
    "клод":        "Claude",
    # Gemini
    "gemini":      "Gemini",
    "джемини":     "Gemini",
    "гемини":      "Gemini",
    # Higgsfield
    "higgsfield":  "Higgsfield",
    "хиксфилд":    "Higgsfield",
    "хигсфилд":    "Higgsfield",
    "хигсфильд":   "Higgsfield",
    # Kling
    "kling":       "Kling",
    "клинг":       "Kling",
    # Suno
    "suno":        "Suno",
    "суно":        "Suno",
    # Version tokens that whisper splits into V + 8 + . + 1
    "v81":         "V8.1",
    "v8":          "V8",
    "v7":          "V7",

    # ── Бизнес-лексикон Максима (10 июня) ────────────────────────────
    # Прод-баг: субтитр «МОРЖА» вместо «маржа» в ролике «Себестоимость»
    # (Whisper; в карточном монтаже нет review-шага). Идентичные пары
    # (себестоимость→себестоимость) нужны, чтобы сработала мульти-токенная
    # склейка «себе» + «стоимость».
    "моржа":            "маржа",
    "моржу":            "маржу",
    "морже":            "марже",
    "моржой":           "маржой",
    "моржинальность":   "маржинальность",
    "маржа":            "маржа",
    "себестоимость":    "себестоимость",
    "кэшфлоу":          "кэшфлоу",
    "кешфлоу":          "кэшфлоу",
    "кэшфло":           "кэшфлоу",
    "кешфло":           "кэшфлоу",
    "глэмпинг":         "глэмпинг",
    "глемпинг":         "глэмпинг",
    "глампинг":         "глэмпинг",
    "лайфдрайв":        "Life Drive",
    "lifedrive":        "Life Drive",
}

# Maximum number of adjacent tokens to try combining when looking for a brand.
# Covers cases like "V" + "8" + "." + "1" → "V8.1" (4 tokens).
_BRAND_MAX_WINDOW = 4


def _normalize_for_brand_lookup(text: str) -> str:
    """Lowercase alphanumeric form for brand dictionary lookup.

    Strips punctuation and spaces so "Меджорни," and "МЕДЖОРНИ" both
    collapse to the same key.
    """
    return "".join(c.lower() for c in text if c.isalnum())


def fix_brand_names(words: list[dict]) -> list[dict]:
    """Replace mis-heard English brand names with canonical spelling.

    Uses a **greedy windowed matcher**: at each position, tries to combine
    1..N adjacent tokens (N = :data:`_BRAND_MAX_WINDOW`) by normalizing and
    concatenating them, and checks whether the result matches a known brand
    in :data:`_BRAND_CANONICAL`. Longest match wins, which handles both:

    - single-token mis-hearing: ``["Меджорни"]`` → ``["Midjourney"]``
    - multi-token splits: ``["Мед", "жорни"]`` → ``["Midjourney"]``
    - version tokens with punctuation: ``["V", "8", ".", "1"]`` → ``["V8.1"]``

    Matched windows collapse into a single new token with the canonical
    spelling and the outer start/end timestamps. Non-matched tokens pass
    through unchanged. Any trailing punctuation on a single matched token is
    peeled off, the brand substituted, and the punctuation re-appended
    (so ``"Меджорни,"`` becomes ``"Midjourney,"``).

    Critically, this function is **much safer than timestamp-gap merging**:
    it only merges tokens when their combined normalized form is literally
    in the brand dictionary, so it cannot accidentally glue together
    unrelated natural-speech words.

    Returns a **new** list; input is not mutated.
    """
    if not words:
        return []

    out: list[dict] = []
    i = 0
    n = len(words)
    while i < n:
        matched = False
        # Try longest window first so "V" + "8" + "." + "1" wins over "v8"
        max_win = min(_BRAND_MAX_WINDOW, n - i)
        for win in range(max_win, 1, -1):
            window = words[i:i + win]
            # Reject windows that END on pure punctuation — that comma or
            # dot is really trailing punctuation for the brand, not part of
            # it. Leave it for the punct-merge pass to attach to the
            # resolved brand token. (V8.1 has "." in the middle, not last,
            # so it is still allowed.)
            last_text = window[-1]["word"].strip()
            if last_text and all(c in _PUNCT_ONLY for c in last_text):
                continue
            combined = "".join(
                _normalize_for_brand_lookup(w["word"]) for w in window
            )
            if combined and combined in _BRAND_CANONICAL:
                out.append({
                    "word":  _BRAND_CANONICAL[combined],
                    "start": window[0]["start"],
                    "end":   window[-1]["end"],
                })
                i += win
                matched = True
                break
        if matched:
            continue

        # Single-token path — peel trailing punctuation, look up, replace
        w = words[i]
        raw = w["word"]
        tail = ""
        stripped = raw.rstrip()
        while stripped and stripped[-1] in _PUNCT_ONLY:
            tail = stripped[-1] + tail
            stripped = stripped[:-1]
        key = _normalize_for_brand_lookup(stripped)
        new_w = dict(w)
        if key and key in _BRAND_CANONICAL:
            new_w["word"] = _BRAND_CANONICAL[key] + tail
        out.append(new_w)
        i += 1

    return out


def merge_whisper_fragments(words: list[dict]) -> list[dict]:
    """Attach standalone punctuation tokens to the previous word.

    Whisper sometimes emits punctuation (``,`` ``.`` ``!``) as its own token
    with its own timestamp. For CapCut-style one-word-per-flash subtitles
    that reads as a separate flashing dot on screen. This pass glues pure
    punctuation tokens onto the previous visible word.

    **History**: earlier this function also tried to merge whisper fragment
    splits of long brand names by looking at timestamp gaps (``gap < 0.05``
    → merge). That rule turned out to be catastrophic — in real whisper
    output, *every* natural word pair has a near-zero gap, so the merger
    glued the entire sentence into a single flash. Brand fragment handling
    now lives in :func:`fix_brand_names` (windowed dictionary match), and
    this function is limited to the punctuation case.

    Runs in O(n). Returns a **new** list; input is not mutated.
    """
    if not words:
        return []

    merged: list[dict] = [dict(words[0])]
    for cur in words[1:]:
        cur_text = cur["word"].strip()
        if not cur_text:
            continue
        # Pure-punctuation token → always attach to previous word. The gap
        # is not checked because whisper consistently emits punctuation as
        # a tight follow-on even when the word-level timestamps suggest a
        # small separation.
        if all(c in _PUNCT_ONLY for c in cur_text):
            prev = merged[-1]
            prev["word"] = prev["word"].rstrip() + cur_text
            prev["end"] = cur["end"]
        else:
            merged.append(dict(cur))

    return merged


def transcribe_words(
    audio_path: str | Path,
    language: str = "ru",
    model_size: str = WHISPER_MODEL,
    initial_prompt: str | None = None,
) -> list[dict]:
    """Transcribe audio → word-level timestamps.

    Returns: [{"word": "слово", "start": 0.5, "end": 0.8}, ...]

    Brand-name fragments that whisper incorrectly split (transliterated
    names like "Midjourney", versions like "V8.1") are collapsed and
    normalized via :func:`fix_brand_names` (windowed dictionary match).
    Then :func:`merge_whisper_fragments` attaches standalone punctuation
    tokens to the preceding word.

    Args:
        initial_prompt: optional context string passed to faster-whisper as
            transcription prompt. Used for vocabulary biasing — pass a
            comma-separated list of expected brand names to improve recognition
            of mis-heard English terms. Default None = no biasing.
    """
    from faster_whisper import WhisperModel

    logger.info(f"Loading Whisper model '{model_size}' …")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=initial_prompt,
    )

    words = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            text = w.word.strip()
            if text:
                words.append({"word": text, "start": w.start, "end": w.end})

    raw_count = len(words)
    # Order matters: brand windowed match runs on RAW whisper output because
    # it needs to see the split fragments (e.g. "V"+"8"+"."+"1") before
    # punctuation merging collapses them. Then punctuation merge handles any
    # trailing commas/dots on the now-collapsed words.
    words = fix_brand_names(words)
    after_brand = len(words)
    words = merge_whisper_fragments(words)
    after_merge = len(words)
    brand_hits = sum(
        1 for w in words
        if any(
            w["word"].rstrip("".join(_PUNCT_ONLY)) == v
            for v in _BRAND_CANONICAL.values()
        )
    )
    logger.info(
        f"Transcribed {raw_count} raw → {after_brand} after brand fix "
        f"→ {after_merge} after punct merge ({brand_hits} brand hits) "
        f"from {Path(audio_path).name} "
        f"(lang={info.language}, prob={info.language_probability:.2f})"
    )
    return words


# ── ASS generation ────────────────────────────────────────────────────────────

def _ass_ts(seconds: float) -> str:
    """Seconds → ASS timestamp ``H:MM:SS.cc``."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _margin_for_word(word_start: float, montage_plan: list[dict] | None,
                     margin_split: int = SPLIT_MARGIN_V,
                     margin_default: int = DEFAULT_MARGIN_V) -> int:
    """Pick MarginV based on which montage segment the word falls into.

    10 июня («субтитры пониже везде»): стык 900 для split отменён — после
    подъёма аватара (crop 260) стык = лоб, а 300 в split = губы (half-кроп
    головы крупнее). split → SPLIT_MARGIN_V (под подбородком),
    остальные лейауты → DEFAULT_MARGIN_V.
    """
    if not montage_plan:
        return 0  # 0 = use style default
    for seg in montage_plan:
        if seg["start"] <= word_start < seg["end"]:
            if seg["layout"] == "split":
                return margin_split
            else:
                return margin_default
    return margin_default


def generate_ass(
    words: list[dict],
    output_path: str | Path,
    *,
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FONTSIZE,
    primary_color: str = DEFAULT_PRIMARY_COLOR,
    outline_color: str = DEFAULT_OUTLINE_COLOR,
    outline_width: int = DEFAULT_OUTLINE_WIDTH,
    shadow: int = DEFAULT_SHADOW,
    margin_v: int = DEFAULT_MARGIN_V,
    uppercase: bool = True,
    pop_animation: bool = True,
    montage_plan: list[dict] | None = None,
) -> Path:
    """Generate ASS file: one word at a time with optional pop-in.

    If *montage_plan* is provided, MarginV adapts per word:
    split segments → margin at junction, other layouts → lower.
    """
    output_path = Path(output_path)

    header = (
        "[Script Info]\n"
        "Title: Auto Subtitles\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Word,{font},{fontsize},{primary_color},&H000000FF,"
        f"{outline_color},&H80000000,-1,0,0,0,100,100,2,0,1,"
        f"{outline_width},{shadow},2,40,40,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    lines = [header]
    pop = POP_DURATION_MS

    for w in words:
        text = w["word"].upper() if uppercase else w["word"]
        start = _ass_ts(w["start"])
        end = _ass_ts(w["end"])

        if pop_animation:
            text_fx = (
                f"{{\\fscx120\\fscy120"
                f"\\t(0,{pop},\\fscx100\\fscy100)}}{text}"
            )
        else:
            text_fx = text

        # Per-word MarginV: 0 = use style default, >0 = override
        word_mv = _margin_for_word(w["start"], montage_plan)
        lines.append(f"Dialogue: 0,{start},{end},Word,,0,0,{word_mv},,{text_fx}")

    output_path.write_text("\n".join(lines), encoding="utf-8-sig")
    logger.info(f"ASS subtitles: {output_path.name} ({len(words)} words, adaptive={'yes' if montage_plan else 'no'})")
    return output_path


# ── Burn onto video ───────────────────────────────────────────────────────────

# veryfast/crf20: визуально прозрачно (SSIM 0.989, замер 18 июня) и укладывается
# в timeout на минутном 1080p/60-ролике. НЕ менять на medium/crf15 — это давало
# ~12× realtime (20-сек ролик = 259 сек) и минутный ролик падал по timeout.
# Замок: tests/test_subtitle_burn_params.py. (Порт M1 из legacy content-bot.)
BURN_PRESET = "veryfast"
BURN_CRF = "20"
BURN_TIMEOUT = 900


def build_burn_cmd(video_path, vf: str, output_path) -> list[str]:
    """Construct the ffmpeg subtitle-burn command (pure, unit-tested).

    *vf* is the already-assembled video filter string (e.g. ``ass='subs.ass'``).
    Uses :data:`BURN_PRESET`/:data:`BURN_CRF` — see the module note and
    tests/test_subtitle_burn_params.py for why this must stay veryfast/crf20.
    """
    return [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", BURN_PRESET, "-crf", BURN_CRF,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]


def burn_subtitles(
    video_path: str | Path,
    ass_path: str | Path,
    output_path: str | Path | None = None,
    font_dir: str | Path | None = None,
) -> Path:
    """Burn ASS subtitles onto video via ffmpeg."""
    video_path = Path(video_path)
    ass_path = Path(ass_path)

    if output_path is None:
        output_path = video_path.parent / f"{video_path.stem}_subs{video_path.suffix}"
    output_path = Path(output_path)

    # ffmpeg filter: escape path for ASS filter (: and \ must be escaped)
    ass_esc = str(ass_path).replace("\\", "/").replace(":", "\\:")

    if font_dir:
        font_esc = str(font_dir).replace("\\", "/").replace(":", "\\:")
        vf = f"ass='{ass_esc}':fontsdir='{font_esc}'"
    else:
        vf = f"ass='{ass_esc}'"

    cmd = build_burn_cmd(video_path, vf, output_path)

    logger.info(f"Burning subtitles: {video_path.name} → {output_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=BURN_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg subtitle burn failed:\n{result.stderr[-600:]}"
        )

    mb = output_path.stat().st_size / 1024 / 1024
    # Verify output duration matches input
    try:
        in_dur = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        out_dur = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(output_path)],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        logger.info(f"Subtitles burned: {output_path.name} ({mb:.1f} MB), in={in_dur}s, out={out_dur}s")
        if float(out_dur) < float(in_dur) * 0.9:
            logger.error(f"[subs] WARNING: output duration ({out_dur}s) much shorter than input ({in_dur}s)!")
    except Exception:
        logger.info(f"Subtitles burned: {output_path.name} ({mb:.1f} MB)")
    return output_path


# ── High-level one-call API ───────────────────────────────────────────────────

def add_subtitles_to_video(
    video_path: str | Path,
    audio_path: str | Path | None = None,
    output_path: str | Path | None = None,
    language: str = "ru",
    font: str = DEFAULT_FONT,
    fontsize: int = DEFAULT_FONTSIZE,
    font_dir: str | Path | None = None,
    uppercase: bool = True,
    margin_v: int = DEFAULT_MARGIN_V,
    montage_plan: list[dict] | None = None,
    words: list[dict] | None = None,
) -> Path:
    """Full pipeline: (transcribe →) ASS → burn.

    *audio_path* — if ``None``, extracts audio from the video.
    *montage_plan* — if provided, subtitle position adapts per layout segment.
    *words* — готовые word-тайминги (например уже отредактированный транскрипт
    селфи). Если переданы — Whisper НЕ запускается (сохраняем правки + время).
    Returns path to the new video with subtitles.
    """
    video_path = Path(video_path)
    cleanup_audio = False

    # Готовые words (отредактированный транскрипт) → пропускаем извлечение
    # аудио и транскрибацию целиком.
    if words is not None:
        audio_path = audio_path or video_path  # не используется, но для единообразия

    # ── extract audio if needed (только если надо транскрибировать) ──
    if words is None and audio_path is None:
        audio_path = video_path.parent / f"_tmp_sub_audio.wav"
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(audio_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"Audio extraction failed: {r.stderr[-300:]}")
        cleanup_audio = True

    try:
        # 1. Transcribe — только если words не переданы
        if words is None:
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(audio_path)],
                    capture_output=True, text=True, timeout=10,
                )
                logger.info(f"[subs] Audio for transcription: {audio_path.name}, duration={probe.stdout.strip()}s")
            except Exception:
                pass
            words = transcribe_words(audio_path, language=language)
        else:
            logger.info(f"[subs] Using {len(words)} provided words (no re-transcription)")
        if not words:
            logger.warning("No words — returning video as-is")
            return video_path

        # 2. Generate ASS
        ass_path = video_path.parent / f"_tmp_subs.ass"
        generate_ass(
            words, ass_path,
            font=font, fontsize=fontsize,
            uppercase=uppercase,
            margin_v=margin_v,
            montage_plan=montage_plan,
        )

        # 3. Burn
        result = burn_subtitles(
            video_path, ass_path,
            output_path=output_path,
            font_dir=font_dir,
        )

        # cleanup
        try:
            ass_path.unlink()
        except OSError:
            pass

        return result

    finally:
        if cleanup_audio:
            try:
                Path(audio_path).unlink()
            except OSError:
                pass
