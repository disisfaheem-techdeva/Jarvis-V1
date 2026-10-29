#!/usr/bin/env python3
"""T18: wire td_takeover.js into one agent's WhatsApp bridge.js (run as root).

  takeover_patch.py <bridge.js>

Stages (each with its own marker, applied in order, a done one skipped):
T18 human takeover, T18b stop per chat and the No Bot label. Each anchor must
occur exactly once, or nothing is written. Exit 0 when patched, 3 when already
patched. Owner and mode kept; atomic; read back.
"""
import os
import sys

MARK = "// ---- techdeva T18: human takeover ----"

EDITS = [
    # 1. load the module next to the allow-list
    ("import { matchesAllowedUser, parseAllowedUsers } from './allowlist.js';\n",
     "import { matchesAllowedUser, parseAllowedUsers } from './allowlist.js';\n"
     + MARK + "\nimport * as tdTakeover from './td_takeover.js';\n"),
    # 2. start it once the LID map exists
    ("let lidToPhone = buildLidMap();\n",
     "let lidToPhone = buildLidMap();\ntdTakeover.init({ getLidMap: () => lidToPhone });\n"),
    # 3. a message from the business number that Hermes did not send pauses that chat
    ("          // Bot mode: separate number. ALL fromMe are echo-backs of our own replies — skip.\n"
     "          continue;\n",
     "          // Bot mode: Hermes' own replies come back here and are skipped. One\n"
     "          // Hermes did not send was typed by a person: td_takeover pauses that chat.\n"
     "          tdTakeover.ownMessage(msg, sock.user, (id) => recentlySentIds.has(id));\n"
     "          continue;\n"),
    # 4. a paused chat's customer messages never reach Hermes
    ("      const messageContent = getMessageContent(msg);\n",
     "      if (!msg.key.fromMe && WHATSAPP_MODE === 'bot' && !isGroup && tdTakeover.isPaused(chatId, msg.key.remoteJidAlt)) {\n"
     "        try { console.log(JSON.stringify({ event: 'td_takeover_held_incoming', chatId, messageId: msg.key.id })); } catch {}\n"
     "        continue;\n"
     "      }\n\n"
     "      const messageContent = getMessageContent(msg);\n"),
    # 5. /send: hold Hermes in a paused chat; a dashboard reply pauses it
    ("  const { chatId, message, replyTo } = req.body;\n"
     "  if (!chatId || !message) {\n"
     "    return res.status(400).json({ error: 'chatId and message are required' });\n"
     "  }\n\n"
     "  try {\n",
     "  const { chatId, message, replyTo } = req.body;\n"
     "  if (!chatId || !message) {\n"
     "    return res.status(400).json({ error: 'chatId and message are required' });\n"
     "  }\n"
     "  const tdDashboard = tdTakeover.fromDashboard(req);\n"
     "  if (!tdDashboard && tdTakeover.isPaused(chatId)) {\n"
     "    try { console.log(JSON.stringify({ event: 'td_takeover_held_send', chatId })); } catch {}\n"
     "    return res.json({ success: true, held: 'takeover', messageIds: [] });\n"
     "  }\n"
     "  const tdSending = tdTakeover.sendStart(chatId);\n\n"
     "  try {\n"),
    ("    res.json({\n"
     "      success: true,\n"
     "      messageId: messageIds[messageIds.length - 1],\n"
     "      messageIds,\n"
     "    });\n"
     "  } catch (err) {\n"
     "    res.status(500).json({ error: err.message });\n"
     "  }\n"
     "});\n",
     "    if (tdDashboard) tdTakeover.pause(chatId, null, 'dashboard_reply');\n"
     "    res.json({\n"
     "      success: true,\n"
     "      messageId: messageIds[messageIds.length - 1],\n"
     "      messageIds,\n"
     "    });\n"
     "  } catch (err) {\n"
     "    res.status(500).json({ error: err.message });\n"
     "  } finally {\n"
     "    tdTakeover.sendEnd(tdSending);\n"
     "  }\n"
     "});\n"),
    # 6. /edit: no edits land in a paused chat
    ("  const { chatId, messageId, message } = req.body;\n"
     "  if (!chatId || !messageId || !message) {\n"
     "    return res.status(400).json({ error: 'chatId, messageId, and message are required' });\n"
     "  }\n",
     "  const { chatId, messageId, message } = req.body;\n"
     "  if (!chatId || !messageId || !message) {\n"
     "    return res.status(400).json({ error: 'chatId, messageId, and message are required' });\n"
     "  }\n"
     "  if (tdTakeover.isPaused(chatId)) {\n"
     "    try { console.log(JSON.stringify({ event: 'td_takeover_held_edit', chatId })); } catch {}\n"
     "    return res.json({ success: true, held: 'takeover', messageIds: [] });\n"
     "  }\n"),
    # 7. /send-media: hold in a paused chat
    ("  const { chatId, filePath, mediaType, caption, fileName } = req.body;\n"
     "  if (!chatId || !filePath) {\n"
     "    return res.status(400).json({ error: 'chatId and filePath are required' });\n"
     "  }\n\n"
     "  try {\n",
     "  const { chatId, filePath, mediaType, caption, fileName } = req.body;\n"
     "  if (!chatId || !filePath) {\n"
     "    return res.status(400).json({ error: 'chatId and filePath are required' });\n"
     "  }\n"
     "  if (tdTakeover.isPaused(chatId)) {\n"
     "    try { console.log(JSON.stringify({ event: 'td_takeover_held_media', chatId })); } catch {}\n"
     "    return res.json({ success: true, held: 'takeover' });\n"
     "  }\n"
     "  const tdSending = tdTakeover.sendStart(chatId);\n\n"
     "  try {\n"),
    ("    trackSentMessageId(sent);\n\n"
     "    res.json({ success: true, messageId: sent?.key?.id });\n"
     "  } catch (err) {\n"
     "    res.status(500).json({ error: err.message });\n"
     "  }\n",
     "    trackSentMessageId(sent);\n\n"
     "    res.json({ success: true, messageId: sent?.key?.id });\n"
     "  } catch (err) {\n"
     "    res.status(500).json({ error: err.message });\n"
     "  } finally {\n"
     "    tdTakeover.sendEnd(tdSending);\n"
     "  }\n"),
    # 8. /typing: no "typing..." in a paused chat
    ("  const { chatId } = req.body;\n"
     "  if (!chatId) return res.status(400).json({ error: 'chatId required' });\n",
     "  const { chatId } = req.body;\n"
     "  if (!chatId) return res.status(400).json({ error: 'chatId required' });\n"
     "  if (tdTakeover.isPaused(chatId)) return res.json({ success: true, held: 'takeover' });\n"),
    # 9. /takeover: see the paused chats, pause or resume one (internal key)
    ("// Health check\n",
     "// techdeva T18: GET /takeover lists paused chats; POST { chatId, minutes }\n"
     "// pauses one (minutes 0 resumes it). Internal key required.\n"
     "app.get('/takeover', (req, res) => {\n"
     "  if (!tdTakeover.fromDashboard(req)) return res.status(401).json({ error: 'bad internal key' });\n"
     "  res.json({ paused: tdTakeover.list() });\n"
     "});\n"
     "app.post('/takeover', (req, res) => {\n"
     "  if (!tdTakeover.fromDashboard(req)) return res.status(401).json({ error: 'bad internal key' });\n"
     "  const { chatId, minutes } = req.body || {};\n"
     "  if (!chatId) return res.status(400).json({ error: 'chatId required' });\n"
     "  const m = Number(minutes);\n"
     "  if (minutes !== undefined && (!Number.isFinite(m) || m < 0 || m > 1440)) return res.status(400).json({ error: 'minutes must be 0-1440' });\n"
     "  if (m === 0) return res.json({ resumed: tdTakeover.resume(chatId, null, 'api'), paused: tdTakeover.list() });\n"
     "  const until = minutes === undefined ? tdTakeover.pause(chatId, null, 'api') : tdTakeover.pause(chatId, null, 'api', m);\n"
     "  res.json({ until: until ? new Date(until).toISOString() : null, paused: tdTakeover.list() });\n"
     "});\n\n"
     "// Health check\n"),
]

