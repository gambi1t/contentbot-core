"""Майнер seeds для банка идей из транскриптов планёрок (Plaud).

11 июня 2026. Идея одобрена Артёмом с ЯВНЫМ требованием приватности:
из планёрок Максима нельзя выпустить наружу имена, деньги, контрагентов,
конкретные постановки задач. Достаём только ОБЕЗЛИЧЕННЫЕ предпринимательские
темы-углы уровня существующих seeds («Делегирование задачи vs делегирование
решения»). Seeds дальше попадают в idea-промпт → идеи → контент, поэтому
фильтр консервативный: сомнительное РЕЖЕМ (лучше потерять тему, чем утечь).

Три слоя защиты:
  1. extraction-промпт — LLM сразу просят обобщать и запрещают конкретику;
  2. judge-промпт — вторая LLM-проверка списка кандидатов на утечки;
  3. _privacy_filter — механика: цифры/деньги/%/имена/дни недели/поручения.
Плюс дедуп против существующих seeds (Jaccard из idea_generator).

Запуск (на сервере, транскрипты предварительно выгружены в файлы):
  python plaud_seed_miner.py --files /tmp/plaud_tr/*.txt \
      --seeds idea_seeds_maksim.txt --out /tmp/seed_candidates.txt
Результат НЕ дописывается в прод-файл автоматически — кандидаты ревьюятся
человеком/оркестратором и добавляются отдельным шагом.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SEED_MODEL = os.getenv("PLAUD_SEED_MODEL", "claude-opus-4-8")
MAX_SEEDS_PER_TRANSCRIPT = 5
MIN_LEN, MAX_LEN = 10, 100

# Стемы частых русских имён (lowercase). Совпадение по началу токена —
# ловит падежи («Сергея», «Серёже»). Консервативно: режем строку целиком.
_NAME_STEMS = (
    "сергe", "серге", "серёж", "сереж", "алекс", "андре", "иван", "дмитр",
    "димо", "марин", "елен", "ольг", "наталь", "татьян", "анастаси", "насти",
    "светлан", "екатерин", "никит", "михаил", "миш", "владимир", "вов",
    "волод", "констант", "кост", "павел", "павл", "паш", "артём", "артем",
    "максим", "юр", "евгени", "жен", "виктор", "вить", "николa", "никола",
    "коль", "анн", "ирин", "оксан", "галин", "людмил", "руслан", "тимур",
    "денис", "антон", "степан", "стёп", "степ", "роман", "ром", "игор",
    "егор", "илья", "иль", "фёдор", "федор", "глеб", "лёш", "леш", "саш",
    "кирилл",
)

# Маркеры конкретики: деньги, поручения, сроки.
_FORBIDDEN_SUBSTRINGS = (
    "руб", "₽", "%", "тыс.", "млн", "поставить задачу", "поставь задачу",
    "ставлю задачу", "нужно сделать", "надо сделать", "сделать до",
    "закупить", "оплатить", "позвонить", "договорит",
    "понедельник", "вторник", "в среду", "четверг", "пятниц", "суббот",
    "воскресень", "завтра", "послезавтра",
)


def _privacy_filter(lines: list[str]) -> tuple[list[str], list[str]]:
    """Механический слой: что НЕ может быть в seed. Возвращает (ok, rejected).

    Консервативно: цифры режутся целиком (суммы/проценты/сроки — главный
    канал утечки конкретики), имена — по стемам с падежами.
    """
    ok: list[str] = []
    rejected: list[str] = []
    for raw in lines:
        s = " ".join((raw or "").split()).strip()
        if not (MIN_LEN <= len(s) <= MAX_LEN):
            rejected.append(raw)
            continue
        low = s.lower()
        if any(ch.isdigit() for ch in s):
            rejected.append(raw)
            continue
        if any(sub in low for sub in _FORBIDDEN_SUBSTRINGS):
            rejected.append(raw)
            continue
        tokens = [re.sub(r"[^\wёЁ-]", "", t).lower() for t in s.split()]
        if any(t and any(t.startswith(stem) and len(t) - len(stem) <= 4
                         for stem in _NAME_STEMS)
               for t in tokens):
            rejected.append(raw)
            continue
        ok.append(s)
    return ok, rejected


def _extraction_prompt(transcript: str) -> str:
    return f"""Ты — контент-стратег предпринимателя (база отдыха, картинг, команда ~50 человек).
Ниже — транскрипт его рабочей планёрки. Выдели из него ДО {MAX_SEEDS_PER_TRANSCRIPT} тем-зацепок
для будущего контента (короткие видео/посты про реальный бизнес).

