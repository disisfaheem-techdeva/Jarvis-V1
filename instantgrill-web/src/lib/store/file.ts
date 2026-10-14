import { promises as fs } from "node:fs";
import path from "node:path";
import { applyUpdate, type Order, type OrderRepository, type OrderStatus, type OrderUpdate } from "./types";

/**
 * JSON-file order store for local development and single-instance hosting.
 * Writes go through a promise chain so concurrent requests in one process
 * never interleave, and through a temp file + rename so a crash mid-write
 * cannot truncate the store. Use the Postgres store for anything multi-instance.
 */
export class FileOrderRepository implements OrderRepository {
  private file: string;
  private queue: Promise<unknown> = Promise.resolve();

  constructor(dir: string) {
    this.file = path.join(dir, "orders.json");
  }

  private async readAll(): Promise<Order[]> {
    try {
      return JSON.parse(await fs.readFile(this.file, "utf8")) as Order[];
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw err;
    }
  }

  private async writeAll(orders: Order[]): Promise<void> {
    await fs.mkdir(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.${process.pid}.tmp`;
    await fs.writeFile(tmp, JSON.stringify(orders, null, 2));
    await fs.rename(tmp, this.file);
  }

  private serial<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.queue.then(fn, fn);
    this.queue = run.catch(() => undefined);
    return run;
  }

  create(order: Order) {
    return this.serial(async () => {
      const all = await this.readAll();
      if (all.some((o) => o.id === order.id)) throw new Error("Duplicate order id");
      all.push(order);
      await this.writeAll(all);
    });
  }

  async get(id: string) {
    return (await this.readAll()).find((o) => o.id === id) ?? null;
  }

  async list(opts: { status?: OrderStatus; limit?: number } = {}) {
    const all = await this.readAll();
    return all
      .filter((o) => !opts.status || o.status === opts.status)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .slice(0, opts.limit ?? 200);
  }

  update(id: string, patch: OrderUpdate) {
    return this.serial(async () => {
      const all = await this.readAll();
      const i = all.findIndex((o) => o.id === id);
      if (i < 0) return null;
      all[i] = applyUpdate(all[i], patch);
      await this.writeAll(all);
      return all[i];
    });
  }
}
