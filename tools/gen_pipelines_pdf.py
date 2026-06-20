"""Generate a presentation-ready PDF overview of all pipelines in maksim-bot.

Output: D:\\AI\\maksim-bot\\docs\\pipelines_overview.pdf

Layout: one page per pipeline (title, status, numbered steps, deps).
Designed for Артём to show Максиму without scrolling through chat.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Register a Unicode font with Cyrillic support ───────────────────────────
# DejaVuSans is shipped with Python in many distros and supports Cyrillic.
# Fall back through a few common locations.
_FONT_NAME = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"


def _register_fonts() -> None:
    candidates = [
        ("C:/Windows/Fonts/DejaVuSans.ttf", "C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ]
    for regular, bold in candidates:
        if Path(regular).exists():
            try:
                pdfmetrics.registerFont(TTFont(_FONT_NAME, regular))
                pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))
                return
            except Exception:
                continue
    # Last resort — built-in Helvetica won't render Cyrillic but won't crash.
    raise RuntimeError("No Unicode-capable font found for PDF generation.")


_register_fonts()

# ── Styles ──────────────────────────────────────────────────────────────────
_STYLES = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "Title",
    parent=_STYLES["Title"],
    fontName=_FONT_BOLD,
    fontSize=22,
    leading=26,
    spaceAfter=0.3 * cm,
    textColor=colors.HexColor("#1a1a1a"),
)
SUBTITLE = ParagraphStyle(
    "Subtitle",
    parent=_STYLES["Normal"],
    fontName=_FONT_NAME,
    fontSize=13,
    leading=16,
    textColor=colors.HexColor("#666"),
    spaceAfter=0.4 * cm,
)
H1 = ParagraphStyle(
    "H1",
    parent=_STYLES["Heading1"],
    fontName=_FONT_BOLD,
    fontSize=18,
    leading=22,
    spaceAfter=0.2 * cm,
    textColor=colors.HexColor("#1a1a1a"),
)
H2 = ParagraphStyle(
    "H2",
    parent=_STYLES["Heading2"],
    fontName=_FONT_BOLD,
    fontSize=12,
    leading=14,
    spaceBefore=0.3 * cm,
    spaceAfter=0.1 * cm,
    textColor=colors.HexColor("#444"),
)
STEP = ParagraphStyle(
    "Step",
    parent=_STYLES["Normal"],
    fontName=_FONT_NAME,
    fontSize=11,
    leading=15,
    leftIndent=0.4 * cm,
    spaceAfter=0.1 * cm,
    textColor=colors.HexColor("#222"),
)
SMALL = ParagraphStyle(
    "Small",
    parent=_STYLES["Normal"],
    fontName=_FONT_NAME,
    fontSize=9,
    leading=12,
    textColor=colors.HexColor("#777"),
    spaceBefore=0.3 * cm,
)
STATUS_DONE = ParagraphStyle(
    "StatusDone",
    parent=_STYLES["Normal"],
    fontName=_FONT_BOLD,
    fontSize=11,
    textColor=colors.HexColor("#0a8a4a"),
)
STATUS_PARTIAL = ParagraphStyle(
    "StatusPartial",
    parent=_STYLES["Normal"],
    fontName=_FONT_BOLD,
    fontSize=11,
    textColor=colors.HexColor("#c98a00"),
)
STATUS_PLANNED = ParagraphStyle(
    "StatusPlanned",
    parent=_STYLES["Normal"],
    fontName=_FONT_BOLD,
    fontSize=11,
    textColor=colors.HexColor("#a83232"),
)


# ── Content ─────────────────────────────────────────────────────────────────

PIPELINES = [
    {
        "n": 1,
        "title": "Голос → Telegram-пост",
        "status": "done",
        "summary": "Надиктовал — получил готовый пост в канал.",
        "steps": [
            "Максим нажимает «📝 TG-пост» в меню бота.",
            "Выбирает формат: разбор-эссе, тезисный список или короткий тезис.",
            "Бот просит фактуру голосом или текстом.",
            "Голосовое распознаётся через Whisper (Groq, русский, ~2 сек).",
            "Claude Opus 4.7 пишет пост в стиле Максима (промпт под его бренд).",
            "Бот показывает готовый текст. Кнопки: ✅ Опубликовать / 🔄 Перегенерировать / 🎙 Правки / 📥 Сохранить в Notion.",
            "Публикация в канал @yumsunov_realbiz одним кликом.",
        ],
        "tech": "Groq Whisper-v3 + Claude Opus 4.7 + Telegram Bot API. Промпт SYSTEM_PROMPT_MAKSIM в tg_post_writer.py.",
    },
    {
        "n": 2,
        "title": "Селфи → ролик с субтитрами и музыкой",
        "status": "done",
        "summary": "Записал селфи на телефон — получил оформленный ролик с заголовком и автоматической публикацией.",
        "steps": [
            "Максим записывает короткое видео на телефон, отправляет в бот.",
            "Whisper распознаёт речь (с подсказкой брендов Life Drive, картинг, глэмпинг, Тюмень + AI-инструменты).",
            "Бот показывает текст. Можно править пословно — если меняешь слово которого нет в речи, бот предупреждает.",
            "Прожиг субтитров в стиле CapCut (шрифт NT Somic Bold, 90px).",
            "Выбор музыки: 5 категорий (chill / energetic / corporate / cinematic / inspiring) × 35 треков. Reroll или «без музыки».",
            "Выбор обложки: 3 кадра из видео / загрузить свою / выбрать из библиотеки 113 фоток / пропустить.",
            "Claude Opus 4.7 генерит 5 виральных хуков по стилю Максима (open-loop, личный якорь, парадокс).",
            "Notion-карточка + автогенерация TG-поста по транскрипту + автопубликация в канал.",
        ],
        "tech": "Модуль selfie/ (5 файлов, 60 unit-тестов). Telethon E2E на реальном видео. Перенесён из бота Артёма 8 июня 2026.",
    },
    {
        "n": 3,
        "title": "Селфи + B-roll вставки",
        "status": "done",
        "summary": "Селфи + вставки поверх лица: архивные фото/видео, AI-графика, или конкретные кадры из сценария через AI-видео (Kling 3.0). Формат Hormozi-style.",
        "steps": [
            "Шаги 1-3 — как в Пайплайне 2 (запись, распознавание, правка текста).",
            "После прожига субтитров бот спрашивает: «🎬 Добавить B-roll-вставки?» → «✅ Да».",
            "Picker с 7 источниками: 📷 Фото из библиотеки · 🎞 Клипы из библиотеки · 📤 Загрузить своё фото · 📤 Загрузить своё видео · 🎨 Сгенерировать графику (AI) · 🎬 AI-видео по сценарию · 🎞 Графика HyperFrames.",
            "НОВОЕ — конкретный B-roll под тезис: жми «🎬 AI-видео по сценарию» → бот сам пишет киноспромпты из твоего текста и генерит РЕАЛЬНЫЕ кадры по теме через Kling 3.0 Pro (1080p; картинг → реальные карты, не абстрактная графика).",
            "Цена AI-видео ~$0.11/сек — бот покажет экран стоимости ДО генерации, подтверждаешь.",
            "Можно микшировать: до 7 вставок в любой комбинации (AI-видео + архивные клипы + фото). Счётчик и список выбранных видны.",
            "По «Готово» бот собирает финал: вставки крутятся поверх селфи (звук остаётся твой), фото — Ken Burns 2.8 сек как 50/50.",
            "Дальше — музыка, обложка, 5 хуков, Notion, публикация.",
        ],
        "tech": "Picker selfie/broll_picker.py (7 источников). B-roll «🎬 AI-видео по сценарию» = ai_video_broll.py → fal Kling 3.0 Pro (kling-video/v3/pro, ~$0.112/сек, заменил Seedance 20 июня — Seedance давал generic-седаны вместо картов). Сборка video_assembler.assemble_auto_montage(layout='smart').",
    },
    {
        "n": 4,
        "title": "Идея → сценарий → AI-аватар + B-roll",
        "status": "partial",
        "summary": "Полностью «бесконтактный» ролик: бот сам пишет сценарий, озвучивает голосом Максима и говорит лицом аватара Максима.",
        "steps": [
            "Максим выбирает идею из банка (или вводит свою тему).",
            "Claude Opus пишет сценарий 30-45 сек по правилам Максима (FACTS + бренд-позиционирование).",
            "ElevenLabs озвучивает голосом Максима (voice_id, модель eleven_v3, 4 лука).",
            "HeyGen Avatar 3 генерит видео-аватара Максима с этим голосом.",
            "Подобрать B-roll: личный архив или AI-генерация через Remotion-движок (динамическая графика).",
            "Авто-монтаж: аватар-хук 3 сек → B-roll-вставки в split-формате → аватар-CTA 3 сек.",
            "Обложка → музыка → публикация (TG/IG/YT/VK).",
        ],
        "tech": "ElevenLabs voice_id Максима + HeyGen Avatar v3 (4 лука) + Remotion для динамической графики. Звук-проблема: на тестовом прогоне 8 июня музыка не наложилась — пайплайн в работе.",
    },
    {
        "n": 5,
        "title": "Реакция на новость",
        "status": "partial",
        "summary": "Launch Monitor мониторит источники по теме Максима — предлагает свежие темы для быстрого ролика.",
        "steps": [
            "Каждый час бот парсит RSS / Telegram-каналы / VK / TikTok / YouTube.",
            "Claude Sonnet оценивает каждый пост по релевантности (0-10).",
            "Утром в 10:00 MSK Максим получает дайджест с топ-новостями.",
            "Клик на интересную новость — переход в Pipeline 4 с готовой темой.",
        ],
        "tech": "launch_monitor.py — движок готов, работает в @panferovai_contentbot. Источники сейчас Артёмовы (AI-блогеры). Под Максима нужно заменить на 13-17 источников: TG/VK предпринимательство, премиум-туризм, картинг, Тюмень, TikTok RU/EN — 1 сессия настройки.",
    },
    {
        "n": 6,
        "title": "Голос клиента (отзывы → ролик)",
        "status": "planned",
        "summary": "Бот мониторит отзывы Максима на 2GIS / Яндекс / Авито и из самых ярких делает ролики.",
        "steps": [
            "Бот регулярно парсит отзывы клиентов Life Drive на 2GIS / Яндекс / Авито.",
            "Фильтрует по тональности и развёрнутости — выбирает развёрнутые позитивные.",
            "Предлагает Максиму в дайджесте «вот отзыв, сделать ролик?».",
            "Клик — переходит в Pipeline 4 со сценарием на базе отзыва.",
        ],
        "tech": "В коде ещё нет. 1-2 сессии дизайна перед реализацией: какие API, авторизация, фильтрация тональности.",
    },
    {
        "n": 7,
        "title": "Календарь публикаций",
        "status": "partial",
        "summary": "Фиксирует когда и куда опубликовали, видна загрузка каналов на неделю.",
        "steps": [
            "При каждой публикации бот пишет факт в JSON-журнал (дата + платформа).",
            "Команда /calendar 7 показывает неделю одной картинкой.",
            "Видно: понедельник — TG + IG, вторник — пусто, среда — YT + TG, и т.д.",
        ],
        "tech": "Работает: запись фактов и базовая визуализация. Не хватает планировщика для SMM-команды (создать пост-план на неделю вперёд → Notion-база) — 1 сессия.",
    },
    {
        "n": 8,
        "title": "Штаб — еженедельный дашборд",
        "status": "partial",
        "summary": "Снимок подписчиков и роста по всем платформам за неделю — Максиму на стол каждое воскресенье.",
        "steps": [
            "В воскресенье 21:00 MSK бот собирает количество подписчиков по всем каналам.",
            "Рисует PNG-дашборд с графиками роста за неделю.",
            "Сохраняет снимок в Notion-базе «Статистика».",
            "Шлёт Максиму в личку.",
        ],
        "tech": "Работает для Telegram (автофетч). Instagram/YouTube/TikTok/VK сейчас вручную через /update — нужны OAuth-токены каждой соцсети для авто.",
    },
    {
        "n": 9,
        "title": "Кросспостинг — одна публикация на все площадки",
        "status": "done",
        "summary": "Один клик — пост улетает в Telegram, Instagram, YouTube Shorts, VK.",
        "steps": [
            "Готовый ролик/пост попадает на экран публикации.",
            "Чекбоксы: какие платформы публиковать (по умолчанию все подключённые).",
            "Бот загружает медиа на временный хостинг (только для IG — требует public URL).",
            "Параллельная отправка в каждую соцсеть, статус по каждой.",
            "В Notion-карточке появляются ссылки на опубликованные посты.",
        ],
        "tech": "TG/IG/YT работают (Instagram через System User Token + Partner Sharing, без OAuth-диалога клиента). TikTok отложен. VK Clips — токен есть, статус проверяется.",
    },
    {
        "n": 10,
        "title": "B-roll архив + библиотеки",
        "status": "done",
        "summary": "Личный архив видео и фото Максима по сферам бизнеса — основа для B-roll и обложек.",
        "steps": [
            "Архив на сервере: 113 фото (8 категорий — glamping, karting, sup, team, meetings, nature, personal, maksim_self).",
            "80 видео-клипов (glamping 35, karting 15, sup 15, personal 15).",
            "Каждый файл имеет JSON-метаданные: описание, теги, сцена, оценка качества, есть ли Максим в кадре.",
            "Музыкальная библиотека: 35 треков royalty-free в 5 категориях.",
            "Используется автоматически во всех пайплайнах с видео.",
        ],
        "tech": "Сервер /home/maksim-bot/maksim-bot/broll-library/. Пополнение через clips_downloader.py + convert_heic.py + tag_clips.py (Claude vision для авто-тегирования).",
    },
]


def status_paragraph(status: str) -> Paragraph:
    if status == "done":
        return Paragraph("● РАБОТАЕТ", STATUS_DONE)
    if status == "partial":
        return Paragraph("◐ ЧАСТИЧНО", STATUS_PARTIAL)
    return Paragraph("○ В ПЛАНАХ", STATUS_PLANNED)


def build_cover(story: list) -> None:
    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph("Контент-бот Максима", TITLE))
    story.append(Paragraph("Обзор пайплайнов на 20 июня 2026", SUBTITLE))
    story.append(Spacer(1, 1 * cm))

    rows = [
        ["№", "Пайплайн", "Статус"],
    ]
    for p in PIPELINES:
        status_label = {
            "done": "● работает",
            "partial": "◐ частично",
            "planned": "○ в планах",
        }[p["status"]]
        rows.append([str(p["n"]), p["title"], status_label])

    t = Table(rows, colWidths=[1 * cm, 13 * cm, 4 * cm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), _FONT_BOLD, 11),
        ("FONT", (0, 1), (-1, -1), _FONT_NAME, 10),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1a1a1a")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eee")),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#999")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#ddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(
        "● — работает в продакшене Максима. "
        "◐ — движок готов, нужна настройка или отдельная задача. "
        "○ — в плане, кода ещё нет.",
        SMALL,
    ))
    story.append(PageBreak())


def build_pipeline_page(story: list, p: dict) -> None:
    story.append(Paragraph(f"Пайплайн {p['n']}. {p['title']}", H1))
    story.append(status_paragraph(p["status"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(p["summary"], SUBTITLE))

    story.append(Paragraph("Как работает по шагам", H2))
    for i, step in enumerate(p["steps"], 1):
        story.append(Paragraph(f"<b>{i}.</b> {step}", STEP))

    story.append(Paragraph("Технические детали", H2))
    story.append(Paragraph(p["tech"], SMALL))
    story.append(PageBreak())


def main(out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="Контент-бот Максима — обзор пайплайнов",
        author="Контент-студия",
    )

    story: list = []
    build_cover(story)
    for p in PIPELINES:
        build_pipeline_page(story, p)

    doc.build(story)
    return out_path


if __name__ == "__main__":
    import sys

    default = Path(__file__).resolve().parent.parent / "docs" / "pipelines_overview.pdf"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    out = main(target)
    print(f"PDF created: {out}")
    print(f"Size: {out.stat().st_size / 1024:.1f} KB")
