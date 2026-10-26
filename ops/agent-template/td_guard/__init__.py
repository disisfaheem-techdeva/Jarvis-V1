"""TechDeva td_guard (T13): a WhatsApp photo is not answered (Rafay, 26 Oct 2026).

A customer's photo (or any image: a sticker, an image sent as a file) is
dropped at the gateway's pre_gateway_dispatch hook, before authorization,
sessions or the model: the agent does not look at it, describe it or reply,
and no AI request is spent on it. Any caption sent with the photo is dropped
with it. The customer still sees the photo delivered on WhatsApp; the
business sees it on its phone.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _is_image(event) -> bool:
    mtype = getattr(getattr(event, "message_type", None), "value", "") or ""
    if mtype in ("photo", "sticker"):
        return True
    return any(str(t or "").lower().startswith("image/") for t in (getattr(event, "media_types", None) or []))


def _drop_photo(event=None, **_kw):
    source = getattr(event, "source", None)
    if getattr(getattr(source, "platform", None), "value", None) != "whatsapp":
        return None
    if getattr(event, "internal", False) or not _is_image(event):
        return None
    logger.info("[TD] T13: photo from %s not answered", getattr(source, "chat_id", "?"))
    return {"action": "skip", "reason": "TechDeva T13: photos are not answered on WhatsApp"}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _drop_photo)
