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
    """Raised when the engine cannot produce a usable result.

    category: "content" — отклонено модерацией fal (повтор бесполезен, надо
    править сценарий) · "technical" — сбой инфраструктуры fal (повтор может
    помочь) · None — категория неизвестна.
    """

    def __init__(self, message, category=None):
        super().__init__(message)
        self.category = category


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


def plan_clip_durations(target_sec: float, min_clips: int | None = None,
                        max_total: int | None = None) -> "list[int]":
    """Audio-first (Fix #5): набор длин клипов (5/10с) под РЕАЛЬНУЮ длину озвучки
    с минимальным остатком. 14.6с → [10, 5] (=15, остаток 0.4), НЕ [10, 10] (=20).
    Kling поддерживает только 5 и 10с. Заполняем 10с-клипами (раскрывают мульти-
    шот), маленький хвост ≤5с → один 5с. Уважает MIN_CLIPS (визуальное разнообразие)
    и cost-guard по сумме секунд (AI_VIDEO_MAX_DURATION_SEC)."""
    if min_clips is None:
        min_clips = MIN_CLIPS
    if max_total is None:
        max_total = AI_VIDEO_MAX_DURATION_SEC
    target = max(0.0, float(target_sec))
    durs: list[int] = []
    covered = 0.0
    while covered < target:
        if (target - covered) > 5:
            durs.append(10); covered += 10
        else:
            durs.append(5); covered += 5
    # Минимум клипов (разнообразие): дробим 10→5+5, иначе добавляем 5с.
    while len(durs) < min_clips:
        if 10 in durs:
            durs.remove(10); durs.extend([5, 5])
        else:
            durs.append(5)
    # Cost-guard: суммарные секунды ≤ max_total (длинный сценарий не разгонит трату).
    capped: list[int] = []
    total = 0
    for d in durs:
        if total + d > max_total:
            logger.warning(
                f"ai_video audio-first: обрезаю план по потолку {max_total}с/прогон")
            break
        capped.append(d); total += d
    if not capped:
        capped = [5] * max(1, min_clips)
    return capped


def fullscreen_plan_from_duration(actual_sec: float) -> dict:
    """Audio-first план (Fix #5): длины клипов под РЕАЛЬНУЮ длину озвучки (ffprobe),
    а не из числа слов. Возвращает {n_clips, durations, total_sec, est_sec, cost}."""
    durations = plan_clip_durations(actual_sec)
    total = sum(durations)
    return {
        "n_clips": len(durations), "durations": durations, "total_sec": total,
        "est_sec": float(actual_sec), "cost": total * KLING_PRICE_PER_SEC_USD,
    }


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


def _normalize_plan_count(plans, required_n: int) -> list:
    """C2 (CTO-ревью): привести число планов к required_n ДО оплаты.

    >N → обрезаем (не платим за лишние клипы). <N → дополняем continuation-
    промптом (повтор последнего бита — визуальное продолжение), БЕЗ нового вызова
    режиссёра и БЕЗ лишних платных секунд (длины уже спланированы). Пустой план
    остаётся пустым → вызывающий обязан поднять ошибку до Kling."""
    plans = list(plans)
    if len(plans) > required_n:
        return plans[:required_n]
    if 0 < len(plans) < required_n:
        base = plans[-1]
        while len(plans) < required_n:
            plans.append({
                "prompt": base.get("prompt", ""),
                "negative_prompt": base.get("negative_prompt"),
                "beat": "continuation",
            })
    return plans


