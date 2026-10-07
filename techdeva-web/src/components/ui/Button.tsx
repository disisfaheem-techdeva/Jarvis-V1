import Link from "next/link";
import { forwardRef } from "react";

import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "approve";
export type ButtonSize = "sm" | "md" | "lg";

const base =
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-[var(--btn-radius)] font-medium " +
  "transition-[background-color,border-color,color,box-shadow,transform] duration-150 ease-out " +
  "focus-visible:outline-none focus-visible:[box-shadow:var(--focus-ring)] " +
  "active:translate-y-px disabled:pointer-events-none disabled:opacity-50 cursor-pointer select-none";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-on-accent hover:bg-accent-hover shadow-[0_0_0_1px_rgb(255_255_255/0.08)_inset,0_8px_24px_-12px_var(--accent-glow)]",
  secondary: "border border-line-strong bg-surface-2 text-fg hover:bg-surface-3",
  ghost: "text-fg-muted hover:bg-surface-2 hover:text-fg",
  danger: "border border-line-strong bg-transparent text-danger hover:bg-danger-tint hover:border-danger",
  approve: "bg-success-fill text-white hover:brightness-110",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-[var(--btn-h-sm)] px-3 text-sm",
  md: "h-[var(--btn-h-md)] px-4 text-sm",
  lg: "h-[var(--btn-h-lg)] px-6 text-base",
};

export function buttonClass(variant: ButtonVariant = "primary", size: ButtonSize = "md", extra?: string) {
  return cn(base, variants[variant], sizes[size], extra);
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className, type = "button", ...props },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...props} />;
});

type ButtonLinkProps = React.ComponentProps<typeof Link> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

/** A link styled as a button — for navigation, never for actions. */
export function ButtonLink({ variant = "primary", size = "md", className, ...props }: ButtonLinkProps) {
  return <Link className={buttonClass(variant, size, className)} {...props} />;
}
