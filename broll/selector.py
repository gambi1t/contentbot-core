"""Metadata-aware B-roll clip selector — Pipeline #2.

По сценарию закадрового ролика выбирает 5-9 релевантных клипов из
тегированного архива Максима. У каждого клипа есть JSON-сайдкар
`<имя>.mov.json` с полями description / tags / scene / quality_grade /
has_people (заполняет tag_clips.py через Claude vision).

Выбор: Claude (Sonnet) ранжирует клипы под сценарий и выстраивает их в
порядке повествования. Фоллбэк при сбое LLM — scene-keyword матчинг.
"""
from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("broll.selector")

# Архив клипов на проде. Переопределяется для локальных тестов.
DEFAULT_CLIPS_ROOT = Path("/home/maksim-bot/maksim-bot/broll-library/clips/maksim")

_CLIP_EXT = (".mov", ".mp4")

# scene-папка → ключевые слова в сценарии (для фоллбэка без LLM).
_SCENE_KEYWORDS: dict[str, list[str]] = {
    "karting": ["картинг", "карт", "трасс", "заезд", "гонк", "болид",
                "пилот", "руль", "скорост", "адреналин", "драйв"],
    "glamping": ["глэмпинг", "глемпинг", "глэмп", "домик", "природ",
                 "лес", "отдых", "туризм", "бронир", "загород"],
    "sup": ["sup", "сап", "сапборд", "доск", "вода", "река", "озер",
            "сплав", "набережн"],
    "personal": ["семья", "семь", "дети", "ребён", "дочь", "сын",
                 "команд", "сотрудник", "корпоратив", "праздник"],
}


class SelectorError(Exception):
    """Не удалось выбрать клипы (пустой архив)."""


@dataclass
class Clip:
    """Клип архива + его метаданные."""
    path: Path
    scene: str = "other"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    quality_grade: str = "broll"
    has_people: bool = False


def _load_archive(clips_root: Path) -> list[Clip]:
    """Собрать все клипы архива с метаданными. Отсеять quality_grade==weak.

    Сайдкар метаданных — `<имя файла клипа>.json` рядом с клипом.
    Клипы без сайдкара берутся с дефолтами (scene из имени папки).
    """
    clips_root = Path(clips_root)
    if not clips_root.is_dir():
        logger.warning(f"[broll.selector] архив не найден: {clips_root}")
        return []

    clips: list[Clip] = []
    for f in sorted(clips_root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in _CLIP_EXT:
            continue
        scene_from_dir = f.parent.name
        meta_path = f.with_name(f.name + ".json")
        scene, desc, tags, grade, has_people = scene_from_dir, "", [], "broll", False
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                scene = meta.get("scene") or scene_from_dir
                desc = meta.get("description", "") or ""
                tags = meta.get("tags", []) or []
                grade = meta.get("quality_grade", "broll") or "broll"
                has_people = bool(meta.get("has_people", False))
            except Exception as e:
                logger.warning(f"[broll.selector] битый сайдкар {meta_path.name}: {e}")
        if grade == "weak":
            continue
        clips.append(Clip(
            path=f, scene=scene, description=desc, tags=tags,
            quality_grade=grade, has_people=has_people,
        ))
    logger.info(f"[broll.selector] архив: {len(clips)} клипов из {clips_root}")
    return clips


def _build_catalog(clips: list[Clip]) -> str:
    """Компактный текстовый каталог клипов для промпта Claude."""
    lines = []
    for i, c in enumerate(clips):
        tags = ", ".join(c.tags[:8])
        lines.append(
            f"[{i}] сцена={c.scene} grade={c.quality_grade} | "
            f"{c.description} | теги: {tags}"
        )
    return "\n".join(lines)


_SYSTEM = """Ты — видеоредактор. Подбираешь B-roll нарезки под закадровый \
текст вертикального ролика (Instagram Reels / TikTok / Shorts) для бренда \
Life Drive (картинг, глэмпинг, SUP, Тюмень).

Тебе дают СЦЕНАРИЙ закадрового голоса и КАТАЛОГ доступных клипов с описаниями.
Задача — выбрать клипы, которые лягут видеорядом под этот текст, и выстроить \
их в порядке повествования.

ПРАВИЛА:
- Выбери от {n_min} до {n_max} клипов.
- Порядок в ответе = порядок появления в ролике. Первый клип — сильный, \
  цепляющий кадр под хук сценария.
- Клипы должны поддерживать смысл сценария. Если сценарий про бизнес/команду — \
  бери рабочие/корпоративные кадры; про драйв — картинг-трассу; про отдых — глэмпинг.
- grade=broll — универсальные кадры (предпочтительны). grade=event — конкретные \
  события, бери только если прямо по теме.
- Не повторяй один клип дважды.
- Разнообразь планы: не 9 почти одинаковых кадров подряд.

ФОРМАТ ОТВЕТА — строго JSON-массив индексов клипов, без пояснений:
[3, 17, 0, 22, 9, 14]"""


def _extract_int_array(s: str) -> list[int]:
    """Выдернуть первый JSON-массив целых из ответа LLM.

    Устойчиво к пояснительному тексту вокруг и к нескольким массивам:
    берёт первый блок [...] из одних цифр/запятых/пробелов. Прошлый
    вариант (first-[ … last-]) ломался, когда Claude добавлял текст со
    скобкой или несколько массивов → JSONDecodeError, всегда фоллбэк.
    """
    m = re.search(r"\[[\s\d,]*\]", s)
    if not m:
        raise ValueError("в ответе нет JSON-массива чисел")
    arr = json.loads(m.group(0))
    if not isinstance(arr, list):
        raise ValueError("распарсенное значение — не список")
    return [int(x) for x in arr]


def _fallback_select(
    clips: list[Clip], script: str, n_min: int, n_max: int,
) -> list[Clip]:
    """Фоллбэк без LLM: scene-keyword матчинг по сценарию.

    Определяет основную сцену по ключевым словам, берёт её клипы, добивает
    из остальных до n_max. broll-grade в приоритете.
    """
    t = (script or "").lower()
    best_scene, best_hits = None, 0
    for scene, kws in _SCENE_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in t)
        if hits > best_hits:
            best_scene, best_hits = scene, hits

    def _rank(c: Clip) -> tuple:
        # сначала клипы целевой сцены, потом broll-grade, остальное — в конец
        return (
            0 if c.scene == best_scene else 1,
            0 if c.quality_grade == "broll" else 1,
        )

    pool = sorted(clips, key=_rank)
    n = max(n_min, min(n_max, len(pool)))
    chosen = pool[:n]
    random.shuffle(chosen)
    logger.info(
        f"[broll.selector] фоллбэк: сцена={best_scene or '—'}, "
        f"выбрано {len(chosen)} клипов"
    )
    return chosen


