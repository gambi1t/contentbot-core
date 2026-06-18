"""Durable draft + BrollItem-контракт для Pipeline 2 «B-roll без аватара».

13 июня 2026, по синтезу CTO-ревью. До этого черновик жил только в
`context.user_data` (in-memory) — терпимо для одношагового preview→approve,
но новый флоу (ручной выбор / загрузка / HF 8-25 мин / микс) рвётся при
рестарте бота посреди длинной ветки («ждал 20 мин → всё пропало»).

Решение (ужато под 1-2 клиентов, без SQLite/CAS):
- `BrollItem` — богатый элемент видеоряда (kind/origin/path/role), живёт до
  самого materialize-шага вместо сырого clip_path.
- `BrollDraft` — состояние ветки: status, source_mode, items, work_dir, ttl.
- Атомарная запись (tempfile + os.replace — паттерн bot_state.save_pending),
  отдельный файл на черновик в `broll_drafts/<id>.json` (не мешаем в общий
  pending.json — чище по ответственности).
- Stale-callback / double-launch ловим по `status`, не по version-CAS.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("broll.draft")

_TMP_SUFFIX = ".tmp"
_ITEM_KINDS = ("video", "image", "hf_scene")
_ITEM_ORIGINS = ("library", "upload", "auto", "hf")


class Status:
    AWAITING_SOURCE = "awaiting_source"
    SELECTING_MANUAL = "selecting_manual"
    UPLOADING = "uploading"
    PLANNING_MIX = "planning_mix"
    HF_RUNNING = "hf_running"
    PREVIEW_READY = "preview_ready"
    ASSEMBLING = "assembling"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"
    ALL = (
        AWAITING_SOURCE, SELECTING_MANUAL, UPLOADING, PLANNING_MIX, HF_RUNNING,
        PREVIEW_READY, ASSEMBLING, DONE, FAILED, EXPIRED,
    )


class SourceMode:
    AUTO = "auto"
    MANUAL = "manual"
    UPLOAD = "upload"
    HF_ONLY = "hf_only"
    AUTO_HF = "auto_hf"
    AI_VIDEO = "ai_video"            # фуллскрин Seedance — кнопка в меню
    AI_VIDEO_GO = "ai_video_go"      # «Запустить» с экрана подтверждения (не в меню)
    AI_VIDEO_MENU = "ai_video_menu"  # «Назад к источникам» (не в меню)
    ALL = (AUTO, MANUAL, UPLOAD, HF_ONLY, AUTO_HF, AI_VIDEO, AI_VIDEO_GO, AI_VIDEO_MENU)


@dataclass
class BrollItem:
    """Элемент видеоряда — богатая структура до materialize-шага.

    kind: video | image | hf_scene; origin: library | upload | auto | hf.
    Сырой mp4-путь получается из любого item только перед сборкой
    (см. broll.materialize), что упрощает preview/fallback/cleanup.

    Поля строго те, что использует Фаза 1 (kind/origin/path/label). Поля под
    микс (semantic_role и т.п.) добавляются в Фазе 3, когда VisualBeatPlan их
    реально потребует — не закладываем под гипотетику.
    """
    kind: str
    origin: str
    path: str
    label: str = ""

    def __post_init__(self):
        if self.kind not in _ITEM_KINDS:
            raise ValueError(f"BrollItem.kind={self.kind!r} не из {_ITEM_KINDS}")
        if self.origin not in _ITEM_ORIGINS:
            raise ValueError(f"BrollItem.origin={self.origin!r} не из {_ITEM_ORIGINS}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BrollItem":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def hf_items_from_clips(clip_paths) -> list[BrollItem]:
    """Готовые hf_NN.mp4 от generate_hyperframes_broll → BrollItem(hf_scene/hf).
    materialize пропускает hf_scene без перекодирования (клипы уже mp4)."""
    out: list[BrollItem] = []
    for p in clip_paths:
        if not p:
            continue
        out.append(BrollItem(
            kind="hf_scene", origin="hf", path=str(p), label=f"hf/{Path(p).name}",
        ))
    return out


def from_picker_items(picker_items) -> list[BrollItem]:
    """selfie.broll_picker.BrollItem (duck-typed: .kind/.source/.label) →
    draft.BrollItem. Граница между переиспользуемым пикером селфи и durable-
    слоем Pipeline 2. origin выводим из метки: 'upload/...' → upload, иначе
    library."""
    out: list[BrollItem] = []
    for it in picker_items:
        label = getattr(it, "label", None) or ""
        origin = "upload" if label.startswith("upload/") else "library"
        out.append(BrollItem(
            kind=it.kind, origin=origin, path=str(it.source), label=label,
        ))
    return out


@dataclass
class BrollDraft:
    draft_id: str
    user_id: int
    chat_id: int
    status: str
    source_mode: str | None
    script_text: str
    voice_estimate_sec: float
    source_items: list[BrollItem] = field(default_factory=list)
    work_dir: str = ""
    notion_url: str | None = None
    notion_page_id: str | None = None  # для update_notion_status при готовности
    theme: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    ttl_hours: float = 24.0

    def touch(self, now: float) -> None:
        self.updated_at = now

    def is_expired(self, now: float) -> bool:
        return (now - self.created_at) > self.ttl_hours * 3600

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_items"] = [i.to_dict() for i in self.source_items]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BrollDraft":
        d = dict(d)
        d["source_items"] = [BrollItem.from_dict(x) for x in d.get("source_items", [])]
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def new_draft_id(user_id: int, ts: float) -> str:
    """Стабильный по входу id черновика. ts передаём явно (детерминизм тестов;
    в проде — time.time())."""
    return f"broll_{user_id}_{int(ts * 1000)}"


def _draft_path(drafts_dir: Path, draft_id: str) -> Path:
    return Path(drafts_dir) / f"{draft_id}.json"


def save_draft(draft: BrollDraft, drafts_dir: Path) -> Path:
    """Атомарная запись (tempfile + os.replace) — переживает рестарт/краш."""
    drafts_dir = Path(drafts_dir)
    drafts_dir.mkdir(parents=True, exist_ok=True)
    dst = _draft_path(drafts_dir, draft.draft_id)
    tmp = dst.with_suffix(dst.suffix + _TMP_SUFFIX)
    tmp.write_text(json.dumps(draft.to_dict(), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(str(tmp), str(dst))
    return dst


def load_draft(draft_id: str, drafts_dir: Path) -> BrollDraft | None:
    p = _draft_path(Path(drafts_dir), draft_id)
    if not p.is_file():
        return None
    try:
        return BrollDraft.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning(f"[broll.draft] битый черновик {draft_id}: {e}")
        return None


def cleanup_expired(drafts_dir: Path, now: float) -> int:
    """Удаляет истёкшие черновики (по created_at + ttl). Возвращает число."""
    drafts_dir = Path(drafts_dir)
    if not drafts_dir.is_dir():
        return 0
    removed = 0
    for f in drafts_dir.glob("broll_*.json"):
        try:
            d = BrollDraft.from_dict(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
        if d.is_expired(now):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed
