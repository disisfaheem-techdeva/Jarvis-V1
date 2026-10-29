/**
 * TechDeva td_takeover: Hermes keeps quiet in a chat a person is answering (Rafay, 29 Oct 2026).
 *
 * A chat is stopped three ways:
 *   * auto:   a message from the business number that Hermes did not send was
 *             typed by a person (phone, WhatsApp Web, or a dashboard reply:
 *             pairctl's /send carries the internal key). That chat is paused for
 *             TD_TAKEOVER_MINUTES (default 1) after the person's latest message.
 *   * manual: the dashboard stops Hermes in one chat for a while or for good
 *             (POST /takeover). An auto pause never shortens it.
 *   * label:  the owner puts a WhatsApp Business label named "No Bot" (or
 *             "Hermes Off", "Bot Off") on the chat; removing it starts Hermes again.
 * While a chat is stopped:
 *   * the customer's messages in it are not handed to Hermes;
 *   * a Hermes reply, edit, media or typing for it is held back;
 *   * every other chat carries on as before.
 * A chat is known by its phone number and its LID (both, through the
 * session's lid-mapping files), so one customer is one chat whichever id
 * WhatsApp uses. State survives a bridge restart in $HERMES_HOME/td_takeover.json.
 *
 * Hermes' own echo arrives before sock.sendMessage() resolves, so a message
 * from us is judged only after ECHO_WAIT_MS (by then the bridge knows its id),
 * and never while a bridge send to that chat is still in flight. Old messages
 * WhatsApp replays after a reconnect (older than FRESH_S) never pause a chat.
 * WhatsApp sends a label's name only when it is created or renamed, so a
 * "No Bot" label made before this bridge first ran is not known by name:
 * the owner makes a new one.
 */
import { readFileSync, writeFileSync, renameSync } from 'fs';
import path from 'path';

const MINUTES = (() => {
  const m = parseFloat(process.env.TD_TAKEOVER_MINUTES || '1');
  return Number.isFinite(m) && m > 0 ? m : 1;
})();
const FRESH_S = 120;
const ECHO_WAIT_MS = 3000;
const FILE = process.env.TD_TAKEOVER_FILE
  || path.join(process.env.HERMES_HOME || '/opt/data', 'td_takeover.json');
const NO_BOT = /^\s*(no\s*-?\s*bot|hermes\s*-?\s*off|bot\s*-?\s*off)\s*$/i;

// Content a person sends. Reactions, deletes, edits, receipts and key
// exchanges are not a person answering.
const REAL = new Set([
  'conversation', 'extendedTextMessage', 'imageMessage', 'videoMessage',
  'audioMessage', 'pttMessage', 'documentMessage', 'stickerMessage',
  'locationMessage', 'liveLocationMessage', 'contactMessage', 'contactsArrayMessage',
]);
const WRAPPERS = ['ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2', 'documentWithCaptionMessage'];

let lidMap = () => ({});
const paused = new Map();   // key -> { until: ms | null (for good), kind: 'auto' | 'manual' }
const labels = new Map();   // label id -> name
const tagged = new Map();   // label id -> Set of chat jids
const inflight = new Map(); // key -> sends in progress

function log(event, extra) {
  try { console.log(JSON.stringify({ event: `td_takeover_${event}`, ...extra })); } catch {}
}

function user(jid) {
  if (!jid) return null;
  const s = String(jid);
  const at = s.indexOf('@');
  const u = (at === -1 ? s : s.slice(0, at)).split(':')[0];
  return u ? { u, lid: s.endsWith('@lid') } : null;
}

// Every id this chat may arrive under: its number and its LID.
export function keysOf(jid, alt) {
  const keys = new Set();
  const map = lidMap() || {};
  for (const j of [jid, alt]) {
    const p = user(j);
    if (!p) continue;
    keys.add(p.u);
    if (p.lid && map[p.u]) keys.add(String(map[p.u]));
    if (!p.lid) for (const [lid, phone] of Object.entries(map)) if (String(phone) === p.u) keys.add(lid);
  }
  return [...keys];
}

