// pairctl - the pairing sidecar for one Hermes agent.
//
// Why this exists. The Hermes gateway starts the WhatsApp bridge, waits about
// thirteen seconds for it to report a paired connection, and tears it down when
// it does not. That is sensible for a bot that is already paired and useless for
// pairing one: there is nothing alive for a client to scan. The gateway also
// exposes no API to ask it for a pairing session.
//
// So pairing gets its own process, owned by us. This sidecar runs beside one
// agent, mounts that agent's data volume and nothing else, and can do exactly
// three things: start a pairing bridge, report its state, stop it. It holds no
// Docker privileges and it can reach no other tenant's data.
//
// The moment the number is paired the sidecar stops the pairing bridge, because
// the credentials are now on the volume and the gateway's own bridge is the one
// that should be talking to customers.

import http from 'http';
import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';
import { DatabaseSync } from 'node:sqlite';

const PORT        = parseInt(process.env.PAIRCTL_PORT || '3010', 10);
const BRIDGE_PORT = parseInt(process.env.PAIRCTL_BRIDGE_PORT || '3011', 10);
const BRIDGE_JS   = process.env.PAIRCTL_BRIDGE_JS || '/opt/data/scripts/whatsapp-bridge/bridge.js';
const SESSION_DIR = process.env.PAIRCTL_SESSION_DIR || '/opt/data/whatsapp/session';
const KEY         = process.env.HERMES_INTERNAL_KEY || '';
const AGENT       = process.env.PAIRCTL_AGENT || 'unknown';

// A pairing session that nobody completes should not sit there forever offering
// a live QR for someone's business number.
const MAX_PAIRING_MS = 10 * 60 * 1000;

let child = null;
let startedAt = null;

function log(...a) { console.log(new Date().toISOString(), '[pairctl]', ...a); }

function startBridge() {
  if (child) return { started: false, already: true };
  child = spawn(process.execPath, [BRIDGE_JS, '--port', String(BRIDGE_PORT), '--session', SESSION_DIR], {
    cwd: BRIDGE_JS.replace(/\/[^/]+$/, ''),
    stdio: ['ignore', 'pipe', 'pipe']
  });
  startedAt = Date.now();
  child.stdout.on('data', d => process.stdout.write(d));
  child.stderr.on('data', d => process.stderr.write(d));
  child.on('exit', (code) => { log('pairing bridge exited', code); child = null; startedAt = null; });
  log('pairing bridge started on', BRIDGE_PORT, 'session', SESSION_DIR);
  return { started: true };
}

function stopBridge(why) {
  if (!child) return { stopped: false };
  log('stopping pairing bridge:', why);
  try { child.kill('SIGTERM'); } catch (e) {}
  child = null; startedAt = null;
  return { stopped: true };
}

function bridgeSession() {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port: BRIDGE_PORT, path: '/session', timeout: 3000, headers: { 'x-internal-key': KEY } }, (res) => {
      let body = '';
      res.on('data', c => body += c);
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
  });
}

// ---- WhatsApp inbox + owner lessons -------------------------------------
// The agent keeps every WhatsApp conversation in its own state.db. This
// sidecar already has the agent's volume, so it is the one place that can
// hand those chats to the platform (read-only) and write the owner's
// standing instructions into SOUL.md -- without the platform needing the
// Docker socket.
const DATA_DIR = process.env.PAIRCTL_DATA_DIR || '/opt/data';
const STATE_DB = path.join(DATA_DIR, 'state.db');
const SOUL = path.join(DATA_DIR, 'SOUL.md');
const L_START = '<!-- TECHDEVA-LESSONS-START -->';
const L_END = '<!-- TECHDEVA-LESSONS-END -->';

function openDb() {
  if (!fs.existsSync(STATE_DB)) return null;
  return new DatabaseSync(STATE_DB, { readOnly: true });
}

function iso(ts) { return ts ? new Date(ts * 1000).toISOString() : null; }

