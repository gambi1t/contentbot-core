# Внешнее ревью: блок «Instagram System User + Comment-to-DM» (maksim-bot, 30 мая 2026)

Документ для внешнего код-ревью. Описывает задачу, архитектурное решение, изменения в коде и открытые вопросы. Внутренний adversarial-ревью уже пройден (см. раздел 5) — нужен второй взгляд.

---

## 1. TL;DR — что сделано

1. Подключили публикацию в Instagram **клиентского** аккаунта (@yumsunov86) через **System User token + Partner sharing**, обойдя сломанный user-OAuth (Meta «Facebook Login is currently unavailable for this app» из-за непройденной Business Verification + `public_profile` на Standard в Live-режиме).
2. Починили **Comment-to-DM воронку** («напиши слово в комментах → получи DM»), которая «то работала, то слетала»: корень — подписка Страницы на webhook-поле `feed` делалась один раз в OAuth и не восстанавливалась. Добавили самовосстановление при старте бота.
3. Внутренний adversarial-ревью нашёл **CRITICAL**: 50-дневный refresh-флоу тихо сломал бы System User token. Исправлено + тест.

---

## 2. Контекст и проблема

- Агентство (одно Meta-приложение `Panferov Content`, ID 921654167162123, тип Business, Live-режим) публикует контент в IG-аккаунты клиентов через свой Telegram-бот (Python, Graph API v21.0).
- Клиент = предприниматель, его личный IG @yumsunov86 (Business-аккаунт, привязан к FB-Странице «Yumsunov Maksim»).
- **Проблема А (подключение):** новый user-OAuth (`facebook.com/v21.0/dialog/oauth`) выдаёт «Login unavailable» — приложение в Live без Business Verification + `public_profile` на Standard. App Review + Business Verification = недели, и из РФ верификация под вопросом.
- **Проблема Б (воронка):** Comment-to-DM нестабильна — подписка Страницы на webhook (`subscribed_apps`, поле `feed`) слетает (пустеет) при перевыдаче токена / смене прав → Meta перестаёт слать comment-вебхуки.

---

## 3. Архитектурное решение

- **Подключение через System User token** (Business Manager агентства), а не user-OAuth: токен генерится в Business Settings без OAuth-диалога → «Login unavailable» не касается. Токен бессрочный.
- **Partner sharing** Страницы клиента: клиент остаётся владельцем своего портфолио/Страницы/IG, делится Страницей с бизнесом агентства как партнёр, выдавая granular-право «Контент». Полный доступ не нужен, ассет не переносится. Масштабируемо: то же для каждого клиента.
- **Самовосстановление подписки** на `feed` при каждом старте бота.

Формат `instagram_token.json`:
```json
{ "access_token": "<system user token>", "page_id": "...", "page_access_token": "...",
  "ig_user_id": "...", "obtained_at": 1780144942, "source": "system_user", "ig_username": "yumsunov86" }
```

---

## 4. Изменения в коде

### 4.1 `crosspost.py` — выбор целевой Страницы по username
Раньше `instagram_exchange_code` брал ПЕРВУЮ Страницу с привязанным IG (баг: у клиента может быть несколько — картинг + личный бренд). Теперь:
```python
INSTAGRAM_TARGET_USERNAME = os.getenv("INSTAGRAM_TARGET_USERNAME", "").strip().lstrip("@").lower()
```
В Step 4 собираются ВСЕ Страницы с IG (id+username), выбирается по совпадению с `INSTAGRAM_TARGET_USERNAME`; если не задан — первая (старое поведение, регресс отсутствует). Если задан и не найден → `return None` с логом.

### 4.2 `crosspost.py` — самовосстановление подписки `ensure_page_subscribed()`
```python
def ensure_page_subscribed() -> bool:
    token_data = _load_instagram_token()
    if not token_data: return False
    page_id = token_data.get("page_id")
    page_token = token_data.get("page_access_token") or token_data.get("access_token")
    if not page_id or not page_token: return False
    try:
        cur = requests.get(f"https://graph.facebook.com/v21.0/{page_id}/subscribed_apps",
                           params={"access_token": page_token}, timeout=15)
        already = False
        if cur.status_code == 200:
            for app in cur.json().get("data", []):
                if "feed" in (app.get("subscribed_fields") or []):
                    already = True; break
        if already: return True
        sub = requests.post(f"https://graph.facebook.com/v21.0/{page_id}/subscribed_apps",
                            data={"subscribed_fields": "feed", "access_token": page_token}, timeout=15)
        if sub.status_code == 200 and sub.json().get("success"):
            return True
        logger.warning(f"[ig-subscribe] не удалось подписаться на feed: {sub.status_code} {sub.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"[ig-subscribe] ошибка: {e}"); return False
```