# T18b: the dashboard stops Hermes in one chat for a while or for good, and a
# WhatsApp Business "No Bot" label stops it until the label is removed.
MARK2 = "// ---- techdeva T18b: stop per chat, No Bot label ----"

EDITS2 = [
    # 1. hear label changes (names and which chats carry them)
    ("  sock.ev.on('creds.update', () => { saveCreds(); lidToPhone = buildLidMap(); });\n",
     "  sock.ev.on('creds.update', () => { saveCreds(); lidToPhone = buildLidMap(); });\n"
     "  " + MARK2 + "\n"
     "  sock.ev.on('labels.edit', (edit) => tdTakeover.labelEdit(edit));\n"
     "  sock.ev.on('labels.association', (ev) => tdTakeover.labelAssociation(ev));\n"),
    # 2. GET /takeover says it is this version (pairctl shows the switch only then)
    ("  res.json({ paused: tdTakeover.list() });\n",
     "  res.json({ version: 2, paused: tdTakeover.list() });\n"),
    # 3. POST /takeover: minutes, for good, or start again
    ("// techdeva T18: GET /takeover lists paused chats; POST { chatId, minutes }\n"
     "// pauses one (minutes 0 resumes it). Internal key required.\n",
     "// techdeva T18: GET /takeover lists stopped chats; POST { chatId, minutes } or\n"
     "// { chatId, forever: true } stops Hermes in one chat (minutes 0 starts it\n"
     "// again; a No Bot label still keeps it quiet). Internal key required.\n"),
    ("  const { chatId, minutes } = req.body || {};\n"
     "  if (!chatId) return res.status(400).json({ error: 'chatId required' });\n"
     "  const m = Number(minutes);\n"
     "  if (minutes !== undefined && (!Number.isFinite(m) || m < 0 || m > 1440)) return res.status(400).json({ error: 'minutes must be 0-1440' });\n"
     "  if (m === 0) return res.json({ resumed: tdTakeover.resume(chatId, null, 'api'), paused: tdTakeover.list() });\n"
     "  const until = minutes === undefined ? tdTakeover.pause(chatId, null, 'api') : tdTakeover.pause(chatId, null, 'api', m);\n"
     "  res.json({ until: until ? new Date(until).toISOString() : null, paused: tdTakeover.list() });\n",
     "  const { chatId, minutes, forever } = req.body || {};\n"
     "  if (!chatId) return res.status(400).json({ error: 'chatId required' });\n"
     "  if (forever === true) {\n"
     "    tdTakeover.hold(chatId, null, null, 'api');\n"
     "    return res.json({ status: tdTakeover.status(chatId) });\n"
     "  }\n"
     "  const m = Number(minutes);\n"
     "  if (!Number.isFinite(m) || m < 0 || m > 43200) return res.status(400).json({ error: 'minutes must be 0-43200, or forever: true' });\n"
     "  if (m === 0) tdTakeover.resume(chatId, null, 'api');\n"
     "  else tdTakeover.hold(chatId, null, m, 'api');\n"
     "  res.json({ status: tdTakeover.status(chatId) });\n"),
]

