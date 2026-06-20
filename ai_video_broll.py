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

Engine: Kling 3.0 Pro (fal `kling-video/v3/pro/text-to-video`, ~1080p, $0.112/sec).
Replaced Seedance v1 Pro Fast on 2026-06-20 — Seedance rendered generic road cars
for a karting script; Kling has far higher motorsport/action fidelity (verified by
render test) and supports negative prompts / per-shot timing / character persistence.

Director invariants (prompt is a tweakable string constant, contract pinned by tests):
  - house tone = brand STYLE only (energetic-entrepreneurial cinematic, premium
    light, dynamic camera). The SUBJECT always comes from the script, never forced.
  - business_context (who the author is) grounds the subject — but the author's
    business is NOT imposed when the script is about something else (lifestyle,
    mindset, money, city, relationships). Author content is mostly lifestyle/path.
  - people allowed in action (from behind / in motion / gear), avoid large
    recognizable celebrity-style portrait faces; NO on-screen text (we overlay subtitles)
  - prompts in English (the model understands it best), multi-shot form
"""
from __future__ import annotations

import json
import logging
import math
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

# Контекст автора по умолчанию (грунтит предмет, НЕ навязывает тему бизнеса).
# В реале должен приходить из tenant-конфига; пока дефолт под Максима (этот бот).
# TODO(tenant): прокинуть business_context из tenant.py через handlers.
_DEFAULT_PERSONA = (
    "Максим — предприниматель из Тюмени, владелец картинг-центра и глэмпинга "
    "«Life Drive». Его контент — про предпринимательский путь, мышление, дисциплину, "
    "образ жизни; ИНОГДА про его бизнесы (картинг, глэмпинг)."
)

# Director prompt (generalized). Контекст автора + сценарий дописываются в plan_clips.
# Доказано (diag v3, 2026-06-20): на картинг-сценарии даёт картинг, на лайфстайл-
# сценарии — лайфстайл, БЕЗ насильного картинга. Предмет всегда из текста.
_DIRECTOR_PROMPT = """Ты — режиссёр-постановщик и аналитик сценария для коротких вертикальных видео.
Задача: глубоко разобрать сценарий и под каждый смысловой бит написать ОДИН
насыщенный английский промпт для AI-видеогенератора — кинематографичную перебивку.

ШАГ 1 — РАЗБОР: пойми, о чём КОНКРЕТНО этот сценарий и какие реальные визуальные
образы его раскрывают. Предмет бери ИЗ ТЕКСТА сценария (что в нём реально
обсуждается). НЕ навязывай тематику бизнеса автора, если сценарий о другом — у
автора разный контент. Если сценарий буквально про его бизнес — показывай это;
если про мышление/дисциплину/деньги/путь/город/отношения — показывай именно это.

ШАГ 2 — под каждый бит напиши конкретный визуальный промпт ИМЕННО про предмет
этого бита. Описывай КОНКРЕТНЫЕ объекты/место/действие (не общими словами),
чтобы видеогенератор не уходил в generic.

ТОН (бренд, всегда — только стиль, НЕ предмет): энергичный предпринимательский
кинематограф, премиальный свет, контраст, динамичная камера, ощущение движения
вперёд. Предмет — всегда из сценария, тон — этот.

ПРАВИЛА:
- Люди разрешены в действии (со спины, в движении, руки, фигуры, экипировка).
  Избегай крупных узнаваемых лиц-портретов знаменитостей.
- НИКАКОГО текста, надписей, логотипов в кадре (свои субтитры кладём отдельно).
- Каждый промпт — на АНГЛИЙСКОМ.
- Мульти-шот: начни с "Multiple shots.", 2-3 плана с ракурсом в скобках
  ([wide shot], [close-up], [tracking shot]) и движением камеры. Вертикаль 9:16. ~40-80 слов.

