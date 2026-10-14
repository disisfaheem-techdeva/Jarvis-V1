import { Pool } from "pg";
import { applyUpdate, type Order, type OrderRepository, type OrderStatus, type OrderUpdate } from "./types";

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
