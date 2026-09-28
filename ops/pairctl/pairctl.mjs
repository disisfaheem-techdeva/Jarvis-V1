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

function readBody(req) {
  return new Promise((ok, bad) => {
    let b = '';
    req.on('data', c => { b += c; if (b.length > 300000) { bad(new Error('too big')); req.destroy(); } });
    req.on('end', () => ok(b));
    req.on('error', bad);
  });
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
      return send(res, 200, { agent: AGENT, running: false, state: 'connected',
                              number: s.number, name: s.name, qr: null });
    }
    return send(res, 200, { agent: AGENT, running: true, ...s });
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

  if (req.method === 'GET' && url === '/health') {
    return send(res, 200, { ok: true, agent: AGENT, pairing: !!child });
  }

  send(res, 404, { error: 'not found' });
});

server.listen(PORT, '0.0.0.0', () => log('listening on', PORT, 'for agent', AGENT));
process.on('SIGTERM', () => { stopBridge('shutting down'); process.exit(0); });