function save() {
  try {
    const out = { paused: {}, labels: {}, tagged: {} };
    for (const [k, v] of paused) out.paused[k] = v;
    for (const [id, name] of labels) out.labels[id] = name;
    for (const [id, set] of tagged) if (set.size) out.tagged[id] = [...set];
    const tmp = `${FILE}.tmp`;
    writeFileSync(tmp, JSON.stringify(out));
    renameSync(tmp, FILE);
  } catch (err) {
    log('save_failed', { error: err.message });
  }
}

const active = (v, now = Date.now()) => !!v && (v.until === null || v.until > now);

function prune() {
  const now = Date.now();
  let gone = false;
  for (const [k, v] of paused) if (!active(v, now)) { paused.delete(k); gone = true; }
  if (gone) save();
}

// Keys of every chat that carries a "No Bot" label right now.
function labelKeys() {
  const keys = new Set();
  for (const [id, set] of tagged) {
    if (!NO_BOT.test(labels.get(id) || '')) continue;
    for (const jid of set) for (const k of keysOf(jid)) keys.add(k);
  }
  return keys;
}

export function init({ getLidMap } = {}) {
  if (getLidMap) lidMap = getLidMap;
  try {
    const saved = JSON.parse(readFileSync(FILE, 'utf8')) || {};
    // The first version kept { key: until } only.
    const p = saved.paused && typeof saved.paused === 'object' ? saved.paused : saved;
    for (const [k, v] of Object.entries(p)) {
      const e = typeof v === 'number' ? { until: v, kind: 'auto' } : v;
      if (e && (e.until === null || Number(e.until) > Date.now())) paused.set(k, { until: e.until === null ? null : Number(e.until), kind: e.kind === 'manual' ? 'manual' : 'auto' });
    }
    for (const [id, name] of Object.entries(saved.labels || {})) labels.set(id, String(name));
    for (const [id, list] of Object.entries(saved.tagged || {})) tagged.set(id, new Set(list));
  } catch {}
  log('ready', { minutes: MINUTES, paused: paused.size, labels: labels.size, file: FILE });
}

export function isPaused(jid, alt) {
  prune();
  const keys = keysOf(jid, alt);
  if (keys.some(k => paused.has(k))) return true;
  const lk = labelKeys();
  return keys.some(k => lk.has(k));
}

// A person answered: stop Hermes here for MINUTES, never shortening a longer stop.
export function pause(jid, alt, why, minutes = MINUTES) {
  const keys = keysOf(jid, alt);
  if (!keys.length) return null;
  const until = Date.now() + minutes * 60000;
  let changed = false;
  for (const k of keys) {
    const cur = paused.get(k);
    if (active(cur) && (cur.until === null || cur.until >= until)) continue;
    paused.set(k, { until, kind: 'auto' });
    changed = true;
  }
  if (changed) { save(); log('paused', { chat: keys, why, until: new Date(until).toISOString() }); }
  return until;
}

// The dashboard: stop Hermes for `minutes`, or for good when minutes is null.
export function hold(jid, alt, minutes, why) {
  const keys = keysOf(jid, alt);
  if (!keys.length) return null;
  const until = minutes === null ? null : Date.now() + minutes * 60000;
  for (const k of keys) paused.set(k, { until, kind: 'manual' });
  save();
  log('held', { chat: keys, why, until: until === null ? 'for good' : new Date(until).toISOString() });
  return { until };
}

// Hermes may answer again. A "No Bot" label still keeps it quiet until removed.
export function resume(jid, alt, why) {
  const keys = keysOf(jid, alt);
  let any = false;
  for (const k of keys) any = paused.delete(k) || any;
  if (any) { save(); log('resumed', { chat: keys, why }); }
  return any;
}

