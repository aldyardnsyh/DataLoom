import type { Metadata } from "next";
import Link from "next/link";
import { FileQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n/id";

export const metadata: Metadata = { title: "Halaman tidak ditemukan" };

/**
 * Root 404. Lives outside the app shell, so it must stand alone but still
 * offer both doors: the landing page (public) and the dashboard (which the
 * auth gate turns into /login when there is no session).
 */
export default function NotFound() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center px-4 py-16 text-center">
      <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground" aria-hidden="true">
        <FileQuestion className="h-6 w-6" />
      </span>
      <h1 className="text-xl font-bold tracking-tight text-foreground">
        Halaman tidak ditemukan
      </h1>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">
        Alamat yang Anda buka tidak tersedia atau sudah dipindahkan. Periksa kembali tautannya, atau lanjutkan dari salah satu pintu di bawah.
      </p>
      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        <Button asChild>
          <Link href="/dashboard">{t.nav.dashboard}</Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/">{t.app.name}</Link>
        </Button>
      </div>
    </div>
  );
}
