import requests, os, json
from dotenv import load_dotenv
load_dotenv()

token = os.getenv('NOTION_TOKEN')
headers = {
    'Authorization': f'Bearer {token}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json'
}

db_id = os.getenv('NOTION_DATABASE_ID')

# Step 1: Create a container page inside the content DB (hidden in Архив/Опубликовано)
page_resp = requests.post('https://api.notion.com/v1/pages', headers=headers, json={
    'parent': {'database_id': db_id},
    'properties': {
        'Name': {'title': [{'text': {'content': '📊 Статистика подписчиков (контейнер)'}}]},
        'Status': {'status': {'name': 'Опубликовано'}}
    }
})

if page_resp.status_code != 200:
    print(f"Page error: {json.dumps(page_resp.json(), ensure_ascii=False, indent=2)}")
    exit(1)

page_id = page_resp.json()['id']
page_url = page_resp.json()['url']
print(f"Container page created: {page_id}")
print(f"URL: {page_url}")

# Step 2: Create stats database inside the page
db_resp = requests.post('https://api.notion.com/v1/databases', headers=headers, json={
    'parent': {'type': 'page_id', 'page_id': page_id},
    'title': [{'type': 'text', 'text': {'content': 'Статистика подписчиков'}}],
    'icon': {'type': 'emoji', 'emoji': '📊'},
    'properties': {
        'Неделя': {'title': {}},
        'Дата': {'date': {}},
        'Instagram': {'number': {'format': 'number'}},
        'Telegram': {'number': {'format': 'number'}},
        'YouTube': {'number': {'format': 'number'}},
        'TikTok': {'number': {'format': 'number'}},
        'VK': {'number': {'format': 'number'}},
        'Max': {'number': {'format': 'number'}},
        'Итого': {
            'formula': {
                'expression': 'prop("Instagram") + prop("Telegram") + prop("YouTube") + prop("TikTok") + prop("VK") + prop("Max")'
            }
        },
    }
})

if db_resp.status_code != 200:
    print(f"DB error: {json.dumps(db_resp.json(), ensure_ascii=False, indent=2)}")
    exit(1)

stats_db_id = db_resp.json()['id']
stats_url = db_resp.json()['url']
print(f"\nStats DB created: {stats_db_id}")
print(f"URL: {stats_url}")

# Save to file for reference
with open('_stats_db_id.txt', 'w') as f:
    f.write(stats_db_id)
print(f"\nAdd to .env: NOTION_STATS_DB={stats_db_id}")
