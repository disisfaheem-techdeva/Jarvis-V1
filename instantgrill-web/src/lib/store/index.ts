import "server-only";
import path from "node:path";
import { Pool } from "pg";
import { FileOrderRepository } from "./file";
import { PostgresOrderRepository } from "./postgres";
import type { OrderRepository } from "./types";

let repo: OrderRepository | undefined;

/** DATABASE_URL set: Postgres. Otherwise a JSON file under DATA_DIR (default ./data). */
export function orders(): OrderRepository {
  if (!repo) {
    repo = process.env.DATABASE_URL
      ? new PostgresOrderRepository(new Pool({ connectionString: process.env.DATABASE_URL, max: 5 }))
      : new FileOrderRepository(process.env.DATA_DIR ?? path.join(process.cwd(), "data"));
  }
  return repo;
}

export * from "./types";