function resolvePhone(uid) {
  const s = String(uid || '');
  const digits = s.replace(/\D/g, '');
  if (!s.includes('@lid')) return digits;
  try {
    const p = path.join(SESSION_DIR, `lid-mapping-${digits}_reverse.json`);
    if (fs.existsSync(p)) {
      const v = String(JSON.parse(fs.readFileSync(p, 'utf8'))).replace(/\D/g, '');
      if (v.length >= 7 && v.length <= 15) return v;
    }
  } catch (e) { /* unresolved is shown as unresolved */ }
  return '';
}

function listChats() {
  const d = openDb();
  if (!d) return [];
  try {
    const rows = d.prepare(`SELECT id, user_id, message_count, started_at FROM sessions
                            WHERE source = 'whatsapp' ORDER BY started_at DESC LIMIT 150`).all();
    const lastQ = d.prepare(`SELECT content, timestamp FROM messages
                             WHERE session_id = ? AND role IN ('user','assistant')
                               AND content IS NOT NULL AND content <> ''
                             ORDER BY id DESC LIMIT 1`);
    return rows.map(r => {
      const l = lastQ.get(r.id) || {};
      return {
        session_id: r.id,
        phone: resolvePhone(r.user_id) || null,
        messages: r.message_count || 0,
        last: String(l.content || '').replace(/\s+/g, ' ').slice(0, 140),
        last_at: iso(l.timestamp),
        started_at: iso(r.started_at),
      };
    });
  } finally { d.close(); }
}

function chatMessages(sid) {
  const d = openDb();
  if (!d) return null;
  try {
    if (!d.prepare(`SELECT id FROM sessions WHERE id = ? AND source = 'whatsapp'`).get(sid)) return null;
    return d.prepare(`SELECT role, content, timestamp FROM messages
                      WHERE session_id = ? AND role IN ('user','assistant')
                        AND content IS NOT NULL AND content <> ''
                      ORDER BY id ASC LIMIT 400`).all(sid)
      .map(m => ({ role: m.role, content: String(m.content).slice(0, 4000), at: iso(m.timestamp) }));
  } finally { d.close(); }
}

// ---- sending a reply from the dashboard ---------------------------------
// The agent's own WhatsApp bridge listens on 127.0.0.1 inside the agent's
// network namespace and refuses non-loopback Host headers. pairctl reaches it
// only when it runs in that same namespace (network_mode: "service:<agent>"
// in the agent template). Anywhere else the connect fails and the caller gets
// that failure verbatim -- never a pretend "sent".
const AGENT_BRIDGE_PORT = parseInt(process.env.PAIRCTL_AGENT_BRIDGE_PORT || '3000', 10);
// Set only by the agent template (pairctl shares the agent's network there, so
// 127.0.0.1:AGENT_BRIDGE_PORT really is that agent's bridge). Hand-built
// pairctls (the clinic's) leave it unset and behave exactly as before.
const GUARD_LIVE = process.env.PAIRCTL_GUARD_LIVE === '1';

function agentBridgeHealth() {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port: AGENT_BRIDGE_PORT, path: '/health', timeout: 3000 }, (r) => {
      let b = ''; r.on('data', c => { b += c; if (b.length > 20000) req.destroy(); });
      r.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { resolve({ status: 'unknown' }); } });
    });
    req.on('timeout', () => { req.destroy(); resolve({ status: 'unreachable' }); });
    req.on('error', () => resolve({ status: 'unreachable' }));
  });
}

function moveOldLoginAside() {
  try {
    if (!fs.existsSync(path.join(SESSION_DIR, 'creds.json'))) return null;
    const to = SESSION_DIR + '.old-' + new Date().toISOString().replace(/[:.]/g, '-');
    fs.renameSync(SESSION_DIR, to);
    return to;
  } catch (e) { log('could not move the old login aside:', e.message); return null; }
}

function chatJid(sid) {
  const d = openDb();
  if (!d) return null;
  try {
    const r = d.prepare(`SELECT user_id FROM sessions WHERE id = ? AND source = 'whatsapp'`).get(sid);
    if (!r || !r.user_id) return null;
    const u = String(r.user_id);
    return u.includes('@') ? u : u.replace(/\D/g, '') + '@s.whatsapp.net';
  } finally { d.close(); }
}

