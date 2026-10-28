import { cn } from "@/lib/utils";

/**
 * Skeleton is the ONLY loading primitive. It mirrors the real layout
 * (PRD 12.3 requires a loading state on every page).
 */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("shimmer rounded-md", className)} aria-hidden="true" {...props} />;
}

export { Skeleton };
