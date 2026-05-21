"""
One-time Instagram authorization script (via Facebook OAuth).

How to use (актуально с 30 апр 2026 — см. reference_oauth_redirect_setup.md):
1. On server: python3 instagram_auth.py
2. URL print'нется в консоли — отправь его Артёму
3. Артём кликает в браузере, авторизуется в Facebook
4. callback приходит на https://maksim-bot.panferov-ai.ru/oauth/callback (nginx
   проксит на 127.0.0.1:8080) — этот listener ловит code и обменивает на token
5. Token + Instagram account ID сохраняется в instagram_token.json

⚠️ SSH-туннель НЕ нужен — всё через публичный nginx-прокси.
Старый IP `178.104.133.148` decommissioned (см. reference_server_access.md).

Requirements:
- Meta Developer App (ID: 921654167162123) с настройками:
  - Facebook Login for Business product enabled
  - Redirect URI: https://maksim-bot.panferov-ai.ru/oauth/callback
  - Permissions: instagram_basic, instagram_content_publish, pages_show_list,
    pages_read_engagement, instagram_manage_comments, instagram_manage_messages
- Instagram Professional account connected to a Facebook Page
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from crosspost import instagram_auth_url, instagram_exchange_code, run_oauth_server


def main():
    url = instagram_auth_url()
    print()
    print("=" * 60)
    print("  INSTAGRAM AUTHORIZATION (via Facebook)")
    print("=" * 60)
    print()
    print("1. Open this URL in your browser:")
    print()
    print(f"   {url}")
    print()
    print("2. Log in with Facebook and authorize the app")
    print("   (select your Facebook Page connected to Instagram)")
    print()
    print("Callback придёт на https://maksim-bot.panferov-ai.ru/oauth/callback")
    print("(nginx проксит на этот listener — SSH-туннель НЕ нужен)")
    print()
    print("Waiting for callback...")
    print()

    code = run_oauth_server("instagram")
    print(f"Code received! Exchanging for token...")

    result = instagram_exchange_code(code)
    if result:
        print()
        print("Instagram authorized successfully!")
        print(f"  Instagram account ID: {result.get('ig_user_id')}")
        print(f"  Facebook Page ID: {result.get('page_id')}")
        print("Token saved to instagram_token.json")
        print("The bot can now publish Instagram Reels.")
    else:
        print()
        print("Authorization FAILED. Check the output above for details.")
        print()
        print("Common issues:")
        print("  - Instagram not connected to a Facebook Page")
        print("  - App doesn't have instagram_content_publish permission")
        print("  - Redirect URI mismatch in Meta Developer settings")
        sys.exit(1)


if __name__ == "__main__":
    main()
