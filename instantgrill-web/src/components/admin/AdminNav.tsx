"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { SignOut } from "@phosphor-icons/react";

const links = [
  { href: "/admin", label: "Dashboard" },
  { href: "/admin/orders", label: "Orders" },
  { href: "/admin/menu", label: "Menu & areas" },
  { href: "/admin/customers", label: "Customers" },
  { href: "/admin/enquiries", label: "Enquiries" },
];

export function AdminNav() {
  const path = usePathname();
  const router = useRouter();
  return (
    <nav aria-label="Admin" className="-mx-4 flex items-center gap-1 overflow-x-auto px-4 pb-2 [scrollbar-width:none] print:hidden">
      {links.map((l) => {
        const active = l.href === "/admin" ? path === "/admin" : path.startsWith(l.href);
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={active ? "page" : undefined}
            className={`shrink-0 rounded-full px-4 py-2 text-sm font-medium transition ${
              active ? "bg-ember-500 text-coal-950" : "text-ash-300 hover:text-ash-50"
            }`}
          >
            {l.label}
          </Link>
        );
      })}
      <button
        type="button"
        onClick={async () => {
          await fetch("/api/admin/logout", { method: "POST" });
          router.refresh();
        }}
        className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full px-4 py-2 text-sm text-ash-400 hover:text-ash-50"
      >
        <SignOut size={16} /> Sign out
      </button>
    </nav>
  );
}