def select_clips(
    script: str,
    claude,
    n_min: int = 5,
    n_max: int = 9,
    clips_root: Path | str = DEFAULT_CLIPS_ROOT,
    model: str = "claude-sonnet-4-6",
) -> list[Path]:
    """Выбрать упорядоченный список клипов под сценарий ролика.

    script      — текст закадрового сценария.
    claude       — anthropic.Anthropic клиент (или None → сразу фоллбэк).
    n_min/n_max  — границы числа клипов.
    clips_root   — корень архива клипов.
    model        — модель Claude для ранжирования.

    Возвращает список Path в порядке появления в ролике.
    Бросает SelectorError если архив пуст.
    """
    clips = _load_archive(Path(clips_root))
    if not clips:
        raise SelectorError(f"архив клипов пуст или недоступен: {clips_root}")

    # Меньше клипов в архиве, чем n_min — берём что есть.
    n_max = min(n_max, len(clips))
    n_min = min(n_min, n_max)

    if claude is None:
        return [c.path for c in _fallback_select(clips, script, n_min, n_max)]

    catalog = _build_catalog(clips)
    system = _SYSTEM.format(n_min=n_min, n_max=n_max)
    user_msg = (
        f"СЦЕНАРИЙ ЗАКАДРОВОГО ГОЛОСА:\n{script}\n\n"
        f"КАТАЛОГ КЛИПОВ ({len(clips)} шт.):\n{catalog}\n\n"
        f"Верни JSON-массив из {n_min}-{n_max} индексов клипов в порядке ролика."
    )

    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text if resp.content else ""
        idxs = _extract_int_array(raw)
    except Exception as e:
        logger.warning(f"[broll.selector] LLM-выбор упал ({type(e).__name__}: {e}) "
                        f"→ фоллбэк")
        return [c.path for c in _fallback_select(clips, script, n_min, n_max)]

    # Валидация: уникальные, в диапазоне, нужное количество.
    seen: set[int] = set()
    valid: list[int] = []
    for x in idxs:
        if 0 <= x < len(clips) and x not in seen:
            seen.add(x)
            valid.append(x)
    if len(valid) < n_min:
        logger.warning(f"[broll.selector] LLM вернул {len(valid)} валидных "
                        f"индексов (<{n_min}) → фоллбэк")
        return [c.path for c in _fallback_select(clips, script, n_min, n_max)]

    valid = valid[:n_max]
    logger.info(f"[broll.selector] LLM выбрал {len(valid)} клипов")
    return [clips[i].path for i in valid]


__all__ = ["select_clips", "SelectorError", "Clip", "DEFAULT_CLIPS_ROOT"]
