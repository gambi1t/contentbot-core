import json, requests
t = json.load(open("instagram_token.json", encoding="utf-8"))
pt = t["page_access_token"]; ig = t["ig_user_id"]
r = requests.get(f"https://graph.facebook.com/v21.0/{ig}",
                 params={"fields": "username,name,media_count,followers_count", "access_token": pt}, timeout=20)
print("IG account:", r.status_code, r.json())
lim = requests.get(f"https://graph.facebook.com/v21.0/{ig}/content_publishing_limit",
                   params={"access_token": pt}, timeout=20)
print("Publish limit:", lim.status_code, lim.json())
