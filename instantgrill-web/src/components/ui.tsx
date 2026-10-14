import Link from "next/link";
import type { ComponentProps } from "react";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full font-medium transition-[transform,background-color,color,box-shadow] duration-300 ease-[var(--ease-out-soft)] active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50";

const variants = {
  primary:
    "bg-ember-500 text-coal-950 hover:bg-ember-400 shadow-[inset_0_1px_0_rgb(255_255_255/0.25),0_10px_30px_-10px_rgb(242_101_42/0.6)]",
  ghost: "border border-coal-600 text-ash-100 hover:border-ash-400 hover:bg-coal-800",
};

const sizes = {
  md: "h-11 px-5 text-sm",
  lg: "h-13 px-7 text-base",
};

type Variant = keyof typeof variants;
type Size = keyof typeof sizes;

export function buttonClass(variant: Variant = "primary", size: Size = "md", extra = "") {
  return `${base} ${variants[variant]} ${sizes[size]} ${extra}`;
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ComponentProps<"button"> & { variant?: Variant; size?: Size }) {
  return <button className={buttonClass(variant, size, className)} {...props} />;
}

export function ButtonLink({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant; size?: Size }) {
  return <Link className={buttonClass(variant, size, className)} {...props} />;
}

export function Container({ className = "", ...props }: ComponentProps<"div">) {
  return <div className={`mx-auto w-full max-w-[1280px] px-4 sm:px-6 lg:px-10 ${className}`} {...props} />;
}
