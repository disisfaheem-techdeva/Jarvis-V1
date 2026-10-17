import { describe, expect, it } from "vitest";
import { MORNING, availableSlots, describeSlot, isSlotAvailable } from "@/lib/delivery";

/** A moment given in Lahore local time (UTC+5). */
const pkt = (iso: string) => new Date(`${iso}+05:00`);

describe("delivery slots", () => {
  it("12 PM to 5:30 PM: earliest is order time + 6 h, rounded up to 30 min", () => {
    const days = availableSlots(pkt("2026-10-14T13:10:00"));
    expect(days[0]).toMatchObject({ date: "2026-10-14", label: "Today" });
    expect(days[0].slots[0].value).toBe("19:30");
    expect(days[0].slots.at(-1)?.value).toBe("23:30");
    expect(days[0].slots.some((s) => s.value === MORNING)).toBe(false);
  });

  it("exactly on the half hour is not pushed later", () => {
    expect(availableSlots(pkt("2026-10-14T14:00:00"))[0].slots[0].value).toBe("20:00");
  });

  it("5:30 PM is the last same-day order (11:30 PM slot)", () => {
    const days = availableSlots(pkt("2026-10-14T17:30:00"));
    expect(days[0].slots.map((s) => s.value)).toEqual(["23:30"]);
  });

  it("after 5:30 PM: first slot is next morning 10 AM to 12 PM", () => {
    const days = availableSlots(pkt("2026-10-14T17:31:00"));
    expect(days[0]).toMatchObject({ date: "2026-10-15", label: "Tomorrow" });
    expect(days[0].slots[0].value).toBe(MORNING);
  });

  it("after midnight: that same morning's 10 AM to 12 PM slot", () => {
    const days = availableSlots(pkt("2026-10-15T01:20:00"));
    expect(days[0]).toMatchObject({ date: "2026-10-15", label: "Today" });
    expect(days[0].slots[0].value).toBe(MORNING);
  });

  it("10 AM to 12 PM (before opening): 6 hours counted from 12 PM", () => {
    expect(availableSlots(pkt("2026-10-14T10:45:00"))[0].slots[0].value).toBe("18:00");
  });

  it("lets the customer pick a later slot, up to 7 days ahead", () => {
    const days = availableSlots(pkt("2026-10-14T13:00:00"));
    expect(days.at(-1)?.date).toBe("2026-10-21");
    expect(days[1].slots[0].value).toBe(MORNING);
    expect(days[1].slots[1].value).toBe("12:30");
  });

  it("validates slots against the clock", () => {
    const now = pkt("2026-10-14T13:00:00");
    expect(isSlotAvailable({ date: "2026-10-14", slot: "19:00" }, now)).toBe(true);
    expect(isSlotAvailable({ date: "2026-10-14", slot: "18:30" }, now)).toBe(false); // under 6 h
    expect(isSlotAvailable({ date: "2026-10-14", slot: MORNING }, now)).toBe(false); // passed
    expect(isSlotAvailable({ date: "2026-10-22", slot: MORNING }, now)).toBe(false); // over 7 days
    expect(isSlotAvailable({ date: "2026-10-15", slot: "12:15" }, now)).toBe(false); // not a slot
  });

  it("describes slots the way checkout shows them", () => {
    const now = pkt("2026-10-14T13:00:00");
    expect(describeSlot({ date: "2026-10-14", slot: "19:30" }, now)).toBe("today 7:30 PM");
    expect(describeSlot({ date: "2026-10-15", slot: MORNING }, now)).toBe("tomorrow 10 AM – 12 PM");
  });

  it("uses Lahore time even at UTC day boundaries", () => {
    // 20:00 UTC on the 14th is 01:00 on the 15th in Lahore.
    expect(availableSlots(new Date("2026-10-14T20:00:00Z"))[0].date).toBe("2026-10-15");
  });
});
