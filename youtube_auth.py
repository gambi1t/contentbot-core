"""
One-time YouTube OAuth authorization script.

How to use:
1. On server: python3 youtube_auth.py
2. On your PC: ssh -L 8080:localhost:8080 root@178.104.133.148
3. Open the URL printed in console in your browser
4. Authorize and allow access
5. Token will be saved automatically to youtube_token.json

After this, the bot can upload YouTube Shorts without re-authorization.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from crosspost import youtube_auth_url, youtube_exchange_code, run_oauth_server

def main():
    url = youtube_auth_url()
    print()
    print("=" * 60)
    print("  YOUTUBE AUTHORIZATION")
    print("=" * 60)
    print()
    print("1. Make sure SSH tunnel is running:")
    print("   ssh -L 8080:localhost:8080 root@178.104.133.148")
    print()
    print("2. Open this URL in your browser:")
    print()
    print(f"   {url}")
    print()
    print("3. Authorize your Google account")
    print()
    print("Waiting for callback...")
    print()

    code = run_oauth_server("youtube")
    print(f"Code received! Exchanging for token...")

    result = youtube_exchange_code(code)
    if result:
        print()
        print("YouTube authorized successfully!")
        print("Token saved to youtube_token.json")
        print("The bot can now upload YouTube Shorts.")
    else:
        print()
        print("Authorization FAILED. Check logs for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