export function status(jid, alt) {
  prune();
  const keys = keysOf(jid, alt);
  const lk = labelKeys();
  if (keys.some(k => lk.has(k))) return { until: null, kind: 'label' };
  for (const k of keys) {
    const v = paused.get(k);
    if (active(v)) return { until: v.until === null ? null : new Date(v.until).toISOString(), kind: v.kind };
  }
  return null;
}

// One row per id (number or LID), so a caller can match either.
export function list() {
  prune();
  const rows = new Map();
  for (const [k, v] of paused) rows.set(k, { chat: k, until: v.until === null ? null : new Date(v.until).toISOString(), kind: v.kind });
  for (const k of labelKeys()) rows.set(k, { chat: k, until: null, kind: 'label' });
  return [...rows.values()];
}

export function labelEdit(edit) {
  try {
    if (!edit || edit.id === undefined) return;
    const id = String(edit.id);
    if (edit.deleted) { labels.delete(id); tagged.delete(id); }
    else if (edit.name !== undefined) labels.set(id, String(edit.name));
    save();
    if (NO_BOT.test(String(edit.name || ''))) log('label', { id, name: edit.name, deleted: !!edit.deleted });
  } catch (err) {
    log('error', { error: err.message });
  }
}

export function labelAssociation(ev) {
  try {
    const a = ev?.association;
    if (!a || a.type !== 'label_jid' || !a.chatId || a.labelId === undefined) return;
    const id = String(a.labelId);
    if (!tagged.has(id)) tagged.set(id, new Set());
    const set = tagged.get(id);
    if (ev.type === 'add') set.add(String(a.chatId)); else set.delete(String(a.chatId));
    save();
    if (NO_BOT.test(labels.get(id) || '')) log(ev.type === 'add' ? 'label_on' : 'label_off', { chat: keysOf(a.chatId) });
  } catch (err) {
    log('error', { error: err.message });
  }
}

export function sendStart(jid) {
  const keys = keysOf(jid);
  for (const k of keys) inflight.set(k, (inflight.get(k) || 0) + 1);
  return keys;
}

export function sendEnd(keys) {
  for (const k of keys || []) {
    const n = (inflight.get(k) || 0) - 1;
    if (n > 0) inflight.set(k, n); else inflight.delete(k);
  }
}

function content(msg) {
  let c = msg?.message || {};
  for (let i = 0; i < 3; i += 1) {
    const w = WRAPPERS.find(k => c[k]?.message);
    if (!w) break;
    c = c[w].message;
  }
  return c;
}

// A fromMe message in bot mode. isOurs(id) says whether the bridge sent it.
export function ownMessage(msg, me, isOurs) {
  try {
    const key = msg?.key || {};
    const chatId = key.remoteJid || '';
    if (!chatId || chatId.endsWith('@g.us') || chatId.includes('status') || chatId.endsWith('@newsletter')) return;
    const mine = new Set([user(me?.id)?.u, user(me?.lid)?.u].filter(Boolean));
    if (keysOf(chatId, key.remoteJidAlt).some(k => mine.has(k))) return; // the owner's own self-chat
    if (!Object.keys(content(msg)).some(k => REAL.has(k))) return;
    const ts = Number(msg.messageTimestamp?.toNumber ? msg.messageTimestamp.toNumber() : msg.messageTimestamp) || 0;
    if (!ts || Date.now() / 1000 - ts > FRESH_S) return;
    if (keysOf(chatId, key.remoteJidAlt).some(k => inflight.get(k))) return; // Hermes is sending here right now
    setTimeout(() => {
      try {
        if (isOurs(key.id)) return;
        pause(chatId, key.remoteJidAlt, 'owner_message');
      } catch (err) {
        log('error', { error: err.message });
      }
    }, ECHO_WAIT_MS);
  } catch (err) {
    log('error', { error: err.message });
  }
}

// pairctl's /send (a dashboard reply) carries the agent's internal key.
export function fromDashboard(req) {
  const k = process.env.HERMES_INTERNAL_KEY;
  return !!k && (req.get('x-internal-key') || '') === k;
}
