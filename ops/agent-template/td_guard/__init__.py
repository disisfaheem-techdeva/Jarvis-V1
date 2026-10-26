"""TechDeva td_guard (T13): what reaches a WhatsApp customer (Rafay, 26 Oct 2026).

In: a customer's photo (or any image: a sticker, an image sent as a file) is
dropped at the gateway's pre_gateway_dispatch hook, before authorization,
sessions or the model: the agent does not look at it, describe it or reply,
and no AI request is spent on it. Any caption sent with the photo is dropped
with it. The business still sees the photo on its phone.

Out: the gateway also sends a few internal lines straight through the
WhatsApp adapter, past the final-reply guard (T10): the raw transcript of a
voice note ('🎙️ "..."'), the speech-to-text setup instructions, and the
background review notes ('💾 ...'). The adapter's send is wrapped once, on the
first message, so those lines are logged and never sent.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lines the gateway itself writes to the chat that a customer must never get.
INTERNAL = ('🎙️ "', "🎤 I received your voice message", "💾 ")


def _is_image(event) -> bool:
    mtype = getattr(getattr(event, "message_type", None), "value", "") or ""
    if mtype in ("photo", "sticker"):
        return True
    return any(str(t or "").lower().startswith("image/") for t in (getattr(event, "media_types", None) or []))


def _wrap_send(gateway) -> None:
    """Wrap the WhatsApp adapter's send once so internal lines are never sent."""
    adapters = getattr(gateway, "adapters", None) or {}
    for platform, adapter in list(adapters.items()):
        if getattr(platform, "value", None) != "whatsapp" or getattr(adapter, "_td_guard_out", False):
            continue
        original = adapter.send

        async def send(chat_id, content, *args, _original=original, **kwargs):
            text = content if isinstance(content, str) else ""
            if text.lstrip().startswith(INTERNAL):
                logger.info("[TD] T13: internal line not sent to %s: %s", chat_id, text[:60].replace("\n", " "))
                from gateway.platforms.base import SendResult
                return SendResult(success=True)
            return await _original(chat_id, content, *args, **kwargs)

        adapter.send = send
        adapter._td_guard_out = True
        logger.info("[TD] T13: WhatsApp outbound filter on")


def _guard(event=None, gateway=None, **_kw):
    source = getattr(event, "source", None)
    if getattr(getattr(source, "platform", None), "value", None) != "whatsapp":
        return None
    try:
        _wrap_send(gateway)
    except Exception:  # noqa: BLE001 -- the photo rule below must still work
        logger.exception("[TD] T13: could not wrap the WhatsApp send")
    if getattr(event, "internal", False) or not _is_image(event):
        return None
    logger.info("[TD] T13: photo from %s not answered", getattr(source, "chat_id", "?"))
    return {"action": "skip", "reason": "TechDeva T13: photos are not answered on WhatsApp"}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _guard)
