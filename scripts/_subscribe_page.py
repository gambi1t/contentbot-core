"""Восстановить подписку Страницы на webhook-события (feed = комментарии + messages).
Читает page_access_token + page_id из instagram_token.json в cwd. Токены не печатает.
"""
import json, requests, sys
from pathlib import Path

tf = Path("instagram_token.json")
if not tf.exists():
    sys.exit("FAIL: нет instagram_token.json в текущей папке")
t = json.load(open(tf, encoding="utf-8"))
pid = t["page_id"]; ptoken = t["page_access_token"]
FIELDS = "feed"

# До
before = requests.get(f"https://graph.facebook.com/v21.0/{pid}/subscribed_apps",
                      params={"access_token": ptoken}, timeout=20).json()
print("ДО:", before.get("data"))

# Подписать
r = requests.post(f"https://graph.facebook.com/v21.0/{pid}/subscribed_apps",
                  data={"subscribed_fields": FIELDS, "access_token": ptoken}, timeout=20)
print("POST subscribe:", r.status_code, r.json())

# После
after = requests.get(f"https://graph.facebook.com/v21.0/{pid}/subscribed_apps",
                     params={"access_token": ptoken}, timeout=20).json()
print("ПОСЛЕ:", after.get("data"))
ok = bool(after.get("data"))
print("РЕЗУЛЬТАТ:", "OK — подписка активна" if ok else "FAIL — подписка пуста")
sys.exit(0 if ok else 1)
