#!/usr/bin/env python3
"""T11, applied at IMAGE BUILD time: no session-title model calls.

After the first one or two exchanges of every conversation the gateway asks
the model for a short session title (agent/title_generator.py,
maybe_auto_title). A WhatsApp sales agent never shows that title to anyone,
yet on the Gemini free tier it costs one request per new conversation: half of
the requests for a short chat (measured on tech-deva-8e9f58, 26 Oct 2026:
3 new conversations = 6 requests).

This inserts an early return at the top of maybe_auto_title. Titles can be
switched back on for an agent with TD_AUTO_TITLE=1 in its environment.

Guarded: the anchor must occur exactly once inside the function, or the build
fails loudly. Idempotent: a file that already has the guard is left alone.
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/hermes/agent/title_generator.py"
FUNC = "def maybe_auto_title("
ANCHOR = "    if not session_db or not session_id or not user_message or not assistant_response:"
MARK = "# TechDeva T11 (patch_no_autotitle.py)"

src = open(PATH, encoding="utf-8").read()
if MARK in src:
    print("patch_no_autotitle: already patched, nothing to do")
    sys.exit(0)
if src.count(FUNC) != 1:
    sys.exit("patch_no_autotitle: %s found %d times; refusing to guess" % (FUNC, src.count(FUNC)))
start = src.index(FUNC)
nxt = re.search(r"\n(?:def |class |async def )", src[start + len(FUNC):])
end = start + len(FUNC) + (nxt.start() if nxt else len(src) - start - len(FUNC))
body = src[start:end]
if body.count(ANCHOR) != 1:
    sys.exit("patch_no_autotitle: anchor found %d times in the function; refusing to patch" % body.count(ANCHOR))
guard = (
    "    %s: no title request on the free tier unless TD_AUTO_TITLE=1.\n"
    "    import os as _td_os\n"
    "    if _td_os.environ.get(\"TD_AUTO_TITLE\", \"0\") != \"1\":\n"
    "        return\n"
) % MARK
body = body.replace(ANCHOR, guard + ANCHOR, 1)
open(PATH, "w", encoding="utf-8").write(src[:start] + body + src[end:])
print("patch_no_autotitle: session titles no longer cost a model request")
