#!/usr/bin/env python3
"""Let the dashboard show the WhatsApp QR code (run at IMAGE BUILD).

The stock Hermes WhatsApp bridge draws the pairing code in the terminal and
then forgets it, so the client's dashboard waits for a code that never comes.
This adds the same `GET /session` the clinic's bridge has had since Part 4
(ops/hermes-bridge/bridge.js): the latest QR, its age and whether the number
is connected, guarded by HERMES_INTERNAL_KEY. pairctl reads it during pairing.

Only adds code; existing routes and behaviour are unchanged. Idempotent. Exits
non-zero if the file no longer looks as expected, so a changed upstream bridge
fails the build instead of silently shipping a bridge without a QR.

    python3 patch_bridge.py /opt/hermes/scripts/whatsapp-bridge/bridge.js
"""
import sys

MARK = "// ---- techdeva: session for the dashboard QR ----"

STATE = "let lastQR = null;\nlet lastQRAt = null;\n"
ON_QR = "lastQR = qr; lastQRAt = Date.now(); connectionState = 'awaiting_scan';\n"
ON_OPEN = "lastQR = null; lastQRAt = null;\n"
ROUTES = MARK + r"""
function _tdAuthed(req) {
  const key = process.env.HERMES_INTERNAL_KEY;
  if (!key) return true;
  return (req.get('x-internal-key') || '') === key;
}
app.get('/session', (req, res) => {
  if (!_tdAuthed(req)) return res.status(401).json({ error: 'bad internal key' });
  const age = lastQRAt ? Math.round((Date.now() - lastQRAt) / 1000) : null;
  const connected = connectionState === 'connected';
  res.json({
    state: connectionState,
    connected: connected,
    number: connected && sock && sock.user ? String(sock.user.id).split(':')[0] : null,
    name: connected && sock && sock.user ? (sock.user.name || null) : null,
    // WhatsApp rotates the code about every 20 seconds; an old one will not scan.
    qr: connected ? null : lastQR,
    qr_age_seconds: age,
    qr_stale: age === null ? null : age > 60,
    at: new Date().toISOString()
  });
});
"""


def insert_after_line(src, anchor, text):
    i = src.find(anchor)
    if i < 0:
        sys.exit("patch_bridge: anchor not found: %r" % anchor)
    j = src.index("\n", i) + 1
    return src[:j] + text + src[j:]


def insert_before_line(src, anchor, text):
    i = src.find(anchor)
    if i < 0:
        sys.exit("patch_bridge: anchor not found: %r" % anchor)
    start = src.rfind("\n", 0, i) + 1
    indent = src[start:i]
    return src[:start] + "".join(indent + l + "\n" for l in text.rstrip("\n").split("\n")) + src[start:]


def main(path):
    src = open(path, encoding="utf-8").read()
    if "'/session'" in src:
        print("patch_bridge: already has /session")
        return
    for need in ("let connectionState", "qrcode.generate(qr", "connectionState = 'connected';",
                 "app.listen(", "let sock"):
        if need not in src:
            sys.exit("patch_bridge: bridge.js changed upstream, missing %r" % need)
    src = insert_after_line(src, "let connectionState", STATE)
    src = insert_before_line(src, "qrcode.generate(qr", ON_QR)
    src = insert_after_line(src, "connectionState = 'connected';", ON_OPEN)
    src = insert_before_line(src, "app.listen(", ROUTES)
    open(path, "w", encoding="utf-8").write(src)
    print("patch_bridge: /session added")


if __name__ == "__main__":
    main(sys.argv[1])
