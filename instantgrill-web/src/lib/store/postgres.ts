import { Pool } from "pg";
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
 * Postgres order store. Schema lives in db/schema.sql.
 * The full order is kept in a JSONB document so the shape can grow, with the
 * fields the admin filters and sorts on lifted into real columns.
 */
export class PostgresOrderRepository implements OrderRepository {
  constructor(private pool: Pool) {}

  async create(order: Order) {
    await this.pool.query(
      `INSERT INTO orders (id, created_at, status, phone, area, slot_date, total, doc)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [
        order.id,
        order.createdAt,
        order.status,
        order.customer.phone,
        order.customer.area,
        order.slot.date,
        order.total,
        order,
      ],
    );
  }

  async get(id: string) {
    const { rows } = await this.pool.query<{ doc: Order }>("SELECT doc FROM orders WHERE id = $1", [id]);
    return rows[0]?.doc ?? null;
  }

  async list(opts: { status?: OrderStatus; limit?: number } = {}) {
    const limit = Math.min(opts.limit ?? 200, 500);
    const { rows } = opts.status
      ? await this.pool.query<{ doc: Order }>(
          "SELECT doc FROM orders WHERE status = $1 ORDER BY created_at DESC LIMIT $2",
          [opts.status, limit],
        )
      : await this.pool.query<{ doc: Order }>("SELECT doc FROM orders ORDER BY created_at DESC LIMIT $1", [limit]);
    return rows.map((r) => r.doc);
  }

  async update(id: string, patch: OrderUpdate) {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const { rows } = await client.query<{ doc: Order }>("SELECT doc FROM orders WHERE id = $1 FOR UPDATE", [id]);
      if (!rows[0]) {
        await client.query("ROLLBACK");
        return null;
      }
      const next = applyUpdate(rows[0].doc, patch);
      await client.query("UPDATE orders SET status = $2, doc = $3, updated_at = now() WHERE id = $1", [
        id,
        next.status,
        next,
      ]);
      await client.query("COMMIT");
      return next;
    } catch (err) {
      await client.query("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }
  }
}

export class PostgresInbox<T extends { id: string; createdAt: string }> implements InboxRepository<T> {
  constructor(
    private pool: Pool,
    private table: "enquiries" | "contact_messages",
  ) {}
  async create(item: T) {
    await this.pool.query(`INSERT INTO ${this.table} (id, created_at, doc) VALUES ($1, $2, $3)`, [
      item.id,
      item.createdAt,
      item,
    ]);
  }
  async list(limit = 200) {
    const { rows } = await this.pool.query<{ doc: T }>(
      `SELECT doc FROM ${this.table} ORDER BY created_at DESC LIMIT $1`,
      [Math.min(limit, 500)],
    );
    return rows.map((r) => r.doc);
  }
}

export class PostgresSettings implements SettingsRepository {
  constructor(private pool: Pool) {}
  async getOverrides() {
    const { rows } = await this.pool.query<{ slug: string; price: number | null; sold_out: boolean | null; active: boolean | null }>(
      "SELECT slug, price, sold_out, active FROM product_overrides",
    );
    const out: MenuOverrides = {};
    for (const r of rows) {
      out[r.slug] = {
        ...(r.price !== null && { price: r.price }),
        ...(r.sold_out !== null && { soldOut: r.sold_out }),
        ...(r.active !== null && { active: r.active }),
      };
    }
    return out;
  }
  async setOverride(slug: string, patch: ProductOverride) {
    await this.pool.query(
      `INSERT INTO product_overrides (slug, price, sold_out, active) VALUES ($1, $2, $3, $4)
       ON CONFLICT (slug) DO UPDATE SET
         price = COALESCE($2, product_overrides.price),
         sold_out = COALESCE($3, product_overrides.sold_out),
         active = COALESCE($4, product_overrides.active),
         updated_at = now()`,
      [slug, patch.price ?? null, patch.soldOut ?? null, patch.active ?? null],
    );
    return this.getOverrides();
  }
  async getAreas() {
    const { rows } = await this.pool.query<{ name: string; fee: number | null; active: boolean }>(
      "SELECT name, fee, active FROM delivery_areas ORDER BY sort, name",
    );
    return rows.length ? rows : null;
  }
  async setAreas(areas: DeliveryArea[]) {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await client.query("DELETE FROM delivery_areas");
      for (const [i, a] of areas.entries()) {
        await client.query("INSERT INTO delivery_areas (name, fee, active, sort) VALUES ($1, $2, $3, $4)", [
          a.name,
          a.fee,
          a.active,
          i,
        ]);
      }
      await client.query("COMMIT");
    } catch (err) {
      await client.query("ROLLBACK");
      throw err;
    } finally {
      client.release();
    }
  }
}

export function postgresStore(pool: Pool): Store {
  return {
    orders: new PostgresOrderRepository(pool),
    enquiries: new PostgresInbox(pool, "enquiries"),
    messages: new PostgresInbox(pool, "contact_messages"),
    settings: new PostgresSettings(pool),
  };
}
