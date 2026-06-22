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
import os
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
# Per-tenant (срез C): персона выбирается по активному тенанту в _default_persona()
# (panferov → Артём; иначе → Максим, дефолт этого ядра). business_context, если
# передан явно, перебивает дефолт.
_DEFAULT_PERSONA = (
    "Максим — предприниматель из Тюмени, владелец картинг-центра и глэмпинга "
    "«Life Drive». Его контент — про предпринимательский путь, мышление, дисциплину, "
    "образ жизни; ИНОГДА про его бизнесы (картинг, глэмпинг)."
)
_PERSONA_PANFEROV = (
    "Артём Панфёров — основатель AI-студии, эксперт по ИИ для предпринимателей. "
    "Его контент — про практическое применение ИИ в бизнесе и жизни: нейросети, "
    "автоматизация, ИИ-агенты, инструменты, мышление и путь предпринимателя."
)


def _default_persona() -> str:
    """Персона режиссёра активного тенанта (per-tenant, срез C): panferov → Артём,
    иначе → Максим (дефолт ядра). Через tenant.active_tenant_id() (lazy import, как
    style_contract), чтобы ai_video_broll оставался импортируемым standalone."""
    try:
        import tenant
        tid = tenant.active_tenant_id()
    except Exception:
        return _DEFAULT_PERSONA
    return _PERSONA_PANFEROV if tid == "panferov" else _DEFAULT_PERSONA

# Базовый negative prompt для Kling (research 2026-06: 5-8 терминов, худшее —
# первым; 20+ снижает качество). Наш главный провал — читаемый текст на экране
# (Kling рисует его мусором) → ставим первым. Поле fal v3/pro принимает negative_prompt.
HOUSE_NEGATIVE = (
    "text, captions, watermark, logo, deformed hands, "
    "extra fingers, distorted face, blur"
)

# Director prompt v2 (2026-06-21). Контекст автора + сценарий дописываются в plan_clips.
# Усилен после провала клипа 1 (азиат + «иероглифы» на экране): корень был в том,
# что режиссёр сам писал «endless notifications» = текст на экране. Веб-ресёрч Kling
# (офиц. гайд + fal): текст рендерится мусором → НЕ просить экраны-с-контентом;
# лица — задавать явно (нет «китайского дефолта»); негатив — отдельным полем.
_DIRECTOR_PROMPT = """Ты — режиссёр-постановщик и аналитик сценария для коротких вертикальных видео (Kling 3.0 Pro).
Задача: разобрать сценарий и под каждый смысловой бит написать ОДИН насыщенный английский
промпт + короткий negative_prompt — кинематографичную перебивку.

ШАГ 1 — РАЗБОР: пойми, о чём КОНКРЕТНО сценарий, и какие реальные образы его раскрывают.
Предмет бери ИЗ ТЕКСТА. НЕ навязывай бизнес автора, если сценарий о другом.
ВАЖНО — абстрактные биты (внимание, уведомления, деньги, фокус, время) переводи в
БЕСТЕКСТОВЫЕ физические образы: телефон экраном вниз, размытое свечение вне фокуса,
отблеск света на поверхности/лице, пульсация света, монеты, песочные часы — НИКОГДА
не «экран с уведомлениями/сообщениями/контентом» и не вывески.

ШАГ 2 — конкретные объекты/место/действие (не общими словами), чтобы не уходить в generic.
Структура промпта (порядок Kling): СУБЪЕКТ (+2-3 детали) → ДВИЖЕНИЕ СУБЪЕКТА → СЦЕНА →
КАМЕРА (конкретно: slow dolly-in / tracking / push-in — НЕ слово "cinematic") → СВЕТ+АТМОСФЕРА.

ТОН (бренд, только стиль): энергичный предпринимательский кинематограф, премиальный свет,
контраст, движение вперёд. Предмет — из сценария, тон — этот.

ЖЁСТКИЕ ПРАВИЛА:
1. ⛔ ГЛАВНОЕ: НИКАКОГО читаемого текста, надписей, UI, уведомлений, сообщений, цифр,
   часов, вывесок, логотипов, watermark в кадре — Kling рисует текст мусором. Экраны —
   только выключенные / экраном вниз / размытые вне фокуса, без контента.
2. ЛИЦА: избегай чётких узнаваемых лиц ЛЮБЫХ людей. Люди — со спины, в профиль, руки,
   силуэт, не в фокусе. Если человек нужен крупно — задай внешность ЯВНО (2-3 детали:
   возраст + типаж/регион + волосы), привязанную к персоне автора; НЕ выдумывай случайного
   незнакомца и НЕ оставляй внешность на волю модели.
3. РУКИ/АНАТОМИЯ: действия рук — в движении / частично за кадром / у предмета, НЕ статичный
   резкий макро пальцев.
4. Каждый промпт — АНГЛИЙСКИЙ, мульти-шот: начни с "Multiple shots.", 2-3 плана с ракурсом
   в скобках ([wide shot]/[close-up]/[tracking shot]) и движением камеры. Вертикаль 9:16. ~60-90 слов.
5. negative_prompt (англ., 5-8 слов через запятую, худшее первым): начни с
   "text, watermark, logo" и добавь под бит (например "deformed hands, extra fingers,
   distorted face, blur"). Плоские существительные, БЕЗ "no/not".

Напоминание перед выводом: правило №1 — ноль читаемого текста/экранного контента в кадре.
Верни СТРОГО JSON без markdown:
{"clips":[{"beat":"<смысл, рус>","prompt":"Multiple shots. ...","negative_prompt":"text, watermark, logo, ..."}]}"""


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
    valid = []
    for c in clips:
        if not (isinstance(c, dict) and len((c.get("prompt") or "").strip()) >= MIN_PROMPT_LEN):
            continue
        # Контракт v2: каждый клип несёт negative_prompt; если режиссёр не дал —
        # подставляем HOUSE_NEGATIVE (жёсткий запрет текста/артефактов — soft-rule
        # в позитивном промпте режиссёр обходил, см. провал клипа 1).
        neg = (c.get("negative_prompt") or "").strip()
        c["negative_prompt"] = neg if neg else HOUSE_NEGATIVE
        valid.append(c)
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
    persona = (business_context or _default_persona()).strip()
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