def generate_ai_broll(script_text, out_dir, claude=None, duration=5, progress_cb=None,
                      max_clips=MAX_CLIPS, target_clips=None, business_context=None,
                      clip_durations=None):
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
    ok, reason = fal_media.kling_ready()         # preflight (FAL_KEY + fal_client)
    if not ok:
        raise AiVideoError(f"Видео-движок недоступен: {reason}")
    if claude is None:
        claude = _default_claude()
    # Audio-first (Fix #5): per-clip длины (микс 5/10) задают РОВНО столько бит.
    if clip_durations:
        target_clips = len(clip_durations)
    clips_dir = Path(out_dir) / CLIPS_SUBDIR

    _notify(progress_cb, "🎬 Режиссёр придумывает раскадровку…")
    plans = plan_clips(script_text, claude, max_clips=max_clips, target_clips=target_clips,
                       business_context=business_context)

    # Cost-guard + per-clip длины. Любой путь входа не сгенерит > AI_VIDEO_MAX_DURATION_SEC
    # секунд видео за прогон — защита от money-leak fal.ai на длинных сценариях.
    if clip_durations:
        # C2 (CTO-ревью): план ОБЯЗАН иметь ровно столько промптов, сколько длин,
        # ДО первого платного Kling-вызова. Иначе zip ниже молча недокроет/обрежет
        # → платный недобор на сборке. >N → trim; <N → pad continuation-промптом
        # (без нового вызова режиссёра, без лишних платных секунд — длины уже есть).
        plans = _normalize_plan_count(plans, len(clip_durations))
        if len(plans) != len(clip_durations):
            raise AiVideoError(
                "не удалось собрать полный план клипов под озвучку — пересоберите",
                category="technical")
        # Audio-first: сопоставляем планы и длины, режем по СУММЕ секунд.
        kept: list = []
        total = 0
        for p, d in zip(plans, clip_durations):
            if total + d > AI_VIDEO_MAX_DURATION_SEC:
                logger.warning(
                    f"ai_video cost-guard: обрезаю по потолку {AI_VIDEO_MAX_DURATION_SEC}с/прогон")
                break
            kept.append((p, int(d))); total += int(d)
        plans = [p for p, _ in kept]
        per_clip = [d for _, d in kept]
    else:
        budget_cap = _max_clips_for_budget(duration)
        if len(plans) > budget_cap:
            logger.warning(
                f"ai_video cost-guard: {len(plans)}→{budget_cap} клипов "
                f"(потолок {AI_VIDEO_MAX_DURATION_SEC}с/прогон при {duration}с-клипах)")
            _notify(progress_cb,
                    f"⚠️ Лимит трат: беру {budget_cap} клипов (потолок {AI_VIDEO_MAX_DURATION_SEC}с/ролик)")
            plans = plans[:budget_cap]
        per_clip = [int(duration)] * len(plans)

    # Сохранить план рядом с клипами (с per-clip длинами) — чтобы ДОБРАТЬ упавший
    # клип той же длины и промптом без повторного вызова режиссёра (regen_ai_clips).
    _write_plan(clips_dir, plans, duration, clip_durations=per_clip)

    _notify(progress_cb,
            f"🎥 Генерю {len(plans)} кинематографичных клипа (Kling 3.0 Pro, ~{sum(per_clip)}с видео)…")
    paths: list[Path] = []
    clip_errors: list[str] = []
    spent_sec = 0
    for i, clip in enumerate(plans, start=1):
        dest = clips_dir / f"ai_{i:02d}.mp4"
        d = per_clip[i - 1] if i - 1 < len(per_clip) else int(duration)
        res = fal_media.generate_kling_video(
            clip["prompt"], dest, duration=d,
            negative_prompt=clip.get("negative_prompt"), errors_out=clip_errors)
        if res:
            paths.append(Path(res)); spent_sec += d
        else:
            logger.warning(f"ai_video: clip {i}/{len(plans)} failed, skipping")

    if not paths:
        # Категория для сообщения юзеру: "content" (модерация fal — повтор
        # бесполезен, надо править сценарий) приоритетнее "technical" (инфра
        # fal — повтор может помочь). Если хоть один клип отклонён по контенту,
        # вся пачка идёт по одному сценарию → классифицируем как content.
        category = "content" if "content" in clip_errors else "technical"
        raise AiVideoError("all Kling clips failed", category=category)

    cost = spent_sec * KLING_PRICE_PER_SEC_USD
    logger.info(f"ai_video: {len(paths)}/{len(plans)} clips ready ({spent_sec}с), est cost ${cost:.2f}")
    return paths, cost


