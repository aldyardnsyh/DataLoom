import { ArrowRight, Ban, ShieldCheck, GitBranch } from "lucide-react";
import Link from "next/link";

import { t } from "@/lib/i18n/id";

/**
 * The "why this exists" section.
 *
 * Positioning decision (D-31): DataLoom does not sell data cleaning - that is a
 * commodity every spreadsheet and AI-cleaner already does. It sells the three
 * things those tools structurally cannot give:
 *   1. Rejection with a recorded reason (quarantine, not silent deletion).
 *   2. A reconciliation gate before data reaches consumers.
 *   3. Rules that change through versions + human approval + audit.
 *
 * Each pillar leads with what the alternative fails to give, then names the
 * exact screen in the product where the proof lives - so a visitor can verify
 * the claim instead of trusting the copy.
 */

const PILLAR_ICONS = [Ban, ShieldCheck, GitBranch];

export function ValueSection() {
  return (
    <section id="nilai-produk" className="scroll-mt-16 border-t border-border">
      <div className="mx-auto w-full max-w-6xl px-4 py-14 md:px-6 md:py-20">
        <div className="max-w-3xl">
          <h2 className="text-xl font-bold tracking-tight text-foreground md:text-2xl">
            {t.landing.value.title}
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            {t.landing.value.body}
          </p>
        </div>

        <ol className="mt-10 space-y-4">
          {t.landing.value.pillars.map((pillar, index) => {
            const Icon = PILLAR_ICONS[index] ?? ShieldCheck;
            return (
              <li
                key={pillar.kicker}
                className="rounded-card border border-border bg-background p-5 shadow-card"
              >
                <div className="flex flex-wrap items-start gap-4">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
                    <Icon className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="font-mono text-[11px] font-semibold uppercase tracking-wide text-primary">
                      {pillar.kicker}
                    </p>
                    <h3 className="mt-1 text-base font-semibold text-foreground">
                      {pillar.title}
                    </h3>

                    {/* The alternative first: the reader needs the contrast
                        before the claim makes sense. */}
                    <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
                      <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 rotate-180" aria-hidden="true" />
                      <span>{pillar.contrast}</span>
                    </p>

                    <p className="mt-2 max-w-3xl text-sm leading-relaxed text-foreground">
                      {pillar.body}
                    </p>

                    <p className="mt-3 inline-flex flex-wrap items-center gap-1.5 rounded-lg border border-primary/30 bg-accent px-2.5 py-1 text-[11px] font-medium text-accent-foreground">
                      {pillar.proof}
                    </p>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>

        <p className="mt-8 max-w-3xl rounded-card border border-border bg-muted px-4 py-3 text-sm text-muted-foreground">
          {t.landing.value.closing}
        </p>

        <p className="mt-4 text-sm text-muted-foreground">
          {t.landing.value.demoLinks}{" "}
          <Link
            href="/data-contract?mode=demo"
            className="inline-flex min-h-6 items-center font-medium text-foreground underline underline-offset-4 hover:text-primary"
          >
            {t.landing.value.demoContract}
          </Link>{" "}
          {t.landing.value.demoAnd}{" "}
          <Link
            href="/data?mode=demo"
            className="inline-flex min-h-6 items-center font-medium text-foreground underline underline-offset-4 hover:text-primary"
          >
            {t.landing.value.demoQuarantine}
          </Link>
          .
        </p>
      </div>
    </section>
  );
}

