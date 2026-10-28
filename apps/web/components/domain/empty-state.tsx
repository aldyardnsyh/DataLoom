import type { LucideIcon } from "lucide-react";
import { Inbox } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * EmptyState: the single empty pattern for every page (PRD 12.3/12.4).
 * Icon + headline + one sentence + one primary action (plus optional secondary).
 */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  primaryAction,
  secondaryAction,
  className,
  compact = false,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  primaryAction?: { label: string; href?: string; onClick?: () => void; disabled?: boolean; disabledReason?: string };
  secondaryAction?: { label: string; href?: string; onClick?: () => void };
  className?: string;
  /** `compact` is for small panels (tabs, cards) rather than full pages. */
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-card border border-dashed border-border bg-background px-6 text-center",
        compact ? "py-8" : "py-14",
        className,
      )}
    >
      <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground" aria-hidden="true">
        <Icon className="h-6 w-6" />
      </span>
      <h3 className="text-base font-semibold text-foreground">{title}</h3>
      {description ? <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p> : null}

      {primaryAction || secondaryAction ? (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {primaryAction ? (
            <ButtonWithLink action={primaryAction} variant="default" />
          ) : null}
          {secondaryAction ? <ButtonWithLink action={secondaryAction} variant="outline" /> : null}
        </div>
      ) : null}
    </div>
  );
}

function ButtonWithLink({
  action,
  variant,
}: {
  action: { label: string; href?: string; onClick?: () => void; disabled?: boolean; disabledReason?: string };
  variant: "default" | "outline";
}) {
  /*
   * `disabled` on an <a> is inert: the browser ignores it and the link still
   * navigates. Rendering `<Button asChild disabled>` therefore produced a
   * button that LOOKED disabled and still worked. A disabled action with an
   * href is rendered as a real disabled <button> instead (no href, no
   * navigation), and the reason is exposed to both mouse and screen readers.
   */
  if (action.href && !action.disabled) {
    return (
      <Button asChild variant={variant}>
        <Link href={action.href}>{action.label}</Link>
      </Button>
    );
  }
  if (action.href) {
    return (
      <Button variant={variant} disabled title={action.disabledReason} aria-disabled="true">
        {action.label}
      </Button>
    );
  }
  return (
    <Button variant={variant} onClick={action.onClick} disabled={action.disabled} title={action.disabledReason}>
      {action.label}
    </Button>
  );
}

