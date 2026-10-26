"""TechDeva td_guard (T13/T14): what reaches a WhatsApp customer (Rafay, 26 Oct 2026).

In: a customer's photo (or any image: a sticker, an image sent as a file) is
dropped at the gateway's pre_gateway_dispatch hook, before authorization,
sessions or the model: the agent does not look at it, describe it or reply,
and no AI request is spent on it. Any caption sent with the photo is dropped
with it. The business still sees the photo on its phone.

Out (T14, every agent): the WhatsApp adapter's send is wrapped once, on the
first message, and every text passes through clean_reply() before it leaves:
  * lines the gateway itself writes to the chat are never sent: the raw
    transcript of a voice note ('🎙️ "..."'), the speech-to-text setup text,
    background review notes ('💾 ...') and the slash-command refusals
    ("⛔ /x is admin-only here ... allow_admin_from ...");
  * lines that are the agent's own instructions or its unfinished thinking
    ("Keep every reply short", "Female verb forms? ... -> wait", "[System: ...",
    "<think>", API errors) are cut out of a reply; seen on 26 Oct 2026 when a
    reply was cut short and continued, and both halves were sent joined;
  * if nothing a customer should read is left, nothing is sent.
Each cut is logged (first characters only) so it can be checked later.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Whole messages the gateway itself writes to the chat.
INTERNAL = ('🎙️ "', "🎤 I received your voice message", "💾 ", "⛔ ")

# A line holding any of these is the agent's instructions, its thinking or an
# error, never something a customer should read. Kept narrow on purpose: a
# false match deletes part of a real answer.
LEAK = re.compile(
    r"(?i)("
    r"keep every reply short|end each reply with one|one short follow-up|never ask two questions"
    r"|female verb|verb forms?\b.*\?|verb for self|->\s*wait\b|\bwait,\s*verb"
    r"|use feminine|feminine verb|address the customer as"
    r"|allow_admin_from|user_allowed_commands|admin-only here|slash command"
    r"|techdeva-(voice|knowledge|lessons)|soul\.md|\bhard rule\b|system prompt|\[system:"
    r"|api call failed|resource_exhausted|\bhttp [45]\d\d\b|traceback \(most recent|max_tokens"
    r"|tool_call|function_call|</?think>|as an ai language model"
    r"|the user sent a voice message|here's what they said"
    r")"
)
# A numbered rule copied out of the instructions ("4. Keep every reply ...").
RULE_LINE = re.compile(r"^\s*\d+\.\s+(keep|end|never|always|address|you are|reply|use|do not|don't)\b", re.I)


def clean_reply(text: str):
    """The reply as a customer may see it, or None when nothing of it may be sent."""
    if not isinstance(text, str):
        return text
    stripped = text.lstrip()
    if stripped.startswith(INTERNAL):
        return None
    lines = text.split("\n")
    kept = [l for l in lines if not (LEAK.search(l) or RULE_LINE.match(l))]
    if len(kept) == len(lines):
        return text
    out = "\n".join(kept).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out if re.search(r"\w", out) else None


def _is_image(event) -> bool:
    mtype = getattr(getattr(event, "message_type", None), "value", "") or ""
    if mtype in ("photo", "sticker"):
        return True
    return any(str(t or "").lower().startswith("image/") for t in (getattr(event, "media_types", None) or []))


def _wrap_send(gateway) -> None:
    """Wrap the WhatsApp adapter's send once so every outgoing text is checked."""
    adapters = getattr(gateway, "adapters", None) or {}
    for platform, adapter in list(adapters.items()):
        if getattr(platform, "value", None) != "whatsapp" or getattr(adapter, "_td_guard_out", False):
            continue
        original = adapter.send

        async def send(chat_id, content, *args, _original=original, **kwargs):
            if isinstance(content, str):
                cleaned = clean_reply(content)
                if cleaned is None:
                    logger.info("[TD] T14: not sent to %s: %s", chat_id, content[:80].replace("\n", " "))
                    from gateway.platforms.base import SendResult
                    return SendResult(success=True)
                if cleaned != content:
                    logger.info("[TD] T14: cleaned a reply to %s (%d -> %d chars)", chat_id, len(content), len(cleaned))
                    content = cleaned
            return await _original(chat_id, content, *args, **kwargs)

        adapter.send = send
        adapter._td_guard_out = True
        logger.info("[TD] T14: WhatsApp reply guard on")


def _guard(event=None, gateway=None, **_kw):
    source = getattr(event, "source", None)
    if getattr(getattr(source, "platform", None), "value", None) != "whatsapp":
        return None
    try:
        _wrap_send(gateway)
    except Exception:  # noqa: BLE001 -- the photo rule below must still work
        logger.exception("[TD] T14: could not wrap the WhatsApp send")
    if getattr(event, "internal", False) or not _is_image(event):
        return None
    logger.info("[TD] T13: photo from %s not answered", getattr(source, "chat_id", "?"))
    return {"action": "skip", "reason": "TechDeva T13: photos are not answered on WhatsApp"}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _guard)
