#!/usr/bin/env python3
"""T10, applied at IMAGE BUILD time (like T3) so it survives any re-create.

Inserts, right after the handler's reply is unwrapped in
BasePlatformAdapter (gateway/platforms/base.py), a call to
gateway.td_final_reply.td_handle_failure_reply for WhatsApp. Every reply the
gateway sends passes through that line, including the error texts built by
_normalize_empty_agent_response and the exception handler in gateway/run.py.

Guarded: the anchor must occur exactly once, or the build fails loudly (an
upstream change must be looked at, not silently skipped).
Idempotent: a file that already has the hook is left alone.
"""
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/hermes/gateway/platforms/base.py"
ANCHOR = "response, _ephemeral_ttl = self._unwrap_ephemeral(response)"
MARK = "# TechDeva T10 (patch_final_reply.py)"

src = open(PATH, encoding="utf-8").read()
if MARK in src:
    print("patch_final_reply: already patched, nothing to do")
    sys.exit(0)
if src.count(ANCHOR) != 1:
    sys.exit("patch_final_reply: anchor found %d times in %s; refusing to patch" % (src.count(ANCHOR), PATH))

pos = src.index(ANCHOR)
line_start = src.rfind("\n", 0, pos) + 1
indent = src[line_start:pos]
line_end = src.index("\n", pos) + 1
hook = (
    "{i}{mark}: on WhatsApp a failure reply never reaches the\n"
    "{i}# customer; the owner's chat is alerted instead (gateway/td_final_reply.py).\n"
    "{i}if response and getattr(getattr(self, 'platform', None), 'value', None) == 'whatsapp':\n"
    "{i}    try:\n"
    "{i}        from gateway.td_final_reply import td_handle_failure_reply\n"
    "{i}        response = await td_handle_failure_reply(self, event, response)\n"
    "{i}    except Exception:  # noqa: BLE001 -- a broken guard must not drop real answers\n"
    "{i}        logger.exception('[TD] final-reply guard failed; reply kept')\n"
).format(i=indent, mark=MARK)
open(PATH, "w", encoding="utf-8").write(src[:line_end] + hook + src[line_end:])
print("patch_final_reply: WhatsApp failure replies now go to the owner, not the customer")
