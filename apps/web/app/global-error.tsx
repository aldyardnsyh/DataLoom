"use client";

/**
 * Root error boundary. Catches errors thrown by the root layout itself
 * (font loading, provider crashes), which route-level boundaries cannot.
 * It renders its own <html>/<body> because the layout tree is not available.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="id">
      <body
        style={{
          margin: 0,
          minHeight: "100dvh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#FFFFFF",
          color: "#0F172A",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <div style={{ maxWidth: 420, padding: 24, textAlign: "center" }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>
            Terjadi kesalahan pada aplikasi
          </h1>
          <p style={{ fontSize: 14, color: "#5E6A79", margin: "8px 0 0" }}>
            Muat ulang halaman. Jika masalah berlanjut, tunggu sebentar lalu coba lagi.
          </p>
          {error.digest ? (
            <p style={{ fontFamily: "monospace", fontSize: 12, color: "#5E6A79" }}>
              ID: {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 20,
              padding: "8px 16px",
              borderRadius: 8,
              border: "1px solid #D6DFEC",
              background: "#1A66D1",
              color: "#FFFFFF",
              fontSize: 14,
              cursor: "pointer",
            }}
          >
            Muat ulang
          </button>
        </div>
      </body>
    </html>
  );
}
