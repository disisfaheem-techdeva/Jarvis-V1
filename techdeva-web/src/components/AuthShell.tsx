"use client";

import { Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { forwardRef, useState } from "react";

import { Input } from "@/components/ui/Input";
import { Logo } from "@/components/ui/Logo";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

/** Centred glass card on the ambient canvas, for sign-in flows. */
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col bg-bg">
      <div aria-hidden="true" className="td-ambient pointer-events-none absolute inset-0" />
      <header className="relative flex h-16 items-center justify-between px-4 sm:px-6">
        <Link href="/" aria-label="TechDeva home" className="rounded-md">
          <Logo />
        </Link>
        <ThemeToggle />
      </header>
      <main id="main" className="relative flex flex-1 items-start justify-center px-4 pb-16 pt-8 sm:items-center sm:pt-0">
        <div className="td-glass td-rim w-full max-w-md rounded-[var(--p-radius-xl)] p-6 sm:p-8">{children}</div>
      </main>
    </div>
  );
}

/** Password input with a show/hide toggle that keeps its accessible name. */
export const PasswordInput = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function PasswordInput(props, ref) {
    const [visible, setVisible] = useState(false);
    return (
      <div className="relative">
        <Input ref={ref} {...props} type={visible ? "text" : "password"} className="pr-12" />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          className="absolute right-0.5 top-1/2 inline-flex h-10 w-11 -translate-y-1/2 items-center justify-center rounded-[var(--btn-radius)] text-fg-subtle hover:text-fg"
        >
          {visible ? <EyeOff className="h-4 w-4" aria-hidden="true" /> : <Eye className="h-4 w-4" aria-hidden="true" />}
        </button>
      </div>
    );
  },
);
