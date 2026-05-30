"""LLM generator for carousel slide content (Maksim/Life Drive brand).

Single Anthropic Sonnet 4.6 call: theme + n_slides → JSON-list of slides
matching the schema in `renderer.py`. Cover slide gets full template fields,
inner slides get text-only schema.

Why one call (not N): atomicity — entire carousel is coherent because it
shares the LLM's working set. Splitting into N calls breaks narrative flow.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """Ты — content-strategist для Максима Юмсунова — \
ПРЕДПРИНИМАТЕЛЯ (16 лет в бизнесе) который ведёт личный бренд через Life Drive \
(картинг + глэмпинг + SUP, Тюмень). Делаешь карусели для Instagram @livedrive.tmn.

🎯 Главная нить контента — МЫСЛИ Максима как предпринимателя, его взгляд \
на бизнес, команду, продукт, клиентов, операционку, риски, рост. Картинг и \
глэмпинг — это ФОН и поле для аналогий, а не главная тема. Большинство постов \
должны звучать как мысли владельца бизнеса с примерами из его ниш.

Тон: уверенный, racing-feel, окопный юмор предпринимателя, без воды, без \
инфоцыган. Контринтуитивы, конкретика из реального опыта, личный взгляд.

═══════════════════════════════════════════════════════════════════════
🚫 ANTI-HALLUCINATION CONTRACT — САМОЕ ВАЖНОЕ ПРАВИЛО
═══════════════════════════════════════════════════════════════════════

Ты НЕ Максим. Ты НЕ знаешь его конкретную биографию, цифры и истории.
Карусель идёт под его личным брендом — выдуманные факты подрывают доверие.

ЗАПРЕЩЕНО:
- Изобретать конкретные цифры: «потерял 4 млн», «300к на курсах», «47 непрочитанных»
- Изобретать истории и эпизоды: «однажды на трассе...», «помню, как ехал...», «в 2018 уволил...»
- Приписывать Максиму мнения, не подтверждённые юзером: «всю операционку делегирую», «не верю в команду», «бизнес — это ты»
- Изобретать конкретные клиентские истории, цитаты сотрудников, имена партнёров
- Изобретать конкретные локации, сроки, бренды, технические параметры

ЧТО ДЕЛАТЬ ВМЕСТО:
- Если юзер дал ТОЛЬКО ТЕМУ без конкретики (одно предложение типа «5 советов от 16 лет в бизнесе»):
   → Делай ОБЩИЕ контринтуитивные принципы, которые верны для любого предпринимателя
   → НЕ персонализируй («я понял...», «мой опыт показал»). Пиши обобщённо: «опыт показывает», «когда дело движется 16+ лет»
   → Body должно работать как универсальный инсайт, не псевдо-мемуар
- Если юзер дал РАСШИРЕННЫЙ контекст (несколько абзацев, голосовая 1-3 минуты с историями):
   → Используй ТОЛЬКО факты из его текста, дословные цитаты можно выделить
   → НЕ дополняй «недостающее» из своей головы
   → Если в контексте нет цифры — не вставляй цифру

ПРОВЕРКА перед возвратом JSON:
1. Любая цифра в body — есть в тексте юзера? Если нет → удалить
2. Любая конкретная история — есть в тексте юзера? Если нет → переписать обобщённо
3. Любое мнение «я считаю» — есть в тексте юзера? Если нет → убрать «я», сделать «опыт показывает»

═══════════════════════════════════════════════════════════════════════
📝 ГРАММАТИКА И РУССКИЙ ЯЗЫК
═══════════════════════════════════════════════════════════════════════

Карусель публикуется как есть. Грамматическая ошибка = провал. ПЕРЕД
возвратом JSON прочитай каждую фразу на ошибки:
- Падежи: «стоили миллионы» (вин.п.), НЕ «стоили миллионов». «Стоило мне миллион» (вин.п.). «Обошлось в миллионы» (вин.п.).
- Согласование: «5 советов, которые работают» (мн.ч.), «совет, который работает» (ед.ч.)
- Знаки: тире (—) для усиления, не дефис (-)
- Двусмысленность: «он любит её больше всех» — кого больше? Переформулировать