def _write_plan(clips_dir, plans, duration, clip_durations=None) -> None:
    """Сохранить план клипов (plan.json) рядом с ними. Нужно, чтобы добрать
    упавший на скачивании клип по ТОМУ ЖЕ промпту (и той же длине), не вызывая
    режиссёра заново. clip_durations — per-clip длины (audio-first микс 5/10);
    при None у каждого клипа длина = общий duration."""
    try:
        clips_dir = Path(clips_dir)
        clips_dir.mkdir(parents=True, exist_ok=True)
        data = {"duration": int(duration), "clips": [
            {"i": i, "prompt": c.get("prompt", ""),
             "negative_prompt": c.get("negative_prompt"), "beat": c.get("beat", ""),
             "duration": int(clip_durations[i - 1])
             if clip_durations and i - 1 < len(clip_durations) else int(duration)}
            for i, c in enumerate(plans, start=1)]}
        (clips_dir / "plan.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"ai_video: не смог сохранить plan.json: {e}")


def _read_plan(clips_dir):
    """Прочитать сохранённый план клипов (или None)."""
    p = Path(clips_dir) / "plan.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"ai_video: битый plan.json: {e}")
        return None


def regen_ai_clips(out_dir, indices=None, progress_cb=None):
    """Перегенерить отдельные клипы AI-видео по сохранённому плану (plan.json).

    indices=None → все НЕДОСТАЮЩИЕ (нет файла ai_NN.mp4) — «добор» после
    частичного сбоя (напр. клип упал на скачивании). Иначе — заданные номера
    (1-индексация), напр. ручная перегенерация сцены N.

    Промпт берётся из plan.json (без нового вызова режиссёра). Скачивание уже
    с ретраями (generate_kling_video). Возвращает (list[Path] новых, cost_usd).
    """
    ok, reason = fal_media.kling_ready()
    if not ok:
        raise AiVideoError(f"Видео-движок недоступен: {reason}")
    clips_dir = Path(out_dir) / CLIPS_SUBDIR
    plan = _read_plan(clips_dir)
    if not plan or not plan.get("clips"):
        raise AiVideoError("план клипов не найден (plan.json) — нечего добирать")
    duration = int(plan.get("duration", 5))
    by_index = {int(c["i"]): c for c in plan["clips"] if "i" in c}

    if indices is None:
        indices = [i for i in sorted(by_index)
                   if not (clips_dir / f"ai_{i:02d}.mp4").exists()]
    else:
        indices = [int(i) for i in indices if int(i) in by_index]
    if not indices:
        return [], 0.0

    _notify(progress_cb, f"🎥 Добираю {len(indices)} клип(ов) (Kling 3.0 Pro)…")
    new_paths: list[Path] = []
    spent_sec = 0
    for i in indices:
        clip = by_index.get(i)
        if not clip or not clip.get("prompt"):
            continue
        # per-clip длина (audio-first); fallback на общий duration плана.
        d = int(clip.get("duration", duration))
        dest = clips_dir / f"ai_{i:02d}.mp4"
        res = fal_media.generate_kling_video(
            clip["prompt"], dest, duration=d,
            negative_prompt=clip.get("negative_prompt"))
        if res:
            new_paths.append(Path(res)); spent_sec += d
        else:
            logger.warning(f"ai_video regen: clip {i} failed again")
    cost = spent_sec * KLING_PRICE_PER_SEC_USD
    logger.info(f"ai_video regen: {len(new_paths)}/{len(indices)} clips filled, est cost ${cost:.2f}")
    return new_paths, cost


_REVISE_PROMPT = (
    "Ты — режиссёр AI-видео Kling 3.0. Дан текущий промпт ОДНОГО клипа и правка от "
    "пользователя (надиктована голосом, по-русски). Перепиши промпт с учётом правки, "
    "СОХРАНив формат Kling: на английском, multi-shot, 60–100 слов, явные субъекты и их "
    "действия, корректная анатомия рук/лиц, БЕЗ любого текста/надписей/UI на экране "
    "(это главное правило — на экране телефона/вывесках ничего читаемого). Верни ТОЛЬКО "
    'JSON {"prompt":"...","negative_prompt":"..."} без markdown и пояснений.'
)


def revise_clip_prompt(out_dir, index, instruction, claude=None):
    """LLM-правка промпта клипа N по инструкции пользователя (с голоса) — обновляет
    plan.json. Сам ре-рендер делает regen_ai_clips([N]) ПОСЛЕ этой правки.
    Возвращает новый промпт (str) или None при сбое (тогда старый промпт цел)."""
    clips_dir = Path(out_dir) / CLIPS_SUBDIR
    plan = _read_plan(clips_dir)
    if not plan or not plan.get("clips"):
        return None
    by_index = {int(c["i"]): c for c in plan["clips"] if "i" in c}
    clip = by_index.get(int(index))
    if not clip:
        return None
    if claude is None:
        claude = _default_claude()
    user = (
        f"{_REVISE_PROMPT}\n\n"
        f"ТЕКУЩИЙ ПРОМПТ:\n{clip.get('prompt', '')}\n\n"
        f"ТЕКУЩИЙ negative_prompt:\n{clip.get('negative_prompt') or HOUSE_NEGATIVE}\n\n"
        f"ПРАВКА ПОЛЬЗОВАТЕЛЯ: {instruction}"
    )
    flock = _acquire_director_lock()
    try:
        resp = claude.messages.create(
            model=_DIRECTOR_MODEL, max_tokens=_DIRECTOR_MAX_TOKENS,
            messages=[{"role": "user", "content": user}])
        raw = getattr(resp.content[0], "text", "") if resp.content else ""
        data = json.loads(_strip_fences(raw))
    except Exception as e:
        logger.warning(f"ai_video revise: LLM/parse failed: {e}")
        return None
    finally:
        _release_director_lock(flock)
    new_prompt = (data.get("prompt") or "").strip() if isinstance(data, dict) else ""
    if len(new_prompt) < MIN_PROMPT_LEN:
        logger.warning("ai_video revise: revised prompt too short — keeping old")
        return None
    new_neg = (data.get("negative_prompt") or "").strip() if isinstance(data, dict) else ""
    clip["prompt"] = new_prompt
    clip["negative_prompt"] = new_neg if new_neg else (clip.get("negative_prompt") or HOUSE_NEGATIVE)
    try:
        (clips_dir / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"ai_video revise: write plan.json failed: {e}")
        return None
    logger.info(f"ai_video revise: clip {index} prompt updated ({len(new_prompt)} chars)")
    return new_prompt
