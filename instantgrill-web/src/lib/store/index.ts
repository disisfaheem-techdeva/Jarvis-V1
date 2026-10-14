import "server-only";
import path from "node:path";
import { Pool } from "pg";
import { fileStore } from "./file";
import { postgresStore } from "./postgres";
import type { Store } from "./types";

let instance: Store | undefined;

/**
 * DATABASE_URL set (your VPS Postgres, or any hosted Postgres): Postgres.
 * Otherwise JSON files under DATA_DIR (default ./data), for local dev.
 */
export function store(): Store {
  if (!instance) {
    instance = process.env.DATABASE_URL
      ? postgresStore(
          new Pool({
            connectionString: process.env.DATABASE_URL,
            max: 5,
            // TLS only when asked for: a Postgres container on the same Docker network
            // (the VPS setup) has none; hosted Postgres uses ?sslmode=require or DATABASE_SSL=true.
            ssl:
              process.env.DATABASE_SSL === "true" || /sslmode=require/.test(process.env.DATABASE_URL)
                ? { rejectUnauthorized: false }
                : undefined,
          }),
        )
      : fileStore(process.env.DATA_DIR ?? path.join(process.cwd(), "data"));
  }
  return instance;
}

export const orders = () => store().orders;

export * from "./types";
