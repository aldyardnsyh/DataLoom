"use client";

import Link from "next/link";

import { EmptyState } from "@/components/domain/empty-state";
import { LoadingAnnouncer } from "@/components/domain/state-loading";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Runs index loading skeleton. A dedicated `loading.tsx` guarantees the route
 * shows a layout-matching skeleton during navigation instead of a blank flash
 * (PRD 12.3 requires a loading state on every page).
 */
export default function RunsLoading() {
  return (
    <div className="page-container space-y-4">
      <LoadingAnnouncer />
      <Skeleton className="h-7 w-40" />
      <Skeleton className="h-4 w-72" />
      <Skeleton className="h-10 w-44" />
      <Skeleton className="h-80 w-full" />
    </div>
  );
}
