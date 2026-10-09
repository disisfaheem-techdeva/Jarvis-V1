"use client";

// The website's "Login" button lands here. The full client dashboard lives at
// api.techdeva.net/dashboard, so this page only decides where to send people:
// signed in -> their dashboard, not signed in -> the login page. It used to
// show a developer message ("No tenant selected ...") to every visitor.

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { API_BASE } from "@/lib/api";

const DASHBOARD = "https://api.techdeva.net/dashboard";

export default function PortalEntry() {
  const [msg, setMsg] = useState("Opening your dashboard…");

  useEffect(() => {
    let done = false;
    fetch(`${API_BASE}/me`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => {
        done = true;
        if (me && me.authenticated) {
          window.location.replace(DASHBOARD);
        } else {
          window.location.replace("/login");
        }
      })
      .catch(() => {
        done = true;
        window.location.replace("/login");
      });
    const t = setTimeout(() => {
      if (!done) setMsg("Still connecting… if nothing happens, go to the login page.");
    }, 6000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="mx-auto mt-24 flex max-w-md flex-col items-center text-center" role="status" aria-live="polite">
      <Loader2 className="h-6 w-6 animate-spin text-accent-ink motion-reduce:animate-none" aria-hidden="true" />
      <h1 className="mt-4 font-display text-xl font-semibold text-fg">{msg}</h1>
      <a className="mt-4 inline-flex min-h-11 items-center text-sm font-medium text-accent-ink hover:underline" href="/login">
        Go to login
      </a>
    </div>
  );
}
