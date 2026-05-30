"""C1 fix: System User token (source=system_user) must NOT be refreshed.
Refresh uses user-flow fb_exchange_token which would break a System User token
(~50 days after obtained_at). This test locks the skip.
"""
import os, sys, time
from pathlib import Path
os.environ.setdefault("META_APP_ID", "x"); os.environ.setdefault("META_APP_SECRET", "y")
sys.path.insert(0, str(Path(__file__).parent.parent))
import crosspost

def main() -> int:
    errors = []
    OLD = time.time() - 60 * 86400  # 60 days ago → would trigger refresh

    # 1) System User token: refresh must NOT be called
    crosspost._load_instagram_token = lambda: {
        "source": "system_user", "obtained_at": OLD,
        "page_access_token": "PAGE_TOK", "access_token": "SYS_TOK",
        "ig_user_id": "123",
    }
    def boom(td):
        raise AssertionError("refresh called for system_user token!")
    crosspost._refresh_instagram_token = boom
    tok = crosspost._get_instagram_access_token()
    ok1 = (tok == "PAGE_TOK")
    print(f"  {'OK' if ok1 else 'FAIL'} system_user token returns page token without refresh (got {tok!r})")
    if not ok1: errors.append("system_user not skipped")

    # 2) Legacy user token (no source): refresh IS attempted when old
    crosspost._load_instagram_token = lambda: {
        "obtained_at": OLD, "page_access_token": "OLD_PAGE", "access_token": "OLD_USER",
        "ig_user_id": "123",
    }
    called = {"v": False}
    def fake_refresh(td):
        called["v"] = True
        return {"page_access_token": "NEW_PAGE", "access_token": "NEW_USER", "obtained_at": time.time()}
    crosspost._refresh_instagram_token = fake_refresh
    tok2 = crosspost._get_instagram_access_token()
    ok2 = called["v"] and tok2 == "NEW_PAGE"
    print(f"  {'OK' if ok2 else 'FAIL'} legacy token still refreshes when old (called={called['v']}, got {tok2!r})")
    if not ok2: errors.append("legacy refresh broken")

    print("\n" + ("PASS" if not errors else f"FAIL: {errors}"))
    return 0 if not errors else 1

if __name__ == "__main__":
    sys.exit(main())
