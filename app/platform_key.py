"""The platform's default Gemini key (founder console > API Manager > Default key).

Until 26 Oct 2026 the platform's own Gemini key was only GEMINI_API_KEY in the
server .env, so changing it meant editing the server. The founder can now pick
one of the keys saved in the API Manager as the default
(platform_settings ai_default_key). It is used by every agent without a key of
its own and by the platform's own AI (Jarvis, pictures, voice, photo reading).
With no default chosen, or when the chosen key is off, refused or gone, the
.env key is used exactly as before.

The key is read from the database at most once a minute per process.
"""
import os
import time

_CACHE = {"at": 0.0, "value": None}
TTL = 60


def default_key_row(cur):
    """The chosen default key's row, or None (then the .env key is the default)."""
    cur.execute("SELECT value FROM platform_settings WHERE key = 'ai_default_key'")
    r = cur.fetchone()
    if not r or not r["value"]:
        return None
    cur.execute("""SELECT * FROM ai_keys WHERE id::text = %s AND provider = 'gemini'
                     AND status NOT IN ('disabled', 'invalid')""", (r["value"],))
    k = cur.fetchone()
    return dict(k) if k else None


def gemini_key() -> str:
    """The Gemini key the platform should use now."""
    now = time.time()
    if now - _CACHE["at"] < TTL:
        return _CACHE["value"] or (os.environ.get("GEMINI_API_KEY") or "").strip()
    value = None
    try:
        from .db import tenant_session
        from .ai_keys import _open
        with tenant_session(None, "techdeva_admin") as cur:
            row = default_key_row(cur)
        if row:
            value = _open(row)
    except Exception as e:  # noqa: BLE001 -- the .env key keeps the platform working
        print("[platform_key] default key not read, using the .env key: %s" % type(e).__name__, flush=True)
    _CACHE.update(at=now, value=value)
    return value or (os.environ.get("GEMINI_API_KEY") or "").strip()
