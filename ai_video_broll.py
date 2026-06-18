"""AI-video B-roll engine — script -> cinematic Seedance clips (Phase 1).

Third visual source alongside Remotion (auto_broll) and HyperFrames: instead of
graphics, it generates filmed-looking cinematic clips via ByteDance Seedance,
for the case "no footage on hand, need cinematic video, not graphics".

Contract mirrors generate_hyperframes_broll / generate_auto_broll so the bot can
pick the engine:
    generate_ai_broll(script_text, out_dir, claude=None, duration=5,
                      progress_cb=None) -> (list[Path], cost_usd)

plan_clips is the LLM "director" (script -> 2-4 prompts); the Seedance call
lives in fal_media.generate_seedance_video; generate_ai_broll wires them together.

Invariants baked into the director prompt (approved 2026-06-17):
  - house tone: energetic-entrepreneurial cinematic
  - LLM auto-chooses the shots from the script (like HyperFrames storyboard)
  - NO recognizable people / close-up faces, NO on-screen text
  - prompts in English (Seedance understands it best), multi-shot form
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import fal_media

logger = logging.getLogger(__name__)


class AiVideoError(Exception):
    """Raised when the engine cannot produce a usable result."""


# --- Director config ------------------------------------------------------

MIN_CLIPS = 2
MAX_CLIPS = 4
MIN_PROMPT_LEN = 15                    # drop junk prompts ("x"/"не знаю") before a paid Seedance call
_DIRECTOR_MODEL = "claude-opus-4-8"   # creative planning; subscription is flat-fee
_DIRECTOR_ATTEMPTS = 2                 # one retry on malformed/insufficient output
_DIRECTOR_MAX_TOKENS = 2000

# Approved director prompt. Script is appended after the trailing "Сценарий:".
_DIRECTOR_PROMPT = """Ты — режиссёр-постановщик коротких вертикальных видео для предпринимателя.
На входе — текст закадрового сценария (озвучка ~30с). Разбей его на 2-4
визуальных бита и на каждый напиши ОДИН промпт для AI-видеогенератора
Seedance — кинематографичную перебивку под этот фрагмент.

Сам реши, сколько битов (2-4) и какие образы сильнее всего раскрывают
смысл, — как арт-директор ищи лучшее визуальное решение, не иллюстрируй
буквально.

ФИРМЕННЫЙ СТИЛЬ (всегда): энергично-предпринимательский кинематограф —
динамичная камера (проезды, разгон, ручная съёмка), премиальный свет,
контраст, ритм, ощущение драйва и движения вперёд; лайфстайл/бизнес-
фактура (город, скорость, рабочие моменты, фактуры успеха).

ЖЁСТКИЕ ПРАВИЛА:
- НИКАКИХ узнаваемых людей и лиц крупным планом. Люди — только силуэты /
  со спины / части тела в движении / издалека.
- НИКАКОГО текста, надписей, логотипов в кадре.
- Каждый промпт — на АНГЛИЙСКОМ (Seedance лучше понимает английский).
- Форма мульти-шот: начни с "Multiple shots.", затем 2-3 плана подряд
  с ракурсом в скобках ([wide shot], [close-up], [tracking shot]) и
  движением камеры словами. Вертикаль 9:16. ~40-80 слов.

Верни СТРОГО JSON без markdown:
{"clips":[{"beat":"<смысл фрагмента, рус>","prompt":"Multiple shots. ..."}]}