# Cost-guard (срез C): жёсткий потолок суммарной длительности сгенерированного
# видеоряда за ОДИН прогон. Kling = реальные деньги fal.ai ($0.112/сек) → длинный
# сценарий не должен разогнать трату. env-override (паттерн paths.py). Дефолт 60с
# (~$6.72/прогон при 10с-клипах). Применяется в fullscreen_plan (видно в оценке)
# и как жёсткий backstop в generate_ai_broll (любой путь входа).
AI_VIDEO_MAX_DURATION_SEC = int(os.getenv("AI_VIDEO_MAX_DURATION_SEC", "60"))


def _max_clips_for_budget(duration: int) -> int:
    """Потолок числа клипов, чтобы n·duration ≤ AI_VIDEO_MAX_DURATION_SEC."""
    return max(1, AI_VIDEO_MAX_DURATION_SEC // max(1, int(duration)))


def fullscreen_plan(script_text: str, clip_len: int = FULLSCREEN_CLIP_LEN) -> dict:
    """План фуллскрин-ролика для экрана подтверждения: число клипов под длину
    озвучки (оценка) + стоимость, с учётом cost-guard (AI_VIDEO_MAX_DURATION_SEC).
    Возвращает {n_clips, est_sec, clip_len, cost}."""
    est = estimate_voiceover_sec(script_text)
    n = min(clips_needed(est, clip_len), _max_clips_for_budget(clip_len))
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

    # Cost-guard backstop (срез C): любой путь входа не сгенерит > AI_VIDEO_MAX_DURATION_SEC
    # секунд видео за прогон — защита от money-leak fal.ai на длинных сценариях.
    budget_cap = _max_clips_for_budget(duration)
    if len(plans) > budget_cap:
        logger.warning(
            f"ai_video cost-guard: {len(plans)}→{budget_cap} клипов "
            f"(потолок {AI_VIDEO_MAX_DURATION_SEC}с/прогон при {duration}с-клипах)")
        _notify(progress_cb,
                f"⚠️ Лимит трат: беру {budget_cap} клипов (потолок {AI_VIDEO_MAX_DURATION_SEC}с/ролик)")
        plans = plans[:budget_cap]

    _notify(progress_cb, f"🎥 Генерю {len(plans)} кинематографичных клипа (Kling 3.0 Pro, ~{duration}с)…")
    paths: list[Path] = []
    for i, clip in enumerate(plans, start=1):
        dest = clips_dir / f"ai_{i:02d}.mp4"
        res = fal_media.generate_kling_video(
            clip["prompt"], dest, duration=duration,
            negative_prompt=clip.get("negative_prompt"))
        if res:
            paths.append(Path(res))
        else:
            logger.warning(f"ai_video: clip {i}/{len(plans)} failed, skipping")

    if not paths:
        raise AiVideoError("all Kling clips failed")

    cost = len(paths) * duration * KLING_PRICE_PER_SEC_USD
    logger.info(f"ai_video: {len(paths)}/{len(plans)} clips ready, est cost ${cost:.2f}")
    return paths, cost
