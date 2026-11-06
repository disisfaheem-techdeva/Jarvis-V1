"""Each login's own dashboard theme (6 Nov 2026), like Windows > Themes.

The theme is a look only (wallpaper, accent colour, light or dark), so it is
stored on the person (users.ui_theme, db/92), not on the business, and any
signed-in login may change its own. The ids must match THEMES in
web/dashboard.html.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

THEMES = ("business", "bloom-light", "bloom-dark", "glow", "flow", "lagoon", "mist", "violet", "emerald")


def _me(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not uid:
        raise HTTPException(401, "Sign in to keep a theme.")
    return str(uid)


def make_ui_theme_router():
    router = APIRouter()

    @router.get("/me/theme")
    def get_theme(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _me(x_tenant_id, x_role, authorization, td_session)
        # the person's own row, found by the id in their signed session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT ui_theme FROM users WHERE id::text = %s", (uid,))
            r = cur.fetchone()
        return {"theme": (r and r["ui_theme"]) or "business", "themes": list(THEMES)}

    @router.put("/me/theme")
    def set_theme(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _me(x_tenant_id, x_role, authorization, td_session)
        theme = str(body.get("theme") or "")
        if theme not in THEMES:
            raise HTTPException(400, "Unknown theme.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE users SET ui_theme = %s WHERE id::text = %s RETURNING id", (theme, uid))
            if not cur.fetchone():
                raise HTTPException(404, "This login has no saved profile to keep a theme on.")
        return {"theme": theme}

    return router