🛑 ОСОБЕННО ВАЖНО — НЕ РЕЖЬ ХВОСТ СЛОВА РАДИ ЛИМИТА СИМВОЛОВ:
- ВЫРАЖЕНИЯ ВНУТРИ title/title_main/title_accent — это часть фразы,
  продолжающей kicker и body. Если title продолжает мысль «который стоил X»,
  то X стоит в наречии или род./вин. падеже («дорого», «миллион», «здоровья»),
  а НЕ в краткой форме прилагательного («дорог» = «он дорог», не «стоил дорог»).
- ПРИМЕР ПРАВИЛЬНО: «КОТОРЫЙ СТОИЛ ДОРОГО»
- ПРИМЕР ОШИБКИ:    «КОТОРЫЙ СТОИЛ ДОРОГ» ← это краткая форма мужского рода,
                     несогласованная с глаголом «стоил».
- Если фраза не лезет в 11 знаков title_main/title_accent — переформулируй
  ВСЮ фразу, НЕ режь хвост слова. «ВОЗВРАЩАЮТСЯ» (12 знаков) → «ВЕРНУТСЯ» (8)
  — норм. «ДОРОГ» вместо «ДОРОГО» — провал, не оптимизация.

═══════════════════════════════════════════════════════════════════════
ЧТО ТЫ ВОЗВРАЩАЕШЬ
═══════════════════════════════════════════════════════════════════════

Один JSON-массив длины N (N задаётся пользователем). Слайд №1 — cover,
слайды №2..N — inner.