function bridgeSend(chatId, message) {
  return new Promise((resolve) => {
    const body = JSON.stringify({ chatId, message });
    const req = http.request({ host: '127.0.0.1', port: AGENT_BRIDGE_PORT, path: '/send', method: 'POST', timeout: 30000,
      headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body),
                 host: '127.0.0.1:' + AGENT_BRIDGE_PORT, 'x-internal-key': KEY } }, (res) => {
      let b = '';
      res.on('data', c => b += c);
      res.on('end', () => { let j = null; try { j = JSON.parse(b); } catch (e) {} resolve({ status: res.statusCode, body: j, raw: b.slice(0, 300) }); });
    });
    req.on('error', (e) => resolve({ status: 0, error: e.code || e.message }));
    req.on('timeout', () => { req.destroy(); resolve({ status: 0, error: 'timed out after 30s' }); });
    req.end(body);
  });
}

// T6. The gateway gives up on WhatsApp when it starts unpaired and does not
// retry, so a fresh scan is not picked up until the agent restarts. This
// marker tells the host's pairwatch that a pairing finished; pairwatch
// restarts the agent once (the marker is newer than the agent's start).
function markPaired(number) {
  try {
    const dir = path.join(DATA_DIR, 'pairing');
    fs.mkdirSync(dir, { recursive: true });
    const tmp = path.join(dir, '.techdeva-paired.json.tmp');
    fs.writeFileSync(tmp, JSON.stringify({ agent: AGENT, number: number || null, at: new Date().toISOString() }));
    fs.renameSync(tmp, path.join(dir, 'techdeva-paired.json'));
    log('pairing marker written; pairwatch will restart the agent');
  } catch (e) { log('could not write pairing marker:', e.message); }
}

function readBody(req) {
  return new Promise((ok, bad) => {
    let b = '';
    req.on('data', c => { b += c; if (b.length > 300000) { bad(new Error('too big')); req.destroy(); } });
    req.on('end', () => ok(b));
    req.on('error', bad);
  });
}

// The business's own facts (services, prices, hours, FAQs, what it does NOT
// offer, rules for its kind of business), written by the platform from the
// dashboard (app/categories.py). Kept between its own markers, before the
// owner's standing instructions so those still win. Template agents only call
// it; a hand-built agent is never sent this.
const K_START = '<!-- TECHDEVA-KNOWLEDGE-START -->';
const K_END = '<!-- TECHDEVA-KNOWLEDGE-END -->';
function writeKnowledge(text) {
  const clean = String(text || '').replace(/<!--|-->/g, '').slice(0, 60000).trim();
  if (!clean) throw new Error('knowledge text is empty');
  const soul = fs.existsSync(SOUL) ? fs.readFileSync(SOUL, 'utf8') : '';
  const block = K_START + '\n' + clean + '\n' + K_END;
  const a = soul.indexOf(K_START), b = soul.indexOf(K_END);
  let next;
  if (a !== -1 && b > a) next = soul.slice(0, a) + block + soul.slice(b + K_END.length);
  else {
    const l = soul.indexOf(L_START);
    next = l !== -1 ? soul.slice(0, l).replace(/\s*$/, '') + '\n\n' + block + '\n\n' + soul.slice(l)
                    : soul.replace(/\s*$/, '') + '\n\n' + block + '\n';
  }
  if (soul && !fs.existsSync(SOUL + '.pre-knowledge')) fs.writeFileSync(SOUL + '.pre-knowledge', soul);
  const tmp = SOUL + '.tmp-' + process.pid;
  fs.writeFileSync(tmp, next);
  fs.renameSync(tmp, SOUL);
  log('wrote the business knowledge into SOUL.md (' + clean.length + ' chars)');
  return { chars: clean.length };
}

