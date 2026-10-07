import { cn } from "@/lib/cn";

type CardProps = React.HTMLAttributes<HTMLElement> & {
  /** "solid" for calm data surfaces; "glass" for the cinematic layer. */
  tone?: "solid" | "glass";
  as?: "div" | "section" | "article" | "li";
};

export function Card({ tone = "solid", as: Tag = "div", className, ...props }: CardProps) {
  return (
    <Tag
      className={cn(
        "relative rounded-[var(--card-radius)]",
        tone === "solid"
          ? "border border-[var(--card-border)] bg-[var(--card-bg)]"
          : "td-glass td-rim",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 p-5 sm:p-6", className)} {...props} />;
}

export function CardTitle({
  className,
  as: Tag = "h2",
  ...props
}: React.HTMLAttributes<HTMLHeadingElement> & { as?: "h2" | "h3" | "h4" }) {
  return <Tag className={cn("text-base font-semibold tracking-tight text-fg", className)} {...props} />;
}

export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-sm text-fg-muted", className)} {...props} />;
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 pb-5 sm:px-6 sm:pb-6", className)} {...props} />;
}

export function CardFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex flex-wrap items-center gap-2 border-t border-line px-5 py-4 sm:px-6", className)}
      {...props}
    />
  );
}