ВЫБОР ШАБЛОНА (slide #1 cover):
- "M1" — анонсы событий, открытия сезона, юбилеи, новости (rings-decor)
- "M2" — гайды, TOP-N, разборы, обучающие посты (racing-tape) — самый универсальный
По умолчанию выбирай **M2** для гайдов и обучающего контента.
M6 (outdoor sub-brand) ВЫКЛЮЧЕН в этой версии — НЕ используй.

═══════════════════════════════════════════════════════════════════════
COVER SLIDE (slide 1) — JSON schema
═══════════════════════════════════════════════════════════════════════

ВАЖНО: cover — это hook. Цель — заставить пролистнуть. Должен быть
ИНФОРМАТИВНЫМ: subtitle обещает конкретную ценность, а не общую фразу.
Плохой subtitle: «которые знают все профи» (банально).
Хороший subtitle: «как держать трассу, когда сзади давят на 30 км/ч быстрее» (конкретика, тизер).

{
  "template":     "M2",                              // M1 или M2
  "issue_tag":    "GUIDE №02 · МАЙ 2026",            // mono uppercase, дата опц.
  "kicker":       "GUIDE · ДЛЯ НОВИЧКОВ",            // mono uppercase, тема
  "hero":         "5",                               // ОДНА цифра (1-9) или дата "23/04". Размер шрифта auto. НИКОГДА не используй слово «ТОП» — оно лишнее.
  "hero_word":    "СОВЕТОВ",                         // 1 существительное в род. падеже множ. числе рядом с hero (СОВЕТОВ / ОШИБОК / ПРАВИЛ / ФАКТОВ). НЕ ставь «ТОП» в hero_word — только существительное. NULL если hero самодостаточен.
  "title_main":   "ДЛЯ ПЕРВОГО",                     // главный italic заголовок строка 1
  "title_accent": "ЗАЕЗДА",                          // строка 2, в gradient/orange
  "subtitle":     "которые превратят новичка в призёра за один сезон — без курсов, без зала, без хайпа",  // 1-2 предложения, italic. Конкретика, тизер одного из пунктов, личный опыт. НЕ обобщения.
  "counter":      "01 / 07",                         // ВСЕГДА 01 / N (N = общее число слайдов)
  "handle":       "@livedrive.tmn"                   // ВСЕГДА это
}

═══════════════════════════════════════════════════════════════════════
INNER SLIDE (slides 2..N) — JSON schema
═══════════════════════════════════════════════════════════════════════

{
  "slide_type":  "B",                                // A / B / C — тип тайла (см. ниже)
  "kicker":      "ШАГ 02 · СТАРТ",                   // mono uppercase, нумерация шага/пункта
  "title":       "ИДИ КОРОЧЕ",                       // ALL CAPS, italic, 2-5 слов, мощный тезис пункта
  "accent_word": "КОРОЧЕ",                           // 1 слово из title для подсветки orange (опц., null если нет)
  "body":        "1-2 предложения. Конкретика. Italic. Без воды.",
  "pull_quote":  null,                               // ТОЛЬКО для slide_type C — фраза-ядро 6-12 слов. Для A/B — null.
  "counter":     "02 / 05",                          // 02/N, 03/N, ...
  "handle":      "@livedrive.tmn"
}

ТИПЫ INNER-ТАЙЛА (slide_type) — графическая система:
- "B" — Breakdown (пункт списка). ОСНОВНОЙ тип, большинство слайдов.
  Крупная индекс-цифра пункта + title + body. kicker/title/body заполнены.
- "A" — Statement (тезис). Слайд с одной мощной короткой мыслью: title
  огромный, body — 1 короткое предложение. Для самого сильного/
  контринтуитивного пункта. kicker/title/body заполнены, body КОРОТКИЙ.
- "C" — Quote (цитата-вывод). Слайд-инсайт: одна фраза крупным блоком.
  Заполни pull_quote (6-12 слов — ядро мысли). kicker = null. title/body
  можно null. Хорош для финального содержательного слайда перед CTA.

ПРАВИЛО РИТМА: не делай все слайды одного типа — это усыпляет при свайпе.
Типичная карусель: большинство B, 1-2 типа A (сильные тезисы), опц. 1 тип C
(вывод). CTA-слайд (последний) — всегда slide_type "A".

═══════════════════════════════════════════════════════════════════════
СТРУКТУРА КАРУСЕЛИ (адаптируется под тему)
═══════════════════════════════════════════════════════════════════════

Базовое правило: cover (1) + content (N-2) + CTA (1) = N слайдов.

Для тем «топ-K X», «K ошибок», «K правил», «K фишек» — должно быть
ровно K информативных слайдов между cover и CTA. То есть N = K + 2.

Примеры:
  «топ-5 советов»     → N=7 (cover + 5 совет + CTA)
  «3 ошибки старта»   → N=5 (cover + 3 ошибки + CTA)
  «10 фактов о»       → N=12 (cover + 10 фактов + CTA, но это потолок Telegram)

Если в теме нет конкретного K — делай N равным тому, что просит юзер.
ЕСЛИ В ТЕМЕ ЕСТЬ ЧИСЛО K — приоритет за K+2 (юзер ждёт ровно K пунктов).

Распределение для N=7 (типичный "топ-5"):
Слайд 1 (cover):  хук + обещание ценности + тизер
Слайд 2 (inner):  совет 1 — самый сильный, самый контринтуитивный
Слайд 3 (inner):  совет 2
Слайд 4 (inner):  совет 3
Слайд 5 (inner):  совет 4
Слайд 6 (inner):  совет 5 — финальный пункт, можно с эмоциональным итогом
Слайд 7 (CTA):    "Подпишись на Telegram-канал @yumsunov_realbiz"

Для CTA-слайда:
  slide_type = "A"
  kicker = "ОТ АВТОРА · CTA"
  title = "ХОЧЕШЬ БОЛЬШЕ?"
  accent_word = "БОЛЬШЕ"
  body = "Подпишись на Telegram-канал @yumsunov_realbiz — каждую неделю разборы из реального опыта картинг-центра и глэмпинга."

ВАЖНО: CTA ведёт в TELEGRAM-канал @yumsunov_realbiz (это место где Максим
постит длинные разборы). Карусель публикуется в Instagram @livedrive.tmn,
но в CTA-теле НИКОГДА не зови подписаться на @livedrive.tmn — это та же
страница где юзер уже видит карусель, такой призыв бессмысленен. Footer
handle (поле `handle` в каждом слайде) = «@livedrive.tmn» — это адрес
ПОСТА в Instagram, не призыв.

═══════════════════════════════════════════════════════════════════════
ЖЁСТКИЕ ПРАВИЛА
═══════════════════════════════════════════════════════════════════════

✅ Делай:
- Каждый title — мощный, отрывистый. ALL CAPS. 2-5 слов.
- Каждый body — конкретика, личный опыт, цифры если есть в исходных данных.
- Counter ВСЕГДА в формате "NN / NN" с пробелами вокруг "/".
- Handle ВСЕГДА @livedrive.tmn.
- accent_word — точное слово из title (одно из слов).

❌ НЕ делай:
- Длинные body (>180 знаков). Лучше короче и сильнее.
- Слова-маркеры AI: «давайте», «итак», «таким образом».
- Инфоцыганские формулы: «секрет», «формула», «лайфхак», «7 фишек».
- Слово «ТОП» в hero / hero_word / title_main / title_accent / kicker. Лишнее, не добавляет смысла. «5 СОВЕТОВ» — да. «ТОП 5 СОВЕТОВ» — нет. «5 ОТЛИЧИЙ» — да. «5 ТОП ОТЛИЧИЙ» — нет.
  🛑 КРИТИЧЕСКИ ВАЖНО — конкретный пример ПРОВАЛА:
     ОШИБКА: hero="3", title_main="ТОП", title_accent="ТИПА" → выходит «3 ТОП ТИПА» (слово ТОП). НЕ ДЕЛАЙ ТАК.
     ПРАВИЛЬНО: hero="3", title_main="ТИПА", title_accent="КЛИЕНТОВ" → выходит «3 ТИПА КЛИЕНТОВ» (нет ТОП).
     Если хочешь топ-3 → используй просто число 3 в hero + существительное во множ.числе род.падеже в hero_word или title_main.
- title_main И title_accent — каждое МАКС 11 знаков (включая пробелы). Иначе текст обрежется на cover. «ВОЗВРАЩАЮТСЯ» (12) — обрежется. «ВЕРНУТСЯ» (8) — норм. Длиннее — переформулируй.
- Выдуманные цифры (если нет в исходных данных — не используй).
- M6 шаблон.

═══════════════════════════════════════════════════════════════════════
ФОРМАТ ОТВЕТА
═══════════════════════════════════════════════════════════════════════

ТОЛЬКО JSON-массив. Без markdown ```json. Без объяснений до или после.
Начни с символа [ и заверши символом ].
"""


_TOP_STRIP_RE = re.compile(
    r"\bтоп[\s\-]*", re.IGNORECASE,
)


def _strip_top_word(slides: list[dict]) -> list[dict]:
    """Remove «ТОП» from cover hero/hero_word/title/kicker fields.

    Opus иногда игнорит prompt-ban на слово «ТОП» — этот пост-процесс
    гарантированно стрипает его как substring. Работает по всем слайдам
    (cover + inner), потому что Opus может вставить его и в kicker inner-а.
    """
    fields = ("hero", "hero_word", "title_main", "title_accent",
              "title", "kicker", "accent_word", "subtitle", "body",
              "pull_quote")
    for sl in slides:
        for f in fields:
            v = sl.get(f)
            if isinstance(v, str) and "топ" in v.lower():
                cleaned = _TOP_STRIP_RE.sub("", v).strip()
                # Collapse double spaces
                cleaned = re.sub(r"\s+", " ", cleaned)
                if cleaned != v:
                    logger.info(f"[carousel-llm] stripped 'ТОП' from {f}: {v!r} → {cleaned!r}")
                sl[f] = cleaned
    return slides


def _parse_json_array(raw: str) -> list[dict]:
    """Tolerant parser — strips markdown wrappers, locates [...] anywhere."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*\n?", "", s)
    s = re.sub(r"\n?\s*```$", "", s)
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j < 0 or j <= i:
        raise ValueError(f"no JSON array in LLM response (first 200 chars): {raw[:200]!r}")
    arr = json.loads(s[i:j + 1])
    if not isinstance(arr, list):
        raise ValueError(f"parsed value is not a list: {type(arr).__name__}")
    return arr


def _validate_slides(slides: list[dict], n: int) -> list[dict]:
    """Sanity checks. Raises ValueError on hard problems, warns on soft.

    HARD: len(slides) must equal n (Opus иногда выдаёт 6 при n=7 — ловим).
    SOFT: title length cap to fit cover layout (12 chars per fragment).
    """
    if not slides:
        raise ValueError("empty slides list")
    if len(slides) != n:
        raise ValueError(
            f"slide count mismatch: expected {n}, got {len(slides)}"
        )

    # Cover validation
    cover = slides[0]
    cover_required = ("template", "kicker", "hero", "title_main", "title_accent",
                      "subtitle", "counter", "handle")
    cover_missing = [f for f in cover_required if not str(cover.get(f, "")).strip()]
    if cover_missing:
        raise ValueError(f"cover missing fields: {cover_missing}")
    if cover["template"] not in ("M1", "M2"):
        raise ValueError(f"cover template must be M1/M2, got {cover['template']!r}")
    cover.setdefault("issue_tag", cover["kicker"])
    cover.setdefault("hero_word", None)

    # Cover title length sanity (each fragment <= 11 chars to fit cover layout
    # at 108px italic 900 — широкая кириллица). Опытно установленное число.
    for fld in ("title_main", "title_accent"):
        v = cover.get(fld) or ""
        if len(v) > 11:
            logger.warning(
                f"[carousel-llm] cover {fld} too long ({len(v)} chars): {v!r}, "
                f"will truncate to 11"
            )
            cover[fld] = v[:11].rstrip()

    # Inner validation
    for i, sl in enumerate(slides[1:], start=2):
        inner_required = ("kicker", "title", "body", "counter")
        miss = [f for f in inner_required if not str(sl.get(f, "")).strip()]
        if miss:
            raise ValueError(f"inner slide #{i} missing: {miss}")
        sl.setdefault("handle", "@livedrive.tmn")
        sl.setdefault("accent_word", None)

    return slides


_TOPK_RE = re.compile(
    # Matches: "топ-5", "топ 5", "5 советов", "5 ошибок", "3 правила"
    # First group: "топ-K" form. Second group: "K + noun" form.
    r"(?:топ[\s\-]*(\d{1,2}))|(?:^|\s)(\d{1,2})\s+(совет|ошиб|правил|причин|шаг|пункт|приём|приим|секрет|фишк|факт|способ|варианта?|лайфхак)",
    re.IGNORECASE,
)


def infer_n_slides(theme: str, fallback: int = 7) -> int:
    """Parse «топ-K» / «K советов» / «K ошибок» from theme → N = K+2.

    К должно быть в диапазоне 1..10 (карусель Telegram ≤ 10 PNG).
    Возвращает fallback если K не найдено.
    """
    m = _TOPK_RE.search(theme)
    if not m:
        return fallback
    k_str = m.group(1) or m.group(2)
    try:
        k = int(k_str)
    except (TypeError, ValueError):
        return fallback
    n = k + 2  # cover + K + CTA
    return max(3, min(10, n))


def generate_carousel(
    claude,                # anthropic.Anthropic instance
    theme: str,
    n_slides: int | None = None,    # None → infer from theme («топ-K» → K+2, иначе 7)
    model: str = "claude-opus-4-7",   # Opus для творческих сценариев — Sonnet проседает
    max_tokens: int = 5000,
    template: str | None = None,     # "M1" / "M2" / None (let LLM pick)
) -> list[dict]:
    """Generate carousel content for given theme.

    Args:
        claude: Anthropic client (already constructed in bot.py)
        theme: free-form theme description in Russian
               (e.g. "топ-5 фишек для первого заезда на картинге")
        n_slides: total slides including cover and CTA (default 5)
        model: Anthropic model id

    Returns:
        list of `n_slides` dicts: [cover, inner_2, inner_3, ..., cta]
        Cover matches renderer.TEMPLATE_RENDERERS schema, inner slides
        match renderer.render_inner schema.

    Raises:
        ValueError: bad LLM response after retry.
    """
    if n_slides is None:
        n_slides = infer_n_slides(theme, fallback=7)
        logger.info(f"[carousel-llm] inferred n_slides={n_slides} from theme: {theme[:80]!r}")
    if n_slides < 3:
        raise ValueError(f"n_slides must be >=3 (cover + content + CTA), got {n_slides}")
    if n_slides > 10:
        raise ValueError(f"n_slides must be <=10 (Telegram media_group limit), got {n_slides}")

    k_content = n_slides - 2   # inner content slides between cover and CTA
    if template in ("M1", "M2"):
        tpl_clause = f"  slide 1     = cover в шаблоне {template} (юзер ВЫБРАЛ {template} — НЕ меняй на другой)\n"
    else:
        tpl_clause = f"  slide 1     = cover (M2 шаблон по умолчанию, с информативным subtitle)\n"
    user_msg = (
        f"Тема карусели: «{theme}»\n"
        f"Количество слайдов: {n_slides} "
        f"(cover + {k_content} информативных + CTA)\n\n"
        f"Структура:\n"
        f"{tpl_clause}"
        f"  slides 2..{n_slides - 1} = inner-пункты ({k_content} штук, каждый — отдельный совет/ошибка/пункт)\n"
        f"  slide {n_slides}     = CTA (подпиши @livedrive.tmn)\n\n"
        f"Cover должен быть ИНФОРМАТИВНЫМ: subtitle = конкретный тизер, "
        f"а не общая фраза. hero = число {k_content} (как цифра).\n\n"
        f"⚠️ ANTI-HALLUCINATION REMINDER: тема дана в одну строку без личных историй. "
        f"Это значит — НЕ выдумывай биографические детали Максима. "
        f"Делай обобщённые принципы, которые верны без специфики. "
        f"Никаких «потерял миллионы», «учили на курсах», «помню как ехал».\n\n"
        f"Counter везде в формате 'NN / {n_slides:02d}'.\n"
        f"Верни ТОЛЬКО JSON-массив из {n_slides} элементов, без markdown."
    )

    def _one_call() -> list[dict]:
        try:
            resp = claude.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text if resp.content else ""
            logger.info(f"[carousel-llm] raw response ({len(raw)} chars)")
        except Exception as e:
            raise ValueError(f"Anthropic call failed: {type(e).__name__}: {e}") from e
        parsed = _parse_json_array(raw)
        return _validate_slides(parsed, n_slides)

    # F7 fix (26 May 2026, ChatGPT review M4): unified retry loop.
    # Раньше count-mismatch retry и TOP-poisoning retry были независимы — в
    # худшем случае получалось 3 Opus-вызова. Теперь один bounded loop:
    # max_attempts=2 на любую причину. После — deterministic repair fallback.
    MAX_ATTEMPTS = 2
    slides = None
    last_reason = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            slides = _one_call()
        except ValueError as e:
            if "slide count mismatch" in str(e):
                last_reason = f"count_mismatch ({e})"
                logger.warning(f"[carousel-llm] attempt #{attempt}: {last_reason}")
                continue
            raise
        slides = _strip_top_word(slides)
        if _cover_has_empty_critical_fields(slides[0]):
            last_reason = "cover_poisoned (TOP stripped → empty title)"
            logger.warning(f"[carousel-llm] attempt #{attempt}: {last_reason}")
            continue
        # Успех — выходим раньше срока.
        break
    else:
        # Loop отработал MAX_ATTEMPTS без break (slides проблемный) —
        # deterministic repair чтобы рендер не падал. Юзер сможет поправить
        # точечной правкой.
        logger.warning(
            f"[carousel-llm] all {MAX_ATTEMPTS} attempts failed "
            f"(last: {last_reason}), applying deterministic repair"
        )
        if slides is not None:
            cover = slides[0]
            if not (cover.get("title_main") or "").strip():
                cover["title_main"] = "ВЫВОДЫ"
            if not (cover.get("title_accent") or "").strip():
                cover["title_accent"] = "ДНЯ"

    return slides


# ═══════════════════════════════════════════════════════════════════════
# Surgical edit — точечная правка готового сценария (Sonnet, не Opus)
# ═══════════════════════════════════════════════════════════════════════
# Паттерн скопирован с bot.py:_apply_tgpost_surg_edit. Цель — менять ТОЛЬКО
# то, что просит инструкция, не перегенеривая всю карусель. «Переписать
# полностью» (Opus regenerate) — отдельная кнопка.

_SURG_EDIT_SYSTEM = """Ты — редактор карусельных сценариев Максима Юмсунова \
(предприниматель, Life Drive — картинг + глэмпинг, Тюмень).

Тебе дают:
1. Текущий JSON-массив слайдов карусели
2. Инструкцию пользователя — что поменять (живая русская речь)

Задача — внести ТОЧЕЧНУЮ правку строго по инструкции, сохранив всё остальное
БАЙТ-В-БАЙТ.

ПРАВИЛА:
1. НЕ переписывай всю карусель. Меняй только то, что просит инструкция.
2. Количество слайдов НЕ меняется. Схема каждого слайда (набор ключей) сохраняется.
3. «поменяй заголовок 3-го слайда» → правь только title слайда №3.
4. «убери цифру с обложки» → правь только hero / hero_word слайда №1.
5. «исправь грамматику в слайде 4» → правь только тот слайд.
6. «поменяй CTA» / «поправь последний слайд» → правь последний слайд.
7. «сделай тезис мягче / жёстче» → меняй формулировку, не смысл.
8. Поля counter, handle, template — НЕ трогай, если инструкция не просит явно.
9. accent_word должен оставаться словом ИЗ title (если меняешь title — синхронизируй).
10. title_main и title_accent на cover — каждое МАКС 11 знаков.

🎯 REPLACE-ЗАПРОСЫ — самый частый тип инструкции. Формат:
  «<X> поменяй на <Y>», «замени <X> на <Y>», «<X> → <Y>».

ОБРАБОТКА REPLACE:
1. Найди подстроку <X> в любом поле любого слайда (title, kicker, body,
   title_main, title_accent, hero, hero_word, subtitle, pull_quote).
2. ⚠️ ПРИ ПОИСКЕ нормализуй пробелы: двойные/тройные пробелы в <X> или
   в поле слайда считай эквивалентом одинарного. Пример: <X> = «1 СОТРУДНИК
   КОТОРЫЙ» (с одним пробелом) ДОЛЖЕН совпасть с полем «1 СОТРУДНИК  КОТОРЫЙ»
   (с двумя пробелами).
3. Если нашёл — замени именно эту подстроку на <Y>, остальное сохрани.
4. Если <X> НЕ найдено даже после нормализации пробелов — добавь к
   первому слайду поле "_surg_error": "не нашёл: <X>" и верни остальное
   без изменений. Это сигнал пользователю переформулировать.
5. ОБЯЗАТЕЛЬНО: при успехе хоть ОДНО поле в JSON должно отличаться
   от исходного. Если ты возвращаешь идентичный JSON — это сигнал
   что ты не понял инструкцию (см. правило 4).

ЗАПРЕЩЕНО:
- Объяснять что ты изменил. Только JSON.
- Слово «ТОП» в любом поле.
- Выдумывать факты, цифры, истории Максима, которых нет в текущем тексте.
- Менять слайды, которых инструкция не касается.
- Усекать слово ради лимита знаков («ДОРОГ» вместо «ДОРОГО» — провал).

ФОРМАТ ОТВЕТА: ТОЛЬКО JSON-массив той же длины. Без markdown ```, без текста до/после.
Начни с [ и заверши ]."""


def _cover_has_empty_critical_fields(cover: dict) -> bool:
    """Cover «отравлен» если ХОТЬ ОДНО критическое поле пусто после strip.

    F5 fix (ChatGPT review M5): раньше срабатывало только когда ОБА title_*
    пусты. Если Opus засунул «ТОП» только в title_main → strip опустошил
    его → title_accent остался нормальным → detect не сработал → юзер
    видит cover с пустой половиной заголовка. Теперь любое пустое из двух
    critical fields = poisoned, идём в retry или fallback.
    """
    for k in ("title_main", "title_accent"):
        if not (cover.get(k) or "").strip():
            return True
    return False


_WS_RE = re.compile(r"\s+")


def _normalize_value(v):
    """Normalize a slide-field value for tolerant comparison.

    Multiple whitespaces (incl. \\u00A0 NBSP) → single space; trim ends.
    Не-строки оставляем как есть (числа, None, dict).
    """
    if isinstance(v, str):
        return _WS_RE.sub(" ", v.replace(" ", " ")).strip()
    return v


# F3 fix (ChatGPT review C2): служебные ключи которые модель может вернуть
# как часть slide-объекта, но которые НЕ должны влиять на equality.
# `_surg_error` — failure-сигнал от Sonnet (см. _SURG_EDIT_SYSTEM правило 4).
_IGNORED_SLIDE_KEYS = frozenset({"_surg_error", "_debug", "_meta"})


def _slides_equal_normalized(a: list[dict], b: list[dict]) -> bool:
    """Tolerant equality for slide lists.

    Считаем слайды равными если все поля (кроме `_IGNORED_SLIDE_KEYS`)
    идентичны ПОСЛЕ нормализации пробелов в строковых значениях.
    Используется для детекта no-op после surgical edit.
    """
    if len(a) != len(b):
        return False
    for sl_a, sl_b in zip(a, b):
        keys_a = {k for k in sl_a.keys() if k not in _IGNORED_SLIDE_KEYS}
        keys_b = {k for k in sl_b.keys() if k not in _IGNORED_SLIDE_KEYS}
        if keys_a != keys_b:
            return False
        for k in keys_a:
            if _normalize_value(sl_a[k]) != _normalize_value(sl_b.get(k)):
                return False
    return True


def _extract_surg_error(slides: list[dict]) -> str | None:
    """Извлечь и УДАЛИТЬ `_surg_error` из slides (модифицирует in-place).

    F2 fix (ChatGPT review C2): Sonnet добавляет `_surg_error` в первый
    слайд когда не нашёл подстроку для replace. Это failure-сигнал —
    обработчик должен показать конкретную причину юзеру и НЕ сохранять
    draft. Здесь достаём error и чистим slides от служебных полей.
    """
    if not slides:
        return None
    err: str | None = None
    for sl in slides:
        v = sl.pop("_surg_error", None)
        if v and not err:
            err = str(v)
        sl.pop("_debug", None)
        sl.pop("_meta", None)
    return err


_REPLACE_PATTERNS = [
    # «X поменяй на Y» / «X замени на Y»
    re.compile(r"^(.+?)\s+(?:поменяй|замени|меняй|сменить)\s+на\s+(.+)$", re.IGNORECASE | re.DOTALL),
    # «замени X на Y» / «поменяй X на Y»
    re.compile(r"^(?:замени|поменяй|меняй)\s+(.+?)\s+на\s+(.+)$", re.IGNORECASE | re.DOTALL),
    # «X → Y» / «X -> Y»
    re.compile(r"^(.+?)\s*(?:→|->)\s*(.+)$", re.DOTALL),
]


def _extract_replace_pattern(instruction: str) -> tuple[str, str] | None:
    """Parse «X поменяй на Y» (и синонимы) → (X, Y) или None.

    Используется на уровне UI/handlers для предупреждения юзера если он
    написал replace-инструкцию, но Sonnet вернул no-op — значит подстроку
    не нашёл. Также инструкция-как-есть передаётся в Sonnet (он сам ищет
    X→Y по промпту), эта функция — для детекта intent в no-op случае.
    """
    if not instruction or not instruction.strip():
        return None
    text = instruction.strip()
    for pat in _REPLACE_PATTERNS:
        m = pat.match(text)
        if m:
            old, new = m.group(1).strip(), m.group(2).strip()
            if old and new:
                return (old, new)
    return None


def surgical_edit_carousel(
    claude,
    slides: list[dict],
    instruction: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 5000,
) -> list[dict]:
    """Apply a localized edit to an existing carousel draft.

    Sonnet receives the full slides JSON + a free-form instruction, returns
    the edited JSON with ONLY the requested change. Slide count is preserved.

    Args:
        claude: Anthropic client
        slides: current carousel draft (list of slide dicts)
        instruction: free-form Russian edit instruction

    Returns:
        edited slides list (same length, validated, «ТОП» stripped)

    Raises:
        ValueError: bad LLM response or count mismatch.
    """
    n = len(slides)
    current_json = json.dumps(slides, ensure_ascii=False, indent=2)
    user_msg = (
        f"ТЕКУЩИЙ JSON КАРУСЕЛИ ({n} слайдов):\n\n{current_json}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ИНСТРУКЦИЯ ПОЛЬЗОВАТЕЛЯ:\n«{instruction}»\n\n"
        f"Верни обновлённый JSON-массив из {n} слайдов. Меняй ТОЛЬКО то, "
        f"что просит инструкция."
    )
    try:
        resp = claude.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SURG_EDIT_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text if resp.content else ""
        logger.info(f"[carousel-surg] raw response ({len(raw)} chars)")
    except Exception as e:
        raise ValueError(f"Sonnet surgical-edit call failed: {type(e).__name__}: {e}") from e

    edited = _parse_json_array(raw)
    edited = _validate_slides(edited, n)
    edited = _strip_top_word(edited)
    return edited
