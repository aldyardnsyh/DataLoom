"use client";

import { useEffect } from "react";

import { ErrorState } from "@/components/domain/error-state";

/**
 * Route-level error boundary. In development Next passes a `digest`; we show it
 * as the request id placeholder so support can correlate (PRD 18.4).
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Intentionally minimal: never log user data or tokens here.
  }, [error]);

  return (
    <div className="page-container">
      <ErrorState
        title="Terjadi kesalahan pada halaman"
        message="Silakan coba lagi. Jika masalah berlanjut, hubungi dukungan dengan ID permintaan di bawah."
        requestId={error.digest ?? null}
        onRetry={reset}
      />
    </div>
  );
}