# T18c: Hermes closes a chat that goes nowhere (td_close's end_chat):
# POST /takeover { chatId, minutes, kind: "closed" }.
MARK3 = "// techdeva T18c: kind closed (td_close)"

EDITS3 = [
    ("  const { chatId, minutes, forever } = req.body || {};\n",
     "  " + MARK3 + "\n"
     "  const { chatId, minutes, forever, kind } = req.body || {};\n"),
    ("  else tdTakeover.hold(chatId, null, m, 'api');\n",
     "  else tdTakeover.hold(chatId, null, m, kind === 'closed' ? 'end_chat' : 'api', kind === 'closed' ? 'closed' : 'manual');\n"),
]

STAGES = [(MARK, EDITS), (MARK2, EDITS2), (MARK3, EDITS3)]


def main(path):
    src = open(path, encoding="utf-8").read()
    out, done = src, 0
    for mark, edits in STAGES:
        if mark in out:
            continue
        for i, (old, new) in enumerate(edits, 1):
            n = out.count(old)
            if n != 1:
                sys.exit("takeover_patch: %s anchor %d found %d times, bridge.js not changed" % (mark, i, n))
            out = out.replace(old, new)
        done += len(edits)
    if not done:
        print("takeover_patch: already patched")
        return 3
    st = os.stat(path)
    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    if open(tmp, encoding="utf-8").read() != out:
        os.unlink(tmp)
        sys.exit("takeover_patch: read-back mismatch, bridge.js not changed")
    os.replace(tmp, path)
    print("takeover_patch: bridge.js patched (%d edits)" % done)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: takeover_patch.py <bridge.js>")
    sys.exit(main(sys.argv[1]))
