"""T10 (TechDeva): a failure reply never reaches a WhatsApp customer.

When every Gemini model fails (free-tier quota, 429 RESOURCE_EXHAUSTED, an
outage), the Hermes gateway still produces a final "reply": its own error
templates ("Sorry, I encountered an error (...)", "The request failed: ...",
"⚠️ Processing stopped ...") or the provider's raw error text. Upstream only
cleans these up for Telegram; on WhatsApp they were sent to the customer as
if the business had written them (reported by Faheem/Rafay, 26 Oct 2026).

patch_final_reply.py hooks td_handle_failure_reply() into
BasePlatformAdapter right after the handler returns, which is the one point
every reply passes through (normal turns, the empty-response normaliser and
the exception handler alike). For WhatsApp:

  * a failure reply is dropped: the customer receives nothing;
  * the owner's own chat (WHATSAPP_HOME_CHANNEL, already set per agent for T4)
    gets a short alert naming the customer and their message, so a person
    can answer. Alerts are throttled: one per customer per 10 minutes and at
    most 20 an hour, so a long outage cannot flood the owner.

Any other reply is returned unchanged. If this module itself fails, the hook
keeps the reply (fail-open for real answers); classify() is pure and covered
by the self-test that runs at image build.
"""
import logging
import os
import re
import time

logger = logging.getLogger("gateway.td_final_reply")

# Replies the gateway itself writes when a turn failed (gateway/run.py). They
# are fixed strings, so they are matched by their opening words.
GATEWAY_FAILURE_PREFIXES = (
    "Sorry, I encountered an error (",
    "The request failed:",
    "⚠️ Processing completed but no response was generated",
    "⚠️ Processing stopped:",
    "⚠️ The model returned no response after processing tool",
    "⚠️ Session too large for the model's context window",
    "⚠️ Provider authentication failed",
    "⚠️ The model provider",
    "⏱️ The model provider is rate-limiting",
)

# Provider error envelopes, matched only at the start of a short reply (the
# same rule upstream uses for Telegram, see _looks_like_gateway_provider_error).
PROVIDER_ERROR_START = re.compile(
    r"^\s*(\W*\s*)?("
    r"api\s+(?:call\s+)?failed"
    r"|provider\s+authentication\s+failed"
    r"|non-retryable\s+error"
    r"|rate\s+limited\s+after\s+\d+\s+retries"
    r"|error\s+code\s*:"
    r"|http\s*\d{3}\b"
    r"|incorrect\s+api\s+key"
    r"|invalid\s+api\s+key"
    r")",
    re.IGNORECASE,
)

# Markers no sales reply ever contains: raw Gemini/Google error text.
PROVIDER_ERROR_ANYWHERE = re.compile(
    r"resource_exhausted"
    r"|you\s+exceeded\s+your\s+current\s+quota"
    r"|generativelanguage\.googleapis\.com"
    r"|geminiapierror"
    r"|gemini\s+http\s+\d{3}",
    re.IGNORECASE,
)

ALERT_PER_CHAT_SECONDS = 600
ALERTS_PER_HOUR = 20

_last_alert_by_chat = {}
_alert_times = []


def classify(text):
    """Return why `text` is a failure reply, or None for a normal reply."""
    if text is None:
        return None
    body = str(text).strip()
    if not body:
        return None
    if body == "(empty)":
        return "empty-sentinel"
    for prefix in GATEWAY_FAILURE_PREFIXES:
        if body.startswith(prefix):
            return "gateway-template"
    short = len(body) <= 600 and body.count("\n") <= 6
    if short and PROVIDER_ERROR_ANYWHERE.search(body):
        return "provider-error"
    if len(body) <= 400 and body.count("\n") <= 4 and PROVIDER_ERROR_START.match(body):
        return "provider-error"
    return None


def _customer_label(source):
    name = (getattr(source, "user_name", None) or getattr(source, "chat_name", None) or "").strip()
    chat = str(getattr(source, "chat_id", "") or "")
    local, _, domain = chat.partition("@")
    # A phone-number JID shows the number; a LID or group id is shown as-is.
    number = ("+" + local) if domain == "s.whatsapp.net" and local.isdigit() else chat
    return "%s (%s)" % (name, number) if name else number