function writeLessons(lessons) {
  if (!Array.isArray(lessons)) throw new Error('lessons must be a list');
  const clean = lessons.slice(0, 200)
    .map(x => String(x || '').replace(/<!--|-->/g, '').replace(/\s+/g, ' ').trim().slice(0, 500))
    .filter(Boolean);
  const soul = fs.existsSync(SOUL) ? fs.readFileSync(SOUL, 'utf8') : '';
  const block = [
    L_START,
    '## Standing instructions from the business owner',
    'The owner set these in their TechDeva dashboard. Follow them exactly; where they conflict with anything above, these win.',
    ...(clean.length ? clean.map(l => '- ' + l) : ['- (none yet)']),
    L_END,
  ].join('\n');
  const a = soul.indexOf(L_START);
  const b = soul.indexOf(L_END);
  const next = (a !== -1 && b > a)
    ? soul.slice(0, a) + block + soul.slice(b + L_END.length)
    : soul.replace(/\s*$/, '') + '\n\n' + block + '\n';
  if (soul && !fs.existsSync(SOUL + '.pre-lessons')) fs.writeFileSync(SOUL + '.pre-lessons', soul);
  const tmp = SOUL + '.tmp-' + process.pid;
  fs.writeFileSync(tmp, next);
  fs.renameSync(tmp, SOUL);
  log('wrote', clean.length, 'owner lessons into SOUL.md');
  return { count: clean.length };
}

function send(res, code, obj) {
  const b = JSON.stringify(obj);
  res.writeHead(code, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(b) });
  res.end(b);
}

