"use client";

import { FlaskConical, RotateCcw, X } from "lucide-react";

import { useDemo } from "@/components/providers/demo-provider";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n/id";

/**
 * Persistent demo banner.
 *
 * Rendered above the topbar for the whole app segment. Three things must be
 * unmistakable while a visitor is in demo mode, so nothing here is subtle:
 *
 * 1. **That it is a demo** — colour plus icon plus the words "Mode demo";
 *    colour alone would fail both WCAG and a colour-blind visitor.
 * 2. **That data is fake** — the banner names sample entities explicitly.
 * 3. **The way out** — "Keluar dari demo" is a real button, not a dead label.
 *
 * It also offers "Kembalikan data demo", because a visitor who deletes all the
 * fixtures deserves a one-click way back to a full workspace.
 */
export function DemoBanner() {
  const { demo, exitDemo, resetDemo } = useDemo();
  if (!demo) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="border-b border-warning/40 bg-warning/10 px-4 py-2 text-foreground"
    >
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-3 gap-y-2">
        <span className="inline-flex items-center gap-1.5 rounded-md border border-warning/50 bg-background px-2 py-0.5 text-[11px] font-semibold text-warning-text">
          <FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />
          {t.demo.badge}
        </span>

        <p className="min-w-0 flex-1 text-xs leading-5 text-foreground">
          <span className="font-semibold">{t.demo.bannerTitle}. </span>
          {t.demo.bannerBody}
          <span className="mt-0.5 block text-[11px] text-muted-foreground">{t.demo.bannerDataNote}</span>
        </p>

        <div className="flex shrink-0 items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={resetDemo}
            title={t.demo.resetHint}
            className="h-7 gap-1.5 text-xs"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            {t.demo.reset}
          </Button>
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={exitDemo}
            title={t.demo.exitHint}
            className="h-7 gap-1.5 text-xs"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            {t.demo.exit}
          </Button>
        </div>
      </div>
    </div>
  );
}
