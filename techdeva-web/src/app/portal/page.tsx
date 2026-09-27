"use client";

// The website's "Login" button lands here. The full client dashboard lives at
// api.techdeva.net/dashboard, so this page only decides where to send people:
// signed in -> their dashboard, not signed in -> the login page. It used to
// show a developer message ("No tenant selected ...") to every visitor.

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
    <div className="mx-auto mt-24 max-w-md text-center">
      <p className="text-sm text-[var(--muted)]">{msg}</p>
      <p className="mt-4 text-sm">
        <a className="underline" href="/login">Go to login</a>
      </p>
    </div>
  );
}