const server = http.createServer(async (req, res) => {
  // No key, no service. A pairing code and a logout are both things you only
  // want the platform doing, and this port is reachable by everything on the
  // Docker network.
  if (!KEY) return send(res, 503, { error: 'HERMES_INTERNAL_KEY is not set; refusing to serve' });
  if ((req.headers['x-internal-key'] || '') !== KEY) return send(res, 401, { error: 'bad internal key' });

  const url = (req.url || '').split('?')[0];

  if (req.method === 'POST' && url === '/pair/start') {
    if (GUARD_LIVE && !child) {
      // Template agents only (PAIRCTL_GUARD_LIVE=1). A second bridge on the same
      // login while the agent's own bridge is connected makes WhatsApp drop the
      // device ("conflict: replaced", then "device_removed": Networkometis,
      // 4 Oct 2026). So: refuse while it is connected; and when it is not, an old
      // login left on the volume would only log straight out again, so it is
      // moved aside (renamed, never deleted) and the scan starts clean.
      const h = await agentBridgeHealth();
      if (h.status === 'connected') {
        return send(res, 409, { agent: AGENT, error: 'already_connected',
          message: 'This WhatsApp number is already connected and answering customers.' });
      }
      const moved = moveOldLoginAside();
      if (moved) log('old login moved aside to', moved, '(agent bridge:', h.status || 'unreachable', ')');
    }
    const r = startBridge();
    return send(res, 200, { agent: AGENT, ...r, bridge_port: BRIDGE_PORT });
  }

  if (req.method === 'POST' && url === '/pair/stop') {
    return send(res, 200, { agent: AGENT, ...stopBridge('asked to') });
  }

  if (req.method === 'GET' && url === '/pair/status') {
    if (!child) return send(res, 200, { agent: AGENT, running: false, state: 'idle', qr: null });
    if (Date.now() - startedAt > MAX_PAIRING_MS) {
      stopBridge('pairing window expired');
      return send(res, 200, { agent: AGENT, running: false, state: 'expired', qr: null });
    }
    const s = await bridgeSession();
    if (!s) return send(res, 200, { agent: AGENT, running: true, state: 'starting', qr: null });
    if (s.connected) {
      // Credentials are on the volume now. Hand the number back to the gateway.
      stopBridge('paired');
      markPaired(s.number);
      return send(res, 200, { agent: AGENT, running: false, state: 'connected',
                              number: s.number, name: s.name, qr: null });
    }
    return send(res, 200, { agent: AGENT, running: true, ...s });
  }

  // Plan limits: how many different customers wrote to this number since a
  // moment (the start of the month). Counts people, not messages; the agent's
  // own retry nudges are stored as 'user' and are left out.
  if (req.method === 'GET' && url === '/usage') {
    const since = Math.max(0, Number(new URL(req.url, 'http://x').searchParams.get('since')) || 0);
    const d = openDb();
    if (!d) return send(res, 200, { agent: AGENT, since, customers: 0 });
    try {
      const row = d.prepare(`SELECT COUNT(DISTINCT s.user_id) AS n FROM sessions s
                             JOIN messages m ON m.session_id = s.id
                             WHERE s.source = 'whatsapp' AND m.role = 'user' AND m.timestamp >= ?
                               AND m.content NOT LIKE '[System%'`).get(since);
      return send(res, 200, { agent: AGENT, since, customers: Number(row && row.n) || 0 });
    } catch (e) { log('usage failed', e.message); return send(res, 500, { error: 'could not count' }); }
    finally { d.close(); }
  }

  if (req.method === 'GET' && url === '/chats') {
    try { return send(res, 200, { agent: AGENT, chats: listChats() }); }
    catch (e) { log('chats failed', e.message); return send(res, 500, { error: 'could not read chats' }); }
  }

  if (req.method === 'GET' && url.startsWith('/chats/')) {
    let msgs;
    try { msgs = chatMessages(decodeURIComponent(url.slice(7))); }
    catch (e) { log('chat failed', e.message); return send(res, 500, { error: 'could not read chat' }); }
    if (msgs === null) return send(res, 404, { error: 'no such conversation' });
    return send(res, 200, { agent: AGENT, messages: msgs });
  }

  if (req.method === 'POST' && url === '/soul/lessons') {
    let body;
    try { body = JSON.parse((await readBody(req)) || '{}'); }
    catch (e) { return send(res, 400, { error: 'bad json' }); }
    try { return send(res, 200, { agent: AGENT, ...writeLessons(body.lessons) }); }
    catch (e) { log('lesson write failed', e.message); return send(res, 500, { error: 'could not write SOUL.md' }); }
  }

  if (req.method === 'POST' && url === '/soul/knowledge') {
    let body;
    try { body = JSON.parse((await readBody(req)) || '{}'); }
    catch (e) { return send(res, 400, { error: 'bad json' }); }
    try { return send(res, 200, { agent: AGENT, ...writeKnowledge(body.text) }); }
    catch (e) { log('knowledge write failed', e.message); return send(res, 500, { error: 'could not write SOUL.md' }); }
  }

  if (req.method === 'POST' && url === '/send') {
    let body;
    try { body = JSON.parse((await readBody(req)) || '{}'); }
    catch (e) { return send(res, 400, { error: 'bad json' }); }
    const text = String(body.text || '').trim();
    if (!text || text.length > 4000) return send(res, 400, { error: 'text must be 1 to 4000 characters' });
    let jid;
    try { jid = chatJid(String(body.session_id || '')); }
    catch (e) { return send(res, 500, { error: 'could not read the conversation: ' + e.message }); }
    if (!jid) return send(res, 404, { error: 'no such conversation' });
    const r = await bridgeSend(jid, text);
    if (r.status === 0) {
      log('send failed: bridge unreachable', r.error);
      return send(res, 502, { error: `cannot reach the agent's WhatsApp bridge on 127.0.0.1:${AGENT_BRIDGE_PORT} (${r.error}). ` +
                                     'This pairctl does not share the agent network, or the agent is down.' });
    }
    if (r.status !== 200 || !r.body || !r.body.success) {
      return send(res, 502, { error: `the WhatsApp bridge refused (HTTP ${r.status}): ` + ((r.body && r.body.error) || r.raw || 'no reason given') });
    }
    if (r.body.guarded) return send(res, 502, { error: 'the bridge held this message back (reliability guard)' });
    log('sent a dashboard reply to', jid.replace(/\d(?=\d{4})/g, '*'));
    return send(res, 200, { agent: AGENT, sent: true, message_id: r.body.messageId || null, at: new Date().toISOString() });
  }

  if (req.method === 'GET' && url === '/health') {
    return send(res, 200, { ok: true, agent: AGENT, pairing: !!child, can_send: true });
  }

  send(res, 404, { error: 'not found' });
});

server.listen(PORT, '0.0.0.0', () => log('listening on', PORT, 'for agent', AGENT));
process.on('SIGTERM', () => { stopBridge('shutting down'); process.exit(0); });