Верни СТРОГО JSON без markdown:
{"clips":[{"beat":"<смысл фрагмента, рус>","prompt":"Multiple shots. ..."}]}"""


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


def plan_clips(script_text: str, claude, max_clips: int = MAX_CLIPS,
               target_clips: int | None = None,
               business_context: str | None = None) -> list[dict]:
    """Turn a voiceover script into Seedance prompts via the LLM director.

    Phase 1 (cutaways): `target_clips=None` → "2-4 бита, сам реши" (unchanged).
    Phase 2 (fullscreen): `target_clips=N` → ровно ~N бит, чтобы покрыть всю озвучку.
    `claude` is injected. Retries once on malformed/insufficient/transient output with
    repair-feedback; raises AiVideoError if it still fails. Claude call under gen-flock.
    """
    if target_clips:
        max_clips = target_clips
        min_clips = max(2, target_clips - 1)
        count_note = (
            f"\n\nВАЖНО: это фуллскрин-ролик — сделай РОВНО {target_clips} бит/клипа "
            f"(они покрывают всю озвучку по порядку), НЕ 2-4."
        )
    else:
        min_clips = min(MIN_CLIPS, max_clips)
        count_note = ""
    persona = (business_context or _DEFAULT_PERSONA).strip()
    base = (
        f"{_DIRECTOR_PROMPT}\n\n"
        f"КОНТЕКСТ АВТОРА: {persona}{count_note}\n\n"
        f"Сценарий:\n{script_text}"
    )
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

# Active engine pricing: Kling 3.0 Pro — flat $0.112/sec (audio off), resolution-
# independent (verified on fal 2026-06-20). fal returns no per-call cost → engine
# estimates from this. Fullscreen reel cost ≈ voiceover_sec × this (видеоряд обязан
# покрыть всю озвучку → есть пол по цене). себестоимость-фаза reuses it.
KLING_PRICE_PER_SEC_USD = 0.112

# Legacy Seedance v1 Pro Fast price (kept for reference; engine switched to Kling 2026-06-20).
SEEDANCE_PRICE_PER_5S_USD = 0.11

CLIPS_SUBDIR = "aivideo"   # own namespace, parallel to Remotion (autobroll/) & HF (hyperframes/)


def estimate_cost_range(duration: int) -> "tuple[float, float]":
    """Estimated $ range for one reel (MIN_CLIPS..MAX_CLIPS clips) at `duration` sec.

    Shown in the UI before a paid run so the user isn't surprised by the bill.
    Estimate only — fal returns no per-call cost (see SEEDANCE_PRICE_PER_5S_USD).
    """
    per_clip = duration * KLING_PRICE_PER_SEC_USD
    return MIN_CLIPS * per_clip, MAX_CLIPS * per_clip


# --- Fullscreen (Pipeline 2): clip count from estimated voiceover length ---

WORDS_PER_MIN = 150   # rough speech rate for estimating voiceover length before it's rendered


def estimate_voiceover_sec(script_text: str) -> float:
    """Грубая оценка длины озвучки из числа слов (озвучка генерится ПОЗЖЕ клипов,
    точной длины ещё нет). ~150 слов/мин."""
    words = len((script_text or "").split())
    return words / WORDS_PER_MIN * 60.0


def clips_needed(est_sec: float, clip_len: float, buffer: int = 0) -> int:
    """Сколько клипов сгенерить, чтобы покрыть est_sec при длине clip_len.
    БЕЗ overshoot-буфера (2026-06-20): на Kling каждый лишний клип = реальные деньги
    ($0.112/сек), а ассемблер и так подрежет последний клип под реальную озвучку.
    ±5с не критично (Артём). buffer оставлен параметром на случай явной нужды."""
    n = math.ceil(est_sec / clip_len) + buffer if est_sec > 0 else MIN_CLIPS
    return max(MIN_CLIPS, n)


FULLSCREEN_CLIP_LEN = 10   # дефолт для фуллскрина: 10с раскрывает мульти-шот, вдвое меньше вызовов


def fullscreen_plan(script_text: str, clip_len: int = FULLSCREEN_CLIP_LEN) -> dict:
    """План фуллскрин-ролика для экрана подтверждения: число клипов под длину
    озвучки (оценка) + стоимость. Возвращает {n_clips, est_sec, clip_len, cost}."""
    est = estimate_voiceover_sec(script_text)
    n = clips_needed(est, clip_len)
    cost = n * clip_len * KLING_PRICE_PER_SEC_USD
    return {"n_clips": n, "est_sec": est, "clip_len": clip_len, "cost": cost}


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
                      max_clips=MAX_CLIPS, target_clips=None, business_context=None):
    """Script -> cinematic Kling 3.0 Pro clips. Returns (list[Path], cost_usd).

    Same contract as generate_hyperframes_broll / generate_auto_broll. Clips land
    in out_dir/aivideo/ai_NN.mp4. `duration` is the user-chosen 5 or 10. `max_clips`
    caps the director so we never PLAN/PAY for more clips than the caller can use.
    `claude` is optional — self-constructed when omitted, so callers mirror auto_broll.
    `business_context` grounds the director's subject (defaults to the author persona).
    Engine: Kling 3.0 Pro (2026-06-20, was Seedance v1 Pro Fast — generic, weak).
    Tolerates per-clip failures (returns the successful ones); raises AiVideoError
    if the video backend is unconfigured or nothing was produced.
    """
    ok, reason = fal_media.seedance_ready()      # preflight (same FAL_KEY gates Kling too)
    if not ok:
        raise AiVideoError(f"Видео-движок недоступен: {reason}")
    if claude is None:
        claude = _default_claude()
    clips_dir = Path(out_dir) / CLIPS_SUBDIR

    _notify(progress_cb, "🎬 Режиссёр придумывает раскадровку…")
    plans = plan_clips(script_text, claude, max_clips=max_clips, target_clips=target_clips,
                       business_context=business_context)

    _notify(progress_cb, f"🎥 Генерю {len(plans)} кинематографичных клипа (Kling 3.0 Pro, ~{duration}с)…")
    paths: list[Path] = []
    for i, clip in enumerate(plans, start=1):
        dest = clips_dir / f"ai_{i:02d}.mp4"
        res = fal_media.generate_kling_video(clip["prompt"], dest, duration=duration)
        if res:
            paths.append(Path(res))
        else:
            logger.warning(f"ai_video: clip {i}/{len(plans)} failed, skipping")

    if not paths:
        raise AiVideoError("all Kling clips failed")

    cost = len(paths) * duration * KLING_PRICE_PER_SEC_USD
    logger.info(f"ai_video: {len(paths)}/{len(plans)} clips ready, est cost ${cost:.2f}")
    return paths, cost
