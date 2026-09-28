#!/usr/bin/env python3
"""T3, applied at IMAGE BUILD time so it survives any container re-create.

nousresearch/hermes-agent's gateway filters agent status callbacks
("Retrying...", "switching to fallback...") for Telegram only. Everything else,
WhatsApp included, is sent to the customer verbatim. This inserts, before the
Telegram check in _prepare_gateway_status_message:

    if _gateway_platform_value(platform) == "whatsapp":
        return None

Guarded: the anchor must occur exactly once inside that function, or the build
fails loudly (an upstream change must be looked at, not silently skipped).
Idempotent: a file that already has the line is left alone.
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/hermes/gateway/run.py"
FUNC = "def _prepare_gateway_status_message("
ANCHOR = 'if _gateway_platform_value(platform) != "telegram":'
PATCH = 'if _gateway_platform_value(platform) == "whatsapp":'

src = open(PATH, encoding="utf-8").read()
start = src.find(FUNC)
if start == -1 or src.count(FUNC) != 1:
    sys.exit("patch_gateway: %s found %d times in %s; refusing to guess" % (FUNC, src.count(FUNC), PATH))
nxt = re.search(r"\n(?:def |class |async def )", src[start + len(FUNC):])
end = start + len(FUNC) + (nxt.start() if nxt else len(src))
body = src[start:end]
if PATCH in body:
    print("patch_gateway: already patched, nothing to do")
    sys.exit(0)
if body.count(ANCHOR) != 1:
    sys.exit("patch_gateway: anchor found %d times in the function; refusing to patch" % body.count(ANCHOR))
line_start = body.rfind("\n", 0, body.find(ANCHOR)) + 1
indent = body[line_start:body.find(ANCHOR)]
insert = "%s%s\n%s    return None\n" % (indent, PATCH, indent)
body = body[:line_start] + insert + body[line_start:]
open(PATH, "w", encoding="utf-8").write(src[:start] + body + src[end:])
print("patch_gateway: WhatsApp status messages are now suppressed")
