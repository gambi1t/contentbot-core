import os
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()
notion = Client(auth=os.getenv("NOTION_TOKEN"))
DB_ID = os.getenv("NOTION_GUIDES_DB_ID")

children = [
    {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": "Гайд для подписчиков. Ключевое слово в комментариях: сири"}}],
            "icon": {"emoji": "🎁"},
            "color": "blue_background"
        }
    },
    {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"text": {"content": "Claude на iPhone"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Скачай приложение Claude из App Store (бесплатно)"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Зарегистрируйся через Google-аккаунт"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Готово. Спрашивай что угодно: написать письмо, разобрать договор, составить план"}}]}
    },
    {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": "Лайфхак: Добавь Claude в виджеты на домашний экран — открывается в одно касание"}}],
            "icon": {"emoji": "💡"},
            "color": "yellow_background"
        }
    },
    {"object": "block", "type": "divider", "divider": {}},
    {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"text": {"content": "Gemini на iPhone"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Скачай приложение Google Gemini из App Store"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Войди через свой Google-аккаунт"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Gemini уже работает с Google-документами, почтой и календарём — то, что Apple обещает только в iOS 27"}}]}
    },
    {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": "Лайфхак: В Google-приложении нажми на иконку Gemini — он видит всё в твоём Google Workspace"}}],
            "icon": {"emoji": "💡"},
            "color": "yellow_background"
        }
    },
    {"object": "block", "type": "divider", "divider": {}},
    {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"text": {"content": "Быстрый доступ через Siri Shortcuts"}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": "Хочешь вызывать Claude или Gemini голосом? Настрой за 2 минуты:"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Открой приложение Команды (Shortcuts)"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Нажми + затем Добавить действие затем Открыть приложение и выбери Claude или Gemini"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Назови команду, например Мой ассистент"}}]}
    },
    {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": [{"text": {"content": "Скажи Привет Siri мой ассистент — iPhone откроет нужное приложение"}}]}
    },
    {"object": "block", "type": "divider", "divider": {}},
    {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"text": {"content": "Что для чего использовать"}}]}
    },
    {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [
            {"text": {"content": "Claude"}, "annotations": {"bold": True}},
            {"text": {"content": " — длинные тексты, анализ документов, написание писем, стратегия. Лучше понимает контекст и нюансы"}}
        ]}
    },
    {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [
            {"text": {"content": "Gemini"}, "annotations": {"bold": True}},
            {"text": {"content": " — всё что связано с Google: разбор почты, работа с таблицами, поиск в интернете с актуальными данными"}}
        ]}
    },
    {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [
            {"text": {"content": "Вместе"}, "annotations": {"bold": True}},
            {"text": {"content": " — именно так Apple планирует в iOS 27: разные задачи — разные ИИ. Ты можешь делать это уже сейчас"}}
        ]}
    },
    {"object": "block", "type": "divider", "divider": {}},
    {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": "Если ты в России и приложение недоступно — вот видео-инструкция как скачать любой ИИ-инструмент (ссылка на YouTube)"}}],
            "icon": {"emoji": "🇷🇺"},
            "color": "red_background"
        }
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [
            {"text": {"content": "Автор: "}, "annotations": {"italic": True}},
            {"text": {"content": "@panferovai"}, "annotations": {"italic": True, "bold": True}},
            {"text": {"content": " — ИИ в бизнесе и жизни"}, "annotations": {"italic": True}}
        ]}
    },
]

page = notion.pages.create(
    parent={"database_id": DB_ID},
    properties={
        "Name": {"title": [{"text": {"content": "Claude и Gemini на iPhone уже сейчас — гайд за 5 минут"}}]},
    },
    children=children,
)

print(f"Страница создана: {page['url']}")
