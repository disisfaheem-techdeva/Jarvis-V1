/**
 * Delivery slot rules (Asia/Karachi, UTC+5, no daylight saving).
 *
 * - Orders 10:00 AM to 5:30 PM: earliest slot is 6 hours after the order (counted
 *   from 12 PM if placed before opening), rounded up to the next 30 minutes.
 *   Last slot of the day is 11:30 PM.
 * - Orders after 5:30 PM: first slot is the next-morning window, 10 AM to 12 PM.
 * - Orders between 12 AM and 10 AM: first slot is that morning's 10 AM to 12 PM window.
 * - The customer may always pick a later slot, up to 7 days ahead.
 * - A full day offers the morning window, then every 30 minutes from 12:30 PM to 11:30 PM.
 *
 * Shared by the checkout (to list slots) and the order API (to validate them).
 */

export type SlotChoice = { date: string; slot: string }; // slot: "morning" or "HH:MM"
export type DaySlots = { date: string; label: string; slots: { value: string; label: string }[] };

const PKT_OFFSET_MS = 5 * 60 * 60 * 1000;
const MORNING_END = 10 * 60;
const OPENING = 12 * 60;
const SAME_DAY_CUTOFF = 17 * 60 + 30;
const FIRST_TIMED = 12 * 60 + 30;
const LAST_TIMED = 23 * 60 + 30;
const DAYS_AHEAD = 7;

export const MORNING = "morning";

function pktNow(now: Date) {
  const t = new Date(now.getTime() + PKT_OFFSET_MS);
  return { date: t.toISOString().slice(0, 10), minutes: t.getUTCHours() * 60 + t.getUTCMinutes() };
}

function addDays(date: string, n: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

export function timeLabel(slot: string): string {
  if (slot === MORNING) return "10 AM – 12 PM";
  const [h, m] = slot.split(":").map(Number);
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h < 12 ? "AM" : "PM"}`;
}

function dayLabel(date: string, today: string): string {
  if (date === today) return "Today";
  if (date === addDays(today, 1)) return "Tomorrow";
  return new Date(`${date}T00:00:00Z`).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

function timed(from: number) {
  const out: { value: string; label: string }[] = [];
  for (let m = from; m <= LAST_TIMED; m += 30) out.push({ value: hhmm(m), label: timeLabel(hhmm(m)) });
  return out;
}

function fullDay() {
  return [{ value: MORNING, label: `${timeLabel(MORNING)} (morning)` }, ...timed(FIRST_TIMED)];
}

export function availableSlots(now: Date = new Date()): DaySlots[] {
  const { date: today, minutes } = pktNow(now);
  const days: DaySlots[] = [];
  let firstFullDay: number;

  if (minutes >= MORNING_END && minutes <= SAME_DAY_CUTOFF) {
    const earliest = Math.ceil((Math.max(minutes, OPENING) + 360) / 30) * 30;
    days.push({ date: today, label: "Today", slots: timed(earliest) });
    firstFullDay = 1;
  } else if (minutes < MORNING_END) {
    firstFullDay = 0;
  } else {
    firstFullDay = 1;
  }

  for (let d = firstFullDay; d <= DAYS_AHEAD; d++) {
    const date = addDays(today, d);
    days.push({ date, label: dayLabel(date, today), slots: fullDay() });
  }
  return days;
}

export function isSlotAvailable(choice: SlotChoice, now: Date = new Date()): boolean {
  return availableSlots(now).some((d) => d.date === choice.date && d.slots.some((s) => s.value === choice.slot));
}

/** "today 7:30 PM", "tomorrow 10 AM – 12 PM", "Fri 17 Oct, 7:30 PM". */
export function describeSlot(choice: SlotChoice, now: Date = new Date()): string {
  const { date: today } = pktNow(now);
  const day = dayLabel(choice.date, today);
  const time = timeLabel(choice.slot);
  return day === "Today" || day === "Tomorrow" ? `${day.toLowerCase()} ${time}` : `${day}, ${time}`;
}
