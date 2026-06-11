"""One-off script: create guide page for the озвучка video and update dm_keywords."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
from datetime import datetime
import anthropic
from notion_client import Client
import requests

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_GUIDES_DB = os.getenv("NOTION_GUIDES_DB_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

notion = Client(auth=NOTION_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

script_path = os.path.join(os.path.dirname(__file__),
    "projects/3440ef6e_Google Gemini Flash TTS — озвучка с эмоциями/script.txt")
script = open(script_path, encoding="utf-8").read()
title = "Google Gemini Flash TTS — озвучка с эмоциями"

today = datetime.now().strftime("%d.%m.%Y")
system_prompt = f"""Ты — эксперт-аналитик и контент-редактор. По сценарию ролика создай глубокий, ценный гайд для подписчиков.

ФОРМАТ ОТВЕТА — строго JSON-массив блоков Notion. Каждый блок — объект с полями:
- type: "callout_blue", "callout_yellow", "callout_red", "heading", "numbered", "bulleted", "paragraph", "divider"
- text: текст блока (не нужен для divider)
- icon: эмодзи для callout
- bold_prefix: жирный текст в начале (для bulleted, опционально)

СТРУКТУРА ГАЙДА (15-25 блоков):
1. callout_blue с 🎁 — "Гайд для подписчиков. Ключевое слово в комментариях: озвучка"
2. paragraph — вступление: почему AI-озвучка важна, контекст
3. 3-5 секций с heading + paragraph/numbered + callout_yellow
4. Секция "Как применить" — bulleted с bold_prefix
НЕ добавляй блок об авторе — он добавляется автоматически.
Сегодня {today}. НЕ выдумывай цифры — используй только факты из сценария."""

response = claude.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4000,
    system=system_prompt,
    messages=[{"role": "user", "content": f"Сценарий ролика:\n{script}\n\nСоздай гайд."}],
)

raw = response.content[0].text.strip()
if "```" in raw:
    raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
    raw = raw.strip()
blocks_data = json.loads(raw)

# Convert to Notion blocks
children = []
for b in blocks_data:
    btype = b.get("type", "")
    text = b.get("text", "")
    if btype == "divider":
        children.append({"object": "block", "type": "divider", "divider": {}})
    elif btype.startswith("callout"):
        color_map = {"callout_blue": "blue_background", "callout_yellow": "yellow_background", "callout_red": "red_background"}
        children.append({"object": "block", "type": "callout", "callout": {
            "rich_text": [{"text": {"content": text}}],
            "icon": {"emoji": b.get("icon", "💡")},
            "color": color_map.get(btype, "blue_background")
        }})
    elif btype == "heading":
        children.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": text}}]}})
    elif btype == "numbered":
        children.append({"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"text": {"content": text}}]}})
    elif btype == "bulleted":
        rich = []
        if b.get("bold_prefix"):
            rich.append({"text": {"content": b["bold_prefix"]}, "annotations": {"bold": True}})
        rich.append({"text": {"content": text}})
        children.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich}})
    elif btype == "paragraph":
        annotations = {}
        if "Автор" in text or "@" in text:
            annotations = {"italic": True}
        rich = [{"text": {"content": text}}]
        if annotations:
            rich = [{"text": {"content": text}, "annotations": annotations}]
        children.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}})

# Author block
children.append({"object": "block", "type": "divider", "divider": {}})
children.append({"object": "block", "type": "callout", "callout": {
    "rich_text": [
        {"text": {"content": "Об авторе"}, "annotations": {"bold": True}},
        {"text": {"content": "\n\nАртём Панфёров — CEO Postulat AI Studio.\nПубличный эксперимент: строю личный бренд с нуля до 10 694 подписчиков, используя только ИИ.\n\n"}},
        {"text": {"content": "📸 Instagram"}, "annotations": {"bold": True}},
        {"text": {"content": " — "}},
        {"text": {"content": "panferov.ai", "link": {"url": "https://www.instagram.com/panferov.ai"}}},
        {"text": {"content": "\n"}},
        {"text": {"content": "✈️ Telegram"}, "annotations": {"bold": True}},
        {"text": {"content": " — "}},
        {"text": {"content": "@artempanferov_ai", "link": {"url": "https://t.me/artempanferov_ai"}}},
        {"text": {"content": "\n"}},
        {"text": {"content": "▶️ YouTube"}, "annotations": {"bold": True}},
        {"text": {"content": " — "}},
        {"text": {"content": "Артём Панферов | ИИ в работе и жизни", "link": {"url": "https://www.youtube.com/channel/UCun7X9cdVxHfBvW0VHZYpQw"}}},
        {"text": {"content": "\n"}},
        {"text": {"content": "🎵 TikTok"}, "annotations": {"bold": True}},
        {"text": {"content": " — "}},
        {"text": {"content": "panferov.ai", "link": {"url": "https://www.tiktok.com/@panferov.ai"}}},
        {"text": {"content": "\n"}},
        {"text": {"content": "📺 VK"}, "annotations": {"bold": True}},
        {"text": {"content": " — "}},
        {"text": {"content": "vk.ru/pantem", "link": {"url": "https://vk.ru/pantem"}}},
        {"text": {"content": "\n\n🤝 Хотите внедрить ИИ в свой бизнес? Напишите мне — помогу.\n"}},
        {"text": {"content": "postulataistudio.ru", "link": {"url": "https://postulataistudio.ru"}}},
    ],
    "icon": {"emoji": "👤"},
    "color": "gray_background"
}})

page = notion.pages.create(
    parent={"database_id": NOTION_GUIDES_DB},
    properties={"Name": {"title": [{"text": {"content": title}}]}},
    children=children,
)

page_id_clean = page["id"].replace("-", "")
public_url = f"https://difficult-relative-e9b.notion.site/{page_id_clean}"
print(f"GUIDE_URL={public_url}")

# Update dm_keywords.json
kw_file = os.path.join(os.path.dirname(__file__), "dm_keywords.json")
kw_data = json.loads(open(kw_file, encoding="utf-8").read())
card_title = title
reply_text = f"Привет! Вот материалы по теме «{card_title}»:\n\n{public_url}"
kw_data["18084689072370899"] = {
    "keyword": "озвучка",
    "reply_text": reply_text,
    "guide_url": "",
    "created_at": datetime.now().isoformat(),
}
open(kw_file, "w", encoding="utf-8").write(json.dumps(kw_data, ensure_ascii=False, indent=2))
print("dm_keywords.json updated")

# Update Notion card with guide link
card_id = "3440ef6e-5ff6-8100-9ba4-e1a168854127"
headers_n = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
resp = requests.patch(
    f"https://api.notion.com/v1/pages/{card_id}",
    headers=headers_n,
    json={"properties": {"ССылка на материалы": {"rich_text": [
        {"text": {"content": public_url, "link": {"url": public_url}}}
    ]}}},
    timeout=15,
)
print(f"Notion card updated: {resp.status_code}")
