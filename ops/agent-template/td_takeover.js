/**
 * TechDeva td_takeover: Hermes keeps quiet in a chat a person is answering (Rafay, 29 Oct 2026).
 *
 * In bot mode every message from the business number that Hermes did not send
 * was typed by a person: on the phone, on WhatsApp Web, or from the dashboard
 * (pairctl's /send carries the internal key). That chat is paused for
 * TD_TAKEOVER_MINUTES (default 1) after the person's latest message:
 *   * the customer's messages in it are not handed to Hermes;
 *   * a Hermes reply, edit, media or typing for it is held back;
 *   * every other chat carries on as before.
 * A chat is known by its phone number and its LID (both, through the
 * session's lid-mapping files), so one customer is one chat whichever id
 * WhatsApp uses. Pauses survive a bridge restart in $HERMES_HOME/td_takeover.json.
 *
 * Hermes' own echo arrives before sock.sendMessage() resolves, so a message
 * from us is judged only after ECHO_WAIT_MS (by then the bridge knows its id),
 * and never while a bridge send to that chat is still in flight. Old messages
 * WhatsApp replays after a reconnect (older than FRESH_S) never pause a chat.
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

// Content a person sends. Reactions, deletes, edits, receipts and key
// exchanges are not a person answering.
const REAL = new Set([
  'conversation', 'extendedTextMessage', 'imageMessage', 'videoMessage',
  'audioMessage', 'pttMessage', 'documentMessage', 'stickerMessage',
  'locationMessage', 'liveLocationMessage', 'contactMessage', 'contactsArrayMessage',
]);
const WRAPPERS = ['ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2', 'documentWithCaptionMessage'];

let lidMap = () => ({});
const paused = new Map();   // key -> until (ms)
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
    const out = {};
    for (const [k, until] of paused) out[k] = until;
    const tmp = `${FILE}.tmp`;
    writeFileSync(tmp, JSON.stringify(out));
    renameSync(tmp, FILE);
  } catch (err) {
    log('save_failed', { error: err.message });
  }
}

function prune() {
  const now = Date.now();
  let gone = false;
  for (const [k, until] of paused) if (until <= now) { paused.delete(k); gone = true; }
  if (gone) save();
}

export function init({ getLidMap } = {}) {
  if (getLidMap) lidMap = getLidMap;
  try {
    const saved = JSON.parse(readFileSync(FILE, 'utf8'));
    for (const [k, until] of Object.entries(saved || {})) if (Number(until) > Date.now()) paused.set(k, Number(until));
  } catch {}
  log('ready', { minutes: MINUTES, paused: paused.size, file: FILE });
}

export function isPaused(jid, alt) {
  prune();
  return keysOf(jid, alt).some(k => paused.has(k));
}

export function pause(jid, alt, why, minutes = MINUTES) {
  const keys = keysOf(jid, alt);
  if (!keys.length) return null;
  const until = Date.now() + minutes * 60000;
  for (const k of keys) paused.set(k, until);
  save();
  log('paused', { chat: keys, why, until: new Date(until).toISOString() });
  return until;
}

export function resume(jid, alt, why) {
  const keys = keysOf(jid, alt);
  let any = false;
  for (const k of keys) any = paused.delete(k) || any;
  if (any) { save(); log('resumed', { chat: keys, why }); }
  return any;
}

export function list() {
  prune();
  return [...paused].map(([chat, until]) => ({ chat, until: new Date(until).toISOString() }));
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