def _may_alert(chat_id, now):
    """Throttle: one alert per customer per 10 min, at most 20 per hour."""
    if now - _last_alert_by_chat.get(chat_id, 0) < ALERT_PER_CHAT_SECONDS:
        return False
    _alert_times[:] = [t for t in _alert_times if now - t < 3600]
    if len(_alert_times) >= ALERTS_PER_HOUR:
        return False
    _last_alert_by_chat[chat_id] = now
    _alert_times.append(now)
    return True


def build_alert(event):
    source = getattr(event, "source", None)
    said = re.sub(r"\s+", " ", str(getattr(event, "text", "") or "")).strip()
    if len(said) > 160:
        said = said[:157] + "..."
    return (
        "⚠️ TechDeva alert: AI abhi jawab nahi de saka (model ya quota ka masla), "
        "is liye customer ko kuch nahi bheja gaya.\n"
        "Customer: %s\n"
        "Message: \"%s\"\n"
        "Please is customer ko khud jawab de dein."
    ) % (_customer_label(source), said or "(attachment)")


async def _alert_owner(adapter, event):
    home = os.getenv("WHATSAPP_HOME_CHANNEL", "").strip()
    if not home:
        logger.warning("[TD] no WHATSAPP_HOME_CHANNEL: owner not alerted")
        return
    chat_id = str(getattr(getattr(event, "source", None), "chat_id", "") or "")
    if not _may_alert(chat_id, time.time()):
        logger.info("[TD] owner alert throttled for chat %s", chat_id)
        return
    result = await adapter.send(home, build_alert(event))
    if getattr(result, "success", True) is False:
        logger.warning("[TD] owner alert not delivered: %s", getattr(result, "error", "unknown"))
    else:
        logger.info("[TD] owner alerted about chat %s", chat_id)


async def td_handle_failure_reply(adapter, event, response):
    """Return the reply to send: unchanged, or None when it is a failure reply."""
    kind = classify(response)
    if not kind:
        return response
    chat_id = getattr(getattr(event, "source", None), "chat_id", "?")
    logger.warning(
        "[TD] failure reply suppressed (%s, %d chars) for chat %s; the customer gets nothing",
        kind, len(str(response)), chat_id,
    )
    try:
        await _alert_owner(adapter, event)
    except Exception:  # noqa: BLE001 -- the alert must never resurrect the reply
        logger.exception("[TD] owner alert failed")
    return None


def _self_test():
    """Run at image build. Fails the build if classification is wrong."""
    failures = [
        "Sorry, I encountered an error (GeminiAPIError).\nGemini HTTP 429\n Try again or use /reset to start a fresh session.",
        "The request failed: Gemini HTTP 429 (RESOURCE_EXHAUSTED): You exceeded your current quota\nTry again or use /reset to start a fresh session.",
        "⚠️ Processing completed but no response was generated. This may be a transient error — try sending your message again.",
        "⚠️ Processing stopped: processing incomplete. Try again.",
        "⚠️ The model returned no response after processing tool results. This can happen with some models — try again or rephrase your question.",
        "⚠️ Session too large for the model's context window.\nUse /compact to compress the conversation, or /reset to start fresh.",
        "API call failed after 3 retries: Gemini HTTP 503",
        "Error code: 429 - {'error': {'status': 'RESOURCE_EXHAUSTED'}}",
        "(empty)",
        "Gemini HTTP 429 (RESOURCE_EXHAUSTED): You exceeded your current quota, please check your plan and billing details.",
    ]
    normal = [
        "Ji bilkul! HydraFacial ka rate 8,000 hai. Aap kis din aana chahengi?",
        "Assalam o alaikum! Main Zara hoon. Aap ko kis service ke baare mein maloomat chahiye?",
        "Sorry, abhi yeh service available nahi hai. Kya main aap ko koi aur option bataun?",
        "Our clinic is open 11am to 7pm. HTTP links: https://example.com",
        "",
        None,
    ]
    bad = [t for t in failures if not classify(t)] + [t for t in normal if classify(t)]
    if bad:
        raise SystemExit("td_final_reply self-test failed on: %r" % (bad,))
    print("td_final_reply self-test: %d failure replies caught, %d normal replies kept" % (len(failures), len(normal)))


if __name__ == "__main__":
    _self_test()