### 4.3 `instagram_dm.py` — вызов при старте webhook-сервера
```python
# в start_webhook_server, после site.start():
try:
    import asyncio as _asyncio
    from crosspost import ensure_page_subscribed
    await _asyncio.to_thread(ensure_page_subscribed)
except Exception as e:
    logger.warning(f"[ig-subscribe] вызов при старте не удался: {e}")
```

### 4.4 `crosspost.py` — ФИКС CRITICAL C1: System User token не рефрешится
Раньше `_get_instagram_access_token` через 50 дней безусловно звал `_refresh_instagram_token` (user-флоу `fb_exchange_token`), что сломало бы System User token. Теперь:
```python
def _get_instagram_access_token() -> str | None:
    token_data = _load_instagram_token()
    if not token_data: return None
    # System User page-токен бессрочный — НЕ рефрешим (баг C1)
    if token_data.get("source") == "system_user":
        return token_data.get("page_access_token") or token_data.get("access_token")
    # ... (далее старая логика refresh для user-токенов)

def _refresh_instagram_token(token_data: dict) -> dict | None:
    if token_data.get("source") == "system_user":
        return token_data   # страховка
    # ... + при успешном refresh теперь сохраняются source/ig_username
```

---

## 5. Что уже проверено (внутренний adversarial-ревью)

- **C1 (CRITICAL):** System User token отвалился бы ~через 50 дней из-за refresh. **Исправлено + unit-тест** (`tests/test_system_user_token_no_refresh.py`, 2/2 GREEN: system_user пропускает refresh, legacy — рефрешится).
- **M3:** `_refresh_instagram_token` терял `source`/`ig_username` — исправлено (переносятся все метаполя).
- **L1:** старый OAuth-флоу не сломан (при пустом `INSTAGRAM_TARGET_USERNAME` берётся первая Страница).
- **Проверено вживую:** IG-аккаунт доступен (GET 200, 183 поста), publish limit 200, токен валиден; воронка content-bot реанимирована (feed-подписка восстановлена POST'ом).

---

## 6. Открытые вопросы — НА ЧТО ПРОСИМ ОБРАТИТЬ ВНИМАНИЕ РЕВЬЮЕРА

1. **H2 (App-level webhook):** `ensure_page_subscribed` восстанавливает только Page-level `subscribed_apps` (`feed`). Для **Instagram**-комментариев Meta доставляет события через App-level webhook object=`instagram`, field=`comments`. Достаточно ли Page `feed` для IG-комментариев, или нужна отдельная проверка/подписка App-level webhook? Это может быть второй половиной корня «слетает».
2. **H1 (messaging-поля):** OAuth-флоу подписывал `feed,messages,messaging_postbacks,messaging_referrals`, а `ensure_page_subscribed` — только `feed`. Если воронка использует входящие IG `messages`/`referrals` вебхуки — они не восстановятся. Достаточно ли `feed` для модели «комментарий → приватный ответ + DM»?
3. **M2 (права партнёрского токена):** System User получил Страницу через Partner sharing с правом «Контент». Хватает ли этого токена на `subscribed_apps` (нужен `pages_manage_metadata`) и на `/media_publish`? Сейчас при нехватке прав подписка молча не восстанавливается (только лог-warning, без алерта).
4. **Надёжность System User + Partner модели:** нет ли подводных камней при отзыве партнёрского доступа клиентом, истечении/ротации System User, лимитах Graph API.
5. **Безопасность:** System User token (Admin) лежит в `instagram_token.json` на сервере. Приемлемо ли, или нужен vault/шифрование?

---

## 7. Конкретные вопросы

- Корректна ли модель **System User + Partner sharing** для агентства, постящего в IG множества клиентов, или есть более правильный паттерн (Tech Provider App Review + OAuth-онбординг по ссылке)?
- Что лучшая практика для **самовосстановления webhook-подписки** — на старте бота (как сейчас), по cron, или реактивно по приходу ошибки?
- Стоит ли мигрировать на **Instagram API with Instagram Login** (новый флоу без FB-Страницы), или FB-Login + Graph API остаётся предпочтительным для агентской модели?
