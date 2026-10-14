import "server-only";
import path from "node:path";
import { Pool } from "pg";
import { fileStore } from "./file";
import { postgresStore } from "./postgres";
import type { Store } from "./types";

let instance: Store | undefined;

/**
 * DATABASE_URL set (e.g. the Supabase Postgres connection string): Postgres.
 * Otherwise JSON files under DATA_DIR (default ./data), for local dev.
 */
export function store(): Store {
  if (!instance) {
    instance = process.env.DATABASE_URL
      ? postgresStore(
          new Pool({
            connectionString: process.env.DATABASE_URL,
            max: 5,
            // Supabase and most hosted Postgres require TLS.
            ssl: process.env.DATABASE_URL.includes("localhost") ? undefined : { rejectUnauthorized: false },
          }),
        )
      : fileStore(process.env.DATA_DIR ?? path.join(process.cwd(), "data"));
  }
  return instance;
}

export const orders = () => store().orders;

export * from "./types";
