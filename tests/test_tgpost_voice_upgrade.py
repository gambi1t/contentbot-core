"""TDD: голос-апгрейд TG-постов Артёма (Илон). Только ARTEM, Максим нетронут.

Проверяем по спеке spec_tgpost_voice_upgrade.md:
  1. позитивный камертон стоит ДО раздела запретов;
  2. эталоны: старые 2 «отчётных» убраны, вшит GOLD (эталон A verbatim + B);
  3. анти-машинный блок вшит в _POLISH_SYSTEM_ARTEM, а polish — brand-aware
     (для Артёма переписывает тело, для Максима остаётся узким);
  4. сургический редактор стал brand-aware (ARTEM-вариант + резолв бренда);
  5. голос Максима (SYSTEM_PROMPT_MAKSIM / _POLISH_SYSTEM_MAKSIM) не задет.

Запуск: python -m pytest tests/test_tgpost_voice_upgrade.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

os.environ.setdefault("NOTION_TOKEN", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
os.environ.setdefault("NOTION_DATABASE_ID", "dummy")
os.environ.setdefault("TELEGRAM_TOKEN", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tg_post_writer as W  # noqa: E402

ARTEM = W.SYSTEM_PROMPT_ARTEM
MAKSIM = W.SYSTEM_PROMPT_MAKSIM
BOT_SRC = (ROOT / "bot.py").read_text(encoding="utf-8")


# ── 1. Камертон до запретов ──────────────────────────────────────────────

def test_kamerton_present_before_rules():
    assert "КАМЕРТОН ГОЛОСА" in ARTEM, "нет блока камертона"
    assert ARTEM.index("КАМЕРТОН ГОЛОСА") < ARTEM.index("ЖЁСТКИЕ ПРАВИЛА ТОНА"), (
        "камертон должен стоять ДО раздела запретов"
    )


def test_kamerton_positive_voice_cues():
    # позитивные «делай так», не запреты
    for cue in ("за столом", "самоиро", "стыдливо лежат", "Балабол палится"):
        assert cue in ARTEM, f"камертон без ключевого свойства: {cue}"


# ── 2. Эталоны: GOLD вшит, старые убраны ─────────────────────────────────

def test_etalon_a_verbatim():
    # ключевые фразы эталона A (его слова — не перефраз)
    for line in (
        "Не пропал и не на Бали (звучало бы красивее)",
        "первые мои боты до сих пор где-то стыдливо лежат",
        "1000 часов — это объём работы, а не время за компом",
        "Только без сказок: нейронка не сделала всё за меня",
    ):
        assert line in ARTEM, f"эталон A искажён/неполон: нет «{line}»"


def test_old_report_etalons_removed():
    assert "ЭТАЛОН 1 — ВВОДНЫЙ ПОСТ" not in ARTEM, "старый эталон 1 не удалён"
    assert "ЭТАЛОН 2 — ЭТАП 1" not in ARTEM, "старый эталон 2 не удалён"


def test_etalon_b_live_lines():
    assert "похоже на пульт от самолёта" in ARTEM, "нет живых строк эталона B"
    assert "ЭТАЛОН ГОЛОСА A" in ARTEM and "ЭТАЛОН ГОЛОСА B" in ARTEM


# ── 3. Анти-машинный фильтр в polish + brand-aware ───────────────────────

def test_polish_artem_has_antimachine():
    p = W._POLISH_SYSTEM_ARTEM
    assert "машинные обороты" in p, "анти-машинный блок не вшит в polish ARTEM"
    assert "в современном мире" in p, "нет стоп-листа AI-маркеров"
    assert "прочитай вслух" in p, "нет теста вслух"


def test_polish_brand_aware_task():
    body = inspect.getsource(W._polish_post_finale)
    assert "polish_task" in body, "polish-задача не вынесена"
    assert 'if brand != "maksim"' in body, "polish-задача не brand-aware"
    # для Артёма — мандат переписать тело
    assert "тело трогать можно и нужно" in body


def test_polish_maksim_stays_narrow():
    # анти-машинная переписка тела к Максиму НЕ применяется
    assert "машинные обороты" not in W._POLISH_SYSTEM_MAKSIM, (
        "анти-машинный блок протёк в polish Максима"
    )


# ── 4. Сургический редактор brand-aware (bot.py) ─────────────────────────

def test_surg_editor_artem_const_added():
    assert "_TGPOST_SURG_EDITOR_SYSTEM_ARTEM" in BOT_SRC, "нет ARTEM-редактора"
    assert "точечный редактор постов под голос Артёма" in BOT_SRC


def test_surg_editor_brand_resolved():
    i = BOT_SRC.index("def _apply_tgpost_surg_edit")
    body = BOT_SRC[i:i + 1600]
    assert "_get_active_brand_name()" in body, "редактор не резолвит бренд"
    assert "_editor_system" in body and 'brand == "maksim"' in body, (
        "редактор не выбирает систему по бренду"
    )
    assert "system=_editor_system" in body, "create() не использует выбранную систему"


# ── 5. Голос Максима не задет ────────────────────────────────────────────

def test_maksim_voice_untouched():
    assert "SYSTEM_PROMPT_MAKSIM" in dir(W)
    assert "Юмсунов" in MAKSIM, "промпт Максима повреждён"
    # MAKSIM-редактор всё ещё есть и используется для его ветки
    assert "_TGPOST_SURG_EDITOR_SYSTEM " in BOT_SRC or "_TGPOST_SURG_EDITOR_SYSTEM\n" in BOT_SRC


# ── 6. MAJOR-фикс ревью: бюджет токенов polish + страж обрыва ─────────────

class _FakeResp:
    def __init__(self, text, stop_reason):
        self.content = [type("C", (), {"text": text})()]
        self.stop_reason = stop_reason


class _FakeClient:
    """Мок claude-клиента: ловит kwargs create() и отдаёт заданный ответ."""
    def __init__(self, text, stop_reason):
        self._t, self._s, self.kw = text, stop_reason, None
        self.messages = self

    def create(self, **kw):
        self.kw = kw
        return _FakeResp(self._t, self._s)


def test_polish_token_budget_scales_for_artem():
    draft = "А" * 1000  # длинный пост → бюджет должен превысить дефолт 1500
    c = _FakeClient("Б" * 900, "end_turn")
    W._polish_post_finale(draft, c, "default")
    assert c.kw["max_tokens"] > 1500, "бюджет токенов не вырос под длинный пост Артёма"


def test_polish_token_budget_default_for_maksim():
    draft = "А" * 1000
    c = _FakeClient("Б" * 900, "end_turn")
    W._polish_post_finale(draft, c, "maksim")
    assert c.kw["max_tokens"] == 1500, "бюджет Максима не должен меняться (узкая правка)"


def test_polish_truncation_guard_returns_draft():
    # Обрыв по лимиту: усечёнка = 80% длины (прошла бы старую страховку <0.5),
    # но stop_reason=max_tokens → должен вернуться ИСХОДНЫЙ черновик.
    draft = "оригинальный пост с фактами и цифрами. " * 40
    truncated = draft[: int(len(draft) * 0.8)]
    c = _FakeClient(truncated, "max_tokens")
    out = W._polish_post_finale(draft, c, "default")
    assert out == draft, "обрыв по max_tokens на 80% должен вернуть исходник, а не усечёнку"


def test_polish_happy_path_returns_polished():
    draft = "оригинал поста. " * 60
    polished = "живой переписанный пост Артёма. " * 50
    c = _FakeClient(polished, "end_turn")
    out = W._polish_post_finale(draft, c, "default")
    assert out == polished.strip(), "нормальный ответ должен пройти как отполированный"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
