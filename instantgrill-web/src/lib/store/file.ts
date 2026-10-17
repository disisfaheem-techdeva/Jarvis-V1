import { promises as fs } from "node:fs";
import path from "node:path";
import {
  applyUpdate,
  type DeliveryArea,
  type InboxRepository,
  type MenuOverrides,
  type Order,
  type OrderRepository,
  type OrderStatus,
  type OrderUpdate,
  type ProductOverride,
  type SettingsRepository,
  type Store,
} from "./types";

/**
 * One JSON file. Writes go through a promise chain so concurrent requests in
 * one process never interleave, and through temp file + rename so a crash
 * mid-write cannot truncate it. For dev and single-server hosting only.
 */
class JsonFile<T> {
  private queue: Promise<unknown> = Promise.resolve();
  constructor(
    private file: string,
    private empty: T,
  ) {}

  async read(): Promise<T> {
    try {
      return JSON.parse(await fs.readFile(this.file, "utf8")) as T;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return structuredClone(this.empty);
      throw err;
    }
  }

  /** Read, change, write, as one serialised step. */
  update<R>(fn: (data: T) => R | Promise<R>): Promise<R> {
    const run = this.queue.then(async () => {
      const data = await this.read();
      const result = await fn(data);
      await fs.mkdir(path.dirname(this.file), { recursive: true });
      const tmp = `${this.file}.${process.pid}.tmp`;
      await fs.writeFile(tmp, JSON.stringify(data, null, 2));
      await fs.rename(tmp, this.file);
      return result;
    });
    this.queue = run.catch(() => undefined);
    return run;
  }
}

export class FileOrderRepository implements OrderRepository {
  private db: JsonFile<Order[]>;
  constructor(dir: string) {
    this.db = new JsonFile(path.join(dir, "orders.json"), []);
  }

  create(order: Order) {
    return this.db.update((all) => {
      if (all.some((o) => o.id === order.id)) throw new Error("Duplicate order id");
      all.push(order);
    });
  }

  async get(id: string) {
    return (await this.db.read()).find((o) => o.id === id) ?? null;
  }

  async list(opts: { status?: OrderStatus; limit?: number } = {}) {
    return (await this.db.read())
      .filter((o) => !opts.status || o.status === opts.status)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .slice(0, opts.limit ?? 500);
  }

  update(id: string, patch: OrderUpdate) {
    return this.db.update((all) => {
      const i = all.findIndex((o) => o.id === id);
      if (i < 0) return null;
      all[i] = applyUpdate(all[i], patch);
      return all[i];
    });
  }
}

class FileInbox<T extends { createdAt: string }> implements InboxRepository<T> {
  private db: JsonFile<T[]>;
  constructor(file: string) {
    this.db = new JsonFile(file, []);
  }
  create(item: T) {
    return this.db.update((all) => {
      all.push(item);
    });
  }
  async list(limit = 200) {
    return (await this.db.read()).sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, limit);
  }
}

type Settings = { overrides: MenuOverrides; areas: DeliveryArea[] | null };

class FileSettings implements SettingsRepository {
  private db: JsonFile<Settings>;
  constructor(file: string) {
    this.db = new JsonFile<Settings>(file, { overrides: {}, areas: null });
  }
  async getOverrides() {
    return (await this.db.read()).overrides;
  }
  setOverride(slug: string, patch: ProductOverride) {
    return this.db.update((s) => {
      s.overrides[slug] = { ...s.overrides[slug], ...patch };
      return s.overrides;
    });
  }
  async getAreas() {
    return (await this.db.read()).areas;
  }
  setAreas(areas: DeliveryArea[]) {
    return this.db.update((s) => {
      s.areas = areas;
    });
  }
}

export function fileStore(dir: string): Store {
  return {
    orders: new FileOrderRepository(dir),
    enquiries: new FileInbox(path.join(dir, "enquiries.json")),
    messages: new FileInbox(path.join(dir, "messages.json")),
    settings: new FileSettings(path.join(dir, "settings.json")),
  };
}
