import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Plus_Jakarta_Sans } from "next/font/google";

import "@xyflow/react/dist/style.css";
import "./globals.css";

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-jakarta",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "DataLoom",
    template: "%s · DataLoom",
  },
  description:
    "DataLoom, platform pipeline data berorkestrasi AI: dari data mentah ke jawaban, otomatis.",
  applicationName: "DataLoom",
  /*
   * Icons are file-based (`app/icon.svg`, `app/apple-icon.svg`) so the browser
   * tab, bookmark list and iOS home screen all show the woven-loom mark instead
   * of a generic page glyph. Declared explicitly as well so the 16px tab icon
   * and the maskable variant are both advertised.
   */
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
    shortcut: [{ url: "/icon.svg", type: "image/svg+xml" }],
    apple: [{ url: "/apple-icon.svg", type: "image/svg+xml" }],
  },
};

export const viewport: Viewport = {
  // Matches --background so mobile chrome blends with the canvas.
  themeColor: "#F7FAFF",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="id" className={`${jakarta.variable} ${jetbrains.variable}`} suppressHydrationWarning>
      <body className="font-sans">
        {/* Skip link: keyboard users reach content in one tab (WCAG 2.1 AA). */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
        >
          Lompat ke konten utama
        </a>
        {children}
      </body>
    </html>
  );
}
