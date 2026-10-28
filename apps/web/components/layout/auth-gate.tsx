"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useSession } from "@/components/providers/session-provider";
import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { LoadingAnnouncer, PageHeaderSkeleton } from "@/components/domain/state-loading";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/lib/i18n/id";

const PUBLIC_PREFIXES = ["/login", "/register"];

/**
 * AuthGate redirects unauthenticated users to /login and shows a skeleton while
 * the session is being resolved (never a flash of protected content).
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { session, loading, error, refresh } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));

  useEffect(() => {
    // Only bounce to /login for a *confirmed* signed-out state. A network
    // failure must never look like "you are logged out", or the user will
    // re-enter credentials against a dead connection.
    if (!loading && !session && !error && !isPublic) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [loading, session, error, isPublic, pathname, router]);

  if (isPublic) return <>{children}</>;

  if (loading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer label="Memeriksa sesi" />
        <PageHeaderSkeleton />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-container">
        <ErrorState
          message={error.message}
          requestId={error.request_id}
          onRetry={() => void refresh()}
        />
      </div>
    );
  }

  if (!session) {
    return (
      <div className="page-container">
        <EmptyState
          title="Sesi tidak ditemukan"
          description="Silakan masuk untuk melanjutkan."
          primaryAction={{ label: "Masuk", href: "/login" }}
        />
      </div>
    );
  }

  return <>{children}</>;
}

/** Global command palette (search across pages). Wired to the topbar button. */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const items = [
    { href: "/dashboard", label: t.nav.dashboard },
    { href: "/projects", label: t.nav.projects },
    { href: "/sources", label: t.nav.sources },
    { href: "/pipelines", label: t.nav.pipelines },
    { href: "/runs", label: t.nav.runs },
    { href: "/data", label: t.nav.explorer },
    { href: "/data-contract", label: t.nav.contract },
    { href: "/agents", label: t.nav.agents },
    { href: "/ask", label: t.nav.ask },
    { href: "/usage", label: t.nav.usage },
    { href: "/settings", label: t.nav.settings },
  ];

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder={t.common.searchPlaceholder} />
      <CommandList>
        <CommandEmpty>Tidak ada hasil.</CommandEmpty>
        <CommandGroup heading="Navigasi">
          {items.map((item) => (
            <CommandItem
              key={item.href}
              value={item.label}
              onSelect={() => {
                onOpenChange(false);
                router.push(item.href);
              }}
            >
              <Link href={item.href}>{item.label}</Link>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