🔴 ЖЁСТКИЕ ПРАВИЛА ОБЕЗЛИЧИВАНИЯ (нарушение = провал):
- НИКАКИХ имён людей и названий компаний/контрагентов/объектов.
- НИКАКИХ цифр: ни денег, ни процентов, ни сроков, ни количеств.
- НИКАКИХ конкретных задач/поручений/решений с планёрки («кто что должен сделать»).
- Тема = УНИВЕРСАЛЬНАЯ боль/дилемма предпринимателя, узнаваемая любым владельцем
  бизнеса. Уровень обобщения как в примерах:
  «Делегирование задачи vs делегирование решения»
  «Сотрудник, который тянет команду вниз, но "он со мной с самого начала"»
  «Текучка как симптом, а не как проблема»
- Если тему нельзя обезличить без потери смысла — НЕ включай её.
- Каждая тема: одна строка, 30-90 символов, без точки в конце.

ФОРМАТ ОТВЕТА: только JSON-массив строк, без пояснений. Нет тем → [].

ТРАНСКРИПТ:
{transcript}"""


def _judge_prompt(candidates: list[str]) -> str:
    listing = "\n".join(f"- {c}" for c in candidates)
    return f"""Ты — аудитор конфиденциальности. Ниже — строки-темы, извлечённые из ЗАКРЫТОЙ
рабочей планёрки. Проверь КАЖДУЮ: содержит ли она персональные данные, имена,
деньги/цифры, названия контрагентов, конкретные поручения или любую деталь,
по которой можно опознать внутреннюю кухню конкретного бизнеса.

Строки:
{listing}

Верни JSON-массив ТОЛЬКО безопасных строк (полностью обезличенных,
универсальных для любого предпринимателя). Сомневаешься — выбрасывай.
Только JSON, без пояснений."""


def _parse_seed_json(text: str) -> list[str]:
    """JSON-массив строк из ответа LLM (терпимо к прозе вокруг)."""
    t = (text or "").strip()
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        arr = json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return []
    return [s.strip() for s in arr if isinstance(s, str) and s.strip()]


def _dedup_new_seeds(new: list[str], existing: list[str],
                     threshold: float = 0.45) -> list[str]:
    """Отрезает кандидатов, похожих на существующие seeds (Jaccard)."""
    from idea_generator import _jaccard
    out: list[str] = []
    for cand in new:
        pool = existing + out
        if any(_jaccard(cand, e) >= threshold for e in pool):
            continue
        out.append(cand)
    return out


def _llm_client():
    tok = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if tok:
        from claude_subscription import SubscriptionClient
        return SubscriptionClient(tok, extra_env={"MAX_THINKING_TOKENS": "0"})
    import anthropic
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


def _llm_seeds(client, prompt: str) -> list[str]:
    resp = client.messages.create(
        model=SEED_MODEL, max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_seed_json(resp.content[0].text)


def _load_existing_seeds(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def mine(files: list[Path], seeds_path: Path) -> tuple[list[str], dict]:
    """Полный конвейер: файлы транскриптов → безопасные кандидаты seeds."""
    client = _llm_client()
    existing = _load_existing_seeds(seeds_path)
    stats = {"files": 0, "extracted": 0, "after_judge": 0,
             "after_filter": 0, "after_dedup": 0}
    candidates: list[str] = []
    for f in files:
        text = Path(f).read_text(encoding="utf-8", errors="replace").strip()
        if len(text) < 200:
            logger.info(f"[seed-miner] {Path(f).name}: слишком короткий, пропуск")
            continue
        stats["files"] += 1
        extracted = _llm_seeds(client, _extraction_prompt(text[:24000]))
        stats["extracted"] += len(extracted)
        if not extracted:
            continue
        judged = _llm_seeds(client, _judge_prompt(extracted))
        stats["after_judge"] += len(judged)
        safe, rejected = _privacy_filter(judged)
        stats["after_filter"] += len(safe)
        for r in rejected:
            logger.info(f"[seed-miner] отрезано фильтром: {r!r}")
        candidates.extend(safe)
    fresh = _dedup_new_seeds(candidates, existing)
    stats["after_dedup"] = len(fresh)
    return fresh, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Plaud → обезличенные seed-кандидаты")
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--seeds", default="idea_seeds_maksim.txt")
    ap.add_argument("--out", default="/tmp/seed_candidates.txt")
    args = ap.parse_args()

    fresh, stats = mine([Path(f) for f in args.files], Path(args.seeds))
    Path(args.out).write_text("\n".join(fresh) + "\n", encoding="utf-8")
    print(f"stats: {stats}")
    print(f"кандидатов: {len(fresh)} → {args.out}")
    for s in fresh:
        print(f"  - {s}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
