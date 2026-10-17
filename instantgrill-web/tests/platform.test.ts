import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deliver, sendToPlatform, signBody } from "@/lib/platform";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("platform webhook signing", () => {
  it("matches the platform's scheme: HMAC-SHA256 over '<t>.<body>'", () => {
    const h = signBody("s3cret", '{"a":1}', 1700000000);
    const expected = createHmac("sha256", "s3cret").update('1700000000.{"a":1}').digest("hex");
    expect(h).toBe(`t=1700000000,v1=${expected}`);
  });
});

describe("delivery", () => {
  it("does nothing when not configured", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    expect(await sendToPlatform("order.created", {})).toBe(false);
    expect(f).not.toHaveBeenCalled();
  });

  it("signs each attempt and retries on 5xx, stops on 4xx", async () => {
    vi.stubEnv("PLATFORM_WEBHOOK_URL", "https://platform.example/storefront/webhook/t");
    vi.stubEnv("PLATFORM_WEBHOOK_SECRET", "s3cret");
    const statuses = [503, 502, 200];
    const f = vi.fn(async (_url: string, init: RequestInit) => {
      const sig = (init.headers as Record<string, string>)["x-storefront-signature"];
      const [t, v1] = sig.split(",").map((p) => p.split("=")[1]);
      expect(createHmac("sha256", "s3cret").update(`${t}.${init.body}`).digest("hex")).toBe(v1);
      return new Response("{}", { status: statuses.shift() });
    });
    vi.stubGlobal("fetch", f);
    expect(await sendToPlatform("order.created", { order: { id: "IG-1" } }, [0, 1, 1, 1])).toBe(true);
    expect(f).toHaveBeenCalledTimes(3);

    f.mockClear();
    statuses.splice(0, statuses.length, 422);
    expect(await sendToPlatform("order.created", {}, [0, 1, 1])).toBe(false);
    expect(f).toHaveBeenCalledTimes(1); // bad payload: no pointless retries
  });

  it("treats network errors as retryable", async () => {
    vi.stubEnv("PLATFORM_WEBHOOK_URL", "https://platform.example/x");
    vi.stubEnv("PLATFORM_WEBHOOK_SECRET", "s");
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    expect(await deliver("contact.created", {})).toEqual({ ok: false, status: 0, retry: true });
  });
});
