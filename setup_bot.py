"""
setup_bot.py — pre-flight check перед первым запуском бота Максима.

Запуск:
    cd D:\\AI\\maksim-bot
    python setup_bot.py

Что проверяет:
1. Файл .env существует
2. Минимальные обязательные переменные заполнены
3. Telegram-токен валидный (запрашивает getMe у Bot API)
4. Notion-токен валидный (запрашивает /v1/users/me у Notion API)
5. Notion DB существует и интеграция к ней подключена
6. Anthropic API key валидный (запрашивает /v1/messages с минимальным prompt'ом)
7. Промпт-файлы script_prompt_maksim.txt и cover_prompt_maksim.txt на месте

Если что-то не ОК — печатает что именно и как исправить, без падений.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Force UTF-8 output for Windows cp1251 terminal
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


HERE = Path(__file__).parent
ENV_PATH = HERE / ".env"
TEMPLATE_PATH = HERE / ".env.maksim.template"


# ---- minimal dotenv reader (no external dep) ---------------------------------

def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


# ---- pretty printers ---------------------------------------------------------

OK = "✅"
FAIL = "❌"
WARN = "⚠️"
INFO = "ℹ️"


def section(title: str):
    print()
    print("═" * 60)
    print(f"  {title}")
    print("═" * 60)


# ---- checks ------------------------------------------------------------------

def check_env_file() -> dict[str, str] | None:
    section("1. Файл .env")
    if not ENV_PATH.exists():
        print(f"{FAIL} {ENV_PATH} не найден")
        if TEMPLATE_PATH.exists():
            print(f"{INFO} Скопируй шаблон: cp .env.maksim.template .env")
            print(f"{INFO} Затем заполни TELEGRAM_BOT_TOKEN, NOTION_TOKEN,")
            print(f"   ADMIN_TELEGRAM_IDS, ANTHROPIC_API_KEY")
        return None
    env = _read_env_file(ENV_PATH)
    print(f"{OK} {ENV_PATH} найден ({len(env)} переменных)")
    return env


def check_required(env: dict[str, str]) -> list[str]:
    section("2. Обязательные переменные")
    required = [
        ("TELEGRAM_BOT_TOKEN", "от @BotFather"),
        ("ADMIN_TELEGRAM_IDS", "твой chat_id (узнать у @userinfobot)"),
        ("NOTION_TOKEN", "интеграция в Maksim's Notion workspace"),
        ("ANTHROPIC_API_KEY", "console.anthropic.com или общий с Артёмом"),
        ("DEFAULT_BRAND", "должно быть = maksim"),
        ("NOTION_DATABASE_ID", "должно быть = 3586889c-d6a7-804e-9f0b-e2d58c34e872"),
    ]
    missing = []
    for key, hint in required:
        val = env.get(key, "")
        if not val:
            print(f"{FAIL} {key} пустой — {hint}")
            missing.append(key)
        else:
            preview = val[:8] + "…" if len(val) > 12 else val
            print(f"{OK} {key} = {preview}")
    return missing


def check_telegram_token(env: dict[str, str]) -> bool:
    section("3. Telegram Bot token")
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print(f"{FAIL} TELEGRAM_BOT_TOKEN пустой — нечего проверять")
        return False
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"{FAIL} Telegram API: {e.code} — {e.reason}")
        if e.code == 401:
            print(f"{INFO} Токен невалидный. Проверь что скопировал целиком от @BotFather.")
        return False
    except Exception as e:
        print(f"{FAIL} Сетевая ошибка: {e}")
        return False
    if not data.get("ok"):
        print(f"{FAIL} Telegram отказал: {data}")
        return False
    info = data["result"]
    print(f"{OK} Бот: @{info['username']} (id={info['id']}) — '{info.get('first_name', '')}'")
    return True


def check_notion_token(env: dict[str, str]) -> bool:
    section("4. Notion integration")
    token = env.get("NOTION_TOKEN", "")
    if not token:
        print(f"{FAIL} NOTION_TOKEN пустой")
        return False
    req = urllib.request.Request(
        "https://api.notion.com/v1/users/me",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200] if hasattr(e, "read") else ""
        print(f"{FAIL} Notion API: {e.code} — {body}")
        if e.code == 401:
            print(f"{INFO} Токен невалидный. Получи новый в notion.so/my-integrations")
        return False
    except Exception as e:
        print(f"{FAIL} Сетевая ошибка: {e}")
        return False
    name = data.get("name", "?")
    bot = data.get("bot", {})
    workspace = bot.get("workspace_name", "?") if bot else "?"
    print(f"{OK} Integration: '{name}' в workspace '{workspace}'")
    return True


def check_notion_db(env: dict[str, str]) -> bool:
    section("5. Notion DB подключена к интеграции")
    token = env.get("NOTION_TOKEN", "")
    db_id = env.get("NOTION_DATABASE_ID", "").replace("-", "")
    if not token or not db_id:
        print(f"{WARN} Пропускаем — нет NOTION_TOKEN или NOTION_DATABASE_ID")
        return False
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300] if hasattr(e, "read") else ""
        print(f"{FAIL} Notion DB запрос: {e.code}")
        if e.code == 404:
            print(f"{INFO} База не найдена ИЛИ интеграция не подключена к ней.")
            print(f"{INFO} Открой Content-страницу в Notion → ••• → Connections → Add → выбери свою интеграцию")
        else:
            print(f"   Ответ: {body}")
        return False
    except Exception as e:
        print(f"{FAIL} Сетевая ошибка: {e}")
        return False
    title_arr = data.get("title", [])
    title = title_arr[0].get("plain_text", "?") if title_arr else "(no title)"
    props = list(data.get("properties", {}).keys())
    print(f"{OK} База: '{title}' — {len(props)} свойств")
    expected = {"Name", "Status", "Рубрика", "Бренд", "Площадки", "Призыв"}
    missing = expected - set(props)
    if missing:
        print(f"{WARN} Отсутствуют свойства: {missing}")
        print(f"{INFO} Должны быть созданы при первой накатке схемы (см. project_maksim_notion_content_db.md)")
    return True


def check_anthropic(env: dict[str, str]) -> bool:
    section("6. Anthropic API")
    key = env.get("ANTHROPIC_API_KEY", "")
    if not key:
        print(f"{FAIL} ANTHROPIC_API_KEY пустой")
        return False
    body = json.dumps({
        "model": "claude-haiku-4-5",
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:300] if hasattr(e, "read") else ""
        print(f"{FAIL} Anthropic API: {e.code}")
        if e.code == 401:
            print(f"{INFO} Ключ невалидный. Проверь в console.anthropic.com")
        else:
            print(f"   Ответ: {body_text}")
        return False
    except Exception as e:
        print(f"{FAIL} Сетевая ошибка: {e}")
        return False
    print(f"{OK} Anthropic API отвечает (модель {data.get('model', '?')})")
    return True


def check_prompt_files() -> bool:
    section("7. Промпт-файлы Максима")
    files = ["script_prompt_maksim.txt", "cover_prompt_maksim.txt"]
    all_ok = True
    for fname in files:
        path = HERE / fname
        if not path.exists():
            print(f"{FAIL} {fname} отсутствует")
            all_ok = False
        else:
            size = path.stat().st_size
            print(f"{OK} {fname} ({size} байт)")
    return all_ok


# ---- main --------------------------------------------------------------------

def main() -> int:
    print("Pre-flight check для maksim-bot")
    print(f"Папка: {HERE}")

    env = check_env_file()
    if env is None:
        return 1

    missing = check_required(env)
    tg_ok = check_telegram_token(env)
    notion_ok = check_notion_token(env)
    notion_db_ok = check_notion_db(env) if notion_ok else False
    anthropic_ok = check_anthropic(env)
    prompts_ok = check_prompt_files()

    section("ИТОГ")
    if missing:
        print(f"{FAIL} Незаполненные обязательные переменные: {missing}")
    blockers = [
        ("Telegram", tg_ok),
        ("Notion auth", notion_ok),
        ("Notion DB", notion_db_ok),
        ("Anthropic", anthropic_ok),
        ("Промпт-файлы", prompts_ok),
    ]
    failed = [name for name, ok in blockers if not ok]
    if failed or missing:
        print(f"{FAIL} НЕ готов запускать бот. Исправь: {failed + missing}")
        return 1
    print(f"{OK} Всё ОК — можно запускать: python bot.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
