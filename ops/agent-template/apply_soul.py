#!/usr/bin/env python3
"""T5: put the voice-rules block into an agent's SOUL.md.

  apply_soul.py SOUL.md --assistant Zara [--business "Clinic"] [--new | --refresh]

--new writes SOUL.md from SOUL.template.md first (fails if one exists).
--refresh rewrites only the opening part (everything above the first TECHDEVA
block) from the current SOUL.template.md: agents made from the first, clinic-
worded template kept talking about consultations and treatments (Networkometis,
an IP-camera business, 5 Oct 2026). The KNOWLEDGE and LESSONS blocks are kept
exactly; the voice block is refreshed as usual. Never for the clinic's agents.
Otherwise the block between the TECHDEVA-VOICE markers is replaced, or added at
the end once. Anything the owner taught (TECHDEVA-LESSONS block) is untouched.
A copy of the previous file is kept as SOUL.md.pre-voice-<n>.
"""
import argparse, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
START, END = "<!-- TECHDEVA-VOICE-START -->", "<!-- TECHDEVA-VOICE-END -->"
ap = argparse.ArgumentParser()
ap.add_argument("soul"); ap.add_argument("--assistant", required=True)
ap.add_argument("--business", default=""); ap.add_argument("--new", action="store_true")
ap.add_argument("--refresh", action="store_true")
a = ap.parse_args()
if not a.assistant.strip() or any(c in a.assistant for c in "<>{}\n"):
    sys.exit("apply_soul: bad assistant name")
block = open(os.path.join(HERE, "soul", "voice_rules.md"), encoding="utf-8").read().strip()
block = block.replace("{{ASSISTANT_NAME}}", a.assistant.strip())
if a.new:
    if os.path.exists(a.soul):
        sys.exit("apply_soul: %s exists; refusing to overwrite with --new" % a.soul)
    if not a.business.strip():
        sys.exit("apply_soul: --business is required with --new")
    tpl = open(os.path.join(HERE, "soul", "SOUL.template.md"), encoding="utf-8").read()
    text = (tpl.replace("{{ASSISTANT_NAME}}", a.assistant.strip())
               .replace("{{BUSINESS_NAME}}", a.business.strip())
               .replace("{{VOICE_RULES}}", block))
else:
    text = open(a.soul, encoding="utf-8").read()
    if a.refresh and re.search(r"irfan|clinic|salon|workspace", os.path.abspath(a.soul), re.I):
        sys.exit("apply_soul: --refresh refuses protected agents (%s)" % a.soul)
    n = 1
    while os.path.exists("%s.pre-voice-%d" % (a.soul, n)):
        n += 1
    open("%s.pre-voice-%d" % (a.soul, n), "w", encoding="utf-8").write(text)
    if a.refresh:
        if not a.business.strip():
            sys.exit("apply_soul: --business is required with --refresh")
        tpl = open(os.path.join(HERE, "soul", "SOUL.template.md"), encoding="utf-8").read()
        head = (tpl.split("{{VOICE_RULES}}")[0].replace("{{ASSISTANT_NAME}}", a.assistant.strip())
                   .replace("{{BUSINESS_NAME}}", a.business.strip()).rstrip() + "\n\n")
        k = text.find("<!-- TECHDEVA-")
        rest = text[k:] if k != -1 else ""
        text = head + rest if rest else head + block + "\n"
    if text.count(START) > 1 or text.count(END) > 1:
        sys.exit("apply_soul: voice markers appear more than once; fix by hand")
    i, j = text.find(START), text.find(END)
    if i != -1 and j > i:
        text = text[:i] + block + text[j + len(END):]
    elif i == -1 and j == -1:
        text = text.rstrip() + "\n\n" + block + "\n"
    else:
        sys.exit("apply_soul: only one voice marker found; fix by hand")
tmp = a.soul + ".tmp"
open(tmp, "w", encoding="utf-8").write(text)
os.replace(tmp, a.soul)
assert open(a.soul, encoding="utf-8").read().count(START) == 1
print("apply_soul: voice rules in place for %s" % a.assistant.strip())