Сценарий:"""


def _strip_fences(text: str) -> str:
    """Drop a leading ```lang fence and trailing ``` if the LLM wrapped JSON."""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z0-9]*\n?", "", t)
    t = re.sub(r"\n?```$", "", t)
    return t.strip()


def _parse_clips(raw: str, max_clips: int, min_clips: int) -> list[dict]:
    """Parse the director JSON into a validated clip list (raises on bad).

    Drops clips whose prompt is missing/blank/too short (junk that would burn a
    paid Seedance call); requires >= min_clips survivors; caps to max_clips.
    """
    data = json.loads(_strip_fences(raw))
    clips = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(clips, list):
        raise ValueError("no 'clips' list in director output")
    valid = [
        c for c in clips
        if isinstance(c, dict) and len((c.get("prompt") or "").strip()) >= MIN_PROMPT_LEN
    ]
    if len(valid) < min_clips:
        raise ValueError(f"only {len(valid)} valid clips (need >= {min_clips})")
    return valid[:max_clips]


def _acquire_director_lock():
    """Cross-process flock around the Claude call — the Max OAuth token is shared
    with auto_broll/HyperFrames/other processes. No-op (None) on non-POSIX."""
    try:
        from claude_gen_lock import acquire_gen_flock, ClaudeGenBusy
    except Exception:
        return None
    try:
        return acquire_gen_flock("ai_video")
    except ClaudeGenBusy as e:
        raise AiVideoError(f"Claude занят другой генерацией, попробуй позже: {e}")


def _release_director_lock(handle) -> None:
    if handle is None:
        return
    try:
        from claude_gen_lock import release_gen_flock
        release_gen_flock(handle)
    except Exception:
        pass


def plan_clips(script_text: str, claude, max_clips: int = MAX_CLIPS) -> list[dict]:
    """Turn a voiceover script into up to `max_clips` Seedance prompts via the LLM director.

    `claude` is injected (SubscriptionClient / anthropic.Anthropic). Returns a list
    of {"beat", "prompt"} dicts. Retries once on malformed/insufficient output OR a
    transient create() error, adding repair-feedback on the retry; raises AiVideoError
    if it still fails. The Claude call is serialised via the shared gen-flock.
    """
    base = f"{_DIRECTOR_PROMPT}\n{script_text}"
    min_clips = min(MIN_CLIPS, max_clips)
    last_err: Exception | None = None
    flock = _acquire_director_lock()
    try:
        for attempt in range(_DIRECTOR_ATTEMPTS):
            prompt = base if attempt == 0 else (
                f"{base}\n\nПредыдущий ответ был НЕВАЛИДЕН ({last_err}). "
                'Верни ТОЛЬКО валидный JSON {"clips":[...]} без markdown и пояснений.'
            )
            try:
                resp = claude.messages.create(
                    model=_DIRECTOR_MODEL,
                    max_tokens=_DIRECTOR_MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = getattr(resp.content[0], "text", "") if resp.content else ""
                return _parse_clips(raw, max_clips, min_clips)
            except Exception as e:   # parse error OR transient API error — both retryable
                last_err = e
                logger.warning(
                    f"ai_video director: attempt {attempt + 1}/{_DIRECTOR_ATTEMPTS} failed: {e}"
                )
    finally:
        _release_director_lock(flock)
    raise AiVideoError(f"director failed to produce a valid plan: {last_err}")


# --- Engine ---------------------------------------------------------------

# Verified fal.ai price for Seedance Pro Fast (1080p, 2026-06-17). fal does not
# return per-call cost, so the engine estimates from this published rate; the
# future себестоимость layer reuses the same number.
SEEDANCE_PRICE_PER_5S_USD = 0.245

CLIPS_SUBDIR = "aivideo"   # own namespace, parallel to Remotion (autobroll/) & HF (hyperframes/)


def estimate_cost_range(duration: int) -> "tuple[float, float]":
    """Estimated $ range for one reel (MIN_CLIPS..MAX_CLIPS clips) at `duration` sec.

    Shown in the UI before a paid run so the user isn't surprised by the bill.
    Estimate only — fal returns no per-call cost (see SEEDANCE_PRICE_PER_5S_USD).
    """
    per_clip = (duration / 5.0) * SEEDANCE_PRICE_PER_5S_USD
    return MIN_CLIPS * per_clip, MAX_CLIPS * per_clip


def _notify(progress_cb, msg: str) -> None:
    """Fire a progress message; a broken callback must never break generation."""
    if progress_cb is None:
        return
    try:
        progress_cb(msg)
    except Exception as e:
        logger.warning(f"ai_video: progress_cb failed (ignored): {e}")


def _default_claude():
    """Build the LLM client the way the rest of the bot does (subscription if an
    OAuth token is present, else metered anthropic). Lets callers invoke the
    engine without threading a client through — like auto_broll / HyperFrames."""
    import os
    token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if token:
        from claude_subscription import SubscriptionClient
        return SubscriptionClient(oauth_token=token)
    import anthropic
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def generate_ai_broll(script_text, out_dir, claude=None, duration=5, progress_cb=None,
                      max_clips=MAX_CLIPS):
    """Script -> cinematic Seedance clips. Returns (list[Path], cost_usd).

    Same contract as generate_hyperframes_broll / generate_auto_broll. Clips land
    in out_dir/aivideo/ai_NN.mp4. `duration` is the user-chosen 5 or 10. `max_clips`
    caps the director so we never PLAN/PAY for more clips than the caller can use.
    `claude` is optional — self-constructed when omitted, so callers mirror auto_broll.
    Tolerates per-clip failures (returns the successful ones); raises AiVideoError
    if Seedance is unconfigured or nothing was produced.
    """
    ok, reason = fal_media.seedance_ready()      # preflight BEFORE the paid Claude director
    if not ok:
        raise AiVideoError(f"Seedance недоступен: {reason}")
    if claude is None:
        claude = _default_claude()
    clips_dir = Path(out_dir) / CLIPS_SUBDIR

    _notify(progress_cb, "🎬 Режиссёр придумывает раскадровку…")
    plans = plan_clips(script_text, claude, max_clips=max_clips)

    _notify(progress_cb, f"🎥 Генерю {len(plans)} кинематографичных клипа (Seedance, ~{duration}с)…")
    paths: list[Path] = []
    for i, clip in enumerate(plans, start=1):
        dest = clips_dir / f"ai_{i:02d}.mp4"
        res = fal_media.generate_seedance_video(clip["prompt"], dest, duration=duration)
        if res:
            paths.append(Path(res))
        else:
            logger.warning(f"ai_video: clip {i}/{len(plans)} failed, skipping")

    if not paths:
        raise AiVideoError("all Seedance clips failed")

    cost = len(paths) * (duration / 5.0) * SEEDANCE_PRICE_PER_5S_USD
    logger.info(f"ai_video: {len(paths)}/{len(plans)} clips ready, est cost ${cost:.2f}")
    return paths, cost
