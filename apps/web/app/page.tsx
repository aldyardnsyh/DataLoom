import type { Metadata } from "next";
import Link from "next/link";
import {
  CheckCircle2,
  CircleDashed,
  Database,
  Gauge,
  MessagesSquare,
  ShieldCheck,
  Workflow,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { ValueSection } from "@/components/domain/value-section";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/domain/brand-mark";
import { DemoCta } from "@/components/domain/demo-cta";
import { t } from "@/lib/i18n/id";

export const metadata: Metadata = {
  title: { absolute: t.landing.metaTitle },
  description: t.landing.metaDescription,
};

const NAV = [
  { href: "#nilai-produk", label: t.landing.nav.value },
  { href: "#alur-kerja", label: t.landing.nav.workflow },
  { href: "#kemampuan", label: t.landing.nav.capabilities },
  { href: "#batas", label: t.landing.nav.limits },
];

/** The six node types the canvas actually ships, in execution order. */
const RUN_NODES = [
  { id: "n_source", type: "source", label: "Webhook Pesanan", meta: "bronze", status: "succeeded" },
  { id: "n_validate", type: "validate", label: "Validasi", meta: "2 aturan", status: "succeeded" },
  { id: "n_clean", type: "clean", label: "Bersihkan", meta: "parse_date", status: "succeeded" },
  { id: "n_dedup", type: "transform", label: "Deduplikasi", meta: "order_id", status: "running" },
  { id: "n_agg", type: "aggregate", label: "Agregasi", meta: "sum per bulan", status: "pending" },
  { id: "n_sink", type: "sink", label: "Gudang Data", meta: "gold_penjualan_bulanan", status: "pending" },
] as const;

const GATE_ITEMS = [
  { rule: "schema_valid", decision: "pass", evidence: "Skema silver lolos validasi." },
  { rule: "quarantine_ratio", decision: "pass", evidence: "Rasio karantina di bawah ambang proyek." },
  { rule: "reconciliation", decision: "pass", evidence: "Jumlah baris silver dan gold cocok." },
  { rule: "pending_proposals", decision: "wait", evidence: "Satu usulan agent menunggu keputusan." },
] as const;

const CAPABILITY_ICONS = [Database, Workflow, Gauge, MessagesSquare, ShieldCheck, CheckCircle2];

function StatusMark({ status }: { status: "succeeded" | "running" | "pending" }) {
  if (status === "succeeded") {
    return <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />;
  }
  if (status === "running") {
    return <CircleDashed className="h-4 w-4 text-info" aria-hidden="true" />;
  }
  return <CircleDashed className="h-4 w-4 text-muted-foreground" aria-hidden="true" />;
}

function RunSchematic() {
  return (
    <figure className="rounded-card border border-border bg-background shadow-card">
      <figcaption className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-foreground">Penjualan Harian</p>
          <p className="font-mono text-[11px] text-muted-foreground">run-3 · v3 · manual</p>
        </div>
        <Badge variant="info" className="gap-1">
          <CircleDashed className="h-3 w-3" aria-hidden="true" />
          {t.status.running}
        </Badge>
      </figcaption>

      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <ol className="space-y-1.5" aria-label="Node yang dieksekusi">
          {RUN_NODES.map((node) => (
            <li
              key={node.id}
              className="flex items-center gap-2.5 rounded-lg border border-border px-2.5 py-2"
            >
              <StatusMark status={node.status} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-foreground">{node.label}</p>
                <p className="truncate font-mono text-[10px] text-muted-foreground">{node.meta}</p>
              </div>
              <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                {node.type}
              </span>
            </li>
          ))}
        </ol>

        <div className="rounded-lg border border-border bg-muted/60 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <ShieldCheck className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            {t.landing.hero.gateTitle}
          </p>
          <ul className="mt-2 space-y-2">
            {GATE_ITEMS.map((item) => (
              <li key={item.rule} className="text-[11px]">
                <p className="flex items-center gap-1.5 font-mono font-medium text-foreground">
                  {item.decision === "wait" ? (
                    <CircleDashed className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
                  ) : (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />
                  )}
                  {item.rule}
                </p>
                <p className="pl-5 text-muted-foreground">{item.evidence}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <p className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
        {t.landing.hero.diagramCaption}
      </p>
    </figure>
  );
}

export default function LandingPage() {
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <header className="sticky top-0 z-30 border-b border-border bg-background">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-4 px-4 md:px-6">
          <Link
            href="/"
            className="flex items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="DataLoom, halaman utama"
          >
            <BrandMark size={28} className="shrink-0" />
            <span className="text-sm font-bold tracking-tight text-foreground">{t.app.name}</span>
          </Link>

          <nav aria-label="Navigasi halaman" className="hidden items-center gap-5 md:flex">
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="inline-flex min-h-9 items-center text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                {item.label}
              </a>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1.5">
            <Button asChild variant="ghost" size="sm">
              <Link href="/login">{t.landing.nav.signIn}</Link>
            </Button>
            <Button asChild size="sm">
              <Link href="/register">{t.landing.nav.signUp}</Link>
            </Button>
          </div>
        </div>
      </header>

      <main id="main-content" className="flex-1">
        {/* Hero: the claim is the product's own behaviour, not a slogan. */}
        <section className="mx-auto w-full max-w-6xl px-4 pb-14 pt-12 md:px-6 md:pb-20 md:pt-16">
          <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-12">
            <div>
              <h1 className="max-w-2xl text-[28px] font-bold leading-[1.15] tracking-tight text-foreground sm:text-2xl sm:leading-tight lg:text-[34px]">
                {t.landing.hero.title}
              </h1>
              <p className="mt-4 max-w-xl text-sm leading-relaxed text-muted-foreground sm:text-base">
                {t.landing.hero.body}
              </p>

              {/*
                Two CTAs, not three. A third same-weight button in the hero
                made the row read as a toolbar instead of a choice: the demo is
                the lowest-friction path, so it takes primary weight, signup is
                the secondary, and "Masuk" drops to a quiet text link for the
                people who already have an account.
              */}
              <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:items-center">
                <DemoCta className="h-11 px-5" />
                <Button asChild size="lg" variant="outline" className="h-11 px-5">
                  <Link href="/register">{t.landing.hero.secondaryCta}</Link>
                </Button>
              </div>

              <p className="mt-3 text-xs text-muted-foreground">
                {t.landing.hero.haveAccount}{" "}
                <Link
                  href="/login"
                  className="inline-flex min-h-6 items-center font-medium text-foreground underline underline-offset-4 hover:text-primary"
                >
                  {t.landing.nav.signIn}
                </Link>
              </p>
              <p className="mt-1 text-[11px] text-muted-foreground">{t.demo.landingNote}</p>

              <ul className="mt-8 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3 sm:gap-3">
                {t.landing.hero.facts.map((fact) => (
                  <li key={fact} className="flex items-start gap-1.5">
                    <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />
                    {fact}
                  </li>
                ))}
              </ul>
            </div>

            <RunSchematic />
          </div>
        </section>

        {/* Value proposition: why this exists when cleaning is a commodity. */}
        <ValueSection />

        {/* Workflow: numbered because the layer order carries meaning. */}
        <section id="alur-kerja" className="scroll-mt-16 border-t border-border bg-muted/50">
          <div className="mx-auto w-full max-w-6xl px-4 py-14 md:px-6 md:py-20">
            <div className="grid gap-10 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:gap-16">
              <div>
                <h2 className="max-w-md text-xl font-bold tracking-tight text-foreground md:text-2xl">
                  {t.landing.workflow.title}
                </h2>
                <p className="mt-3 max-w-md text-sm leading-relaxed text-muted-foreground">
                  {t.landing.workflow.body}
                </p>
                <p className="mt-6 font-mono text-[11px] text-muted-foreground">
                  sumber → bronze → silver → gold
                </p>
              </div>

              <ol className="relative space-y-0">
                {t.landing.workflow.steps.map((step, index) => {
                  const isLast = index === t.landing.workflow.steps.length - 1;
                  return (
                    <li key={step.title} className="relative flex gap-4 pb-8 last:pb-0">
                      {!isLast ? (
                        <span
                          className="absolute left-[13px] top-7 h-full w-px bg-border"
                          aria-hidden="true"
                        />
                      ) : null}
                      <span
                        className="relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-background font-mono text-xs font-semibold text-foreground tabular"
                        aria-hidden="true"
                      >
                        {index + 1}
                      </span>
                      <div className="min-w-0 pt-0.5">
                        <h3 className="text-sm font-semibold text-foreground">{step.title}</h3>
                        <p className="mt-1 max-w-lg text-sm leading-relaxed text-muted-foreground">
                          {step.body}
                        </p>
                      </div>
                    </li>
                  );
                })}
              </ol>
            </div>
          </div>
        </section>

        {/* Capabilities: one flagship surface, then a compact reference list. */}
        <section id="kemampuan" className="scroll-mt-16 border-t border-border">
          <div className="mx-auto w-full max-w-6xl px-4 py-14 md:px-6 md:py-20">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="text-xl font-bold tracking-tight text-foreground md:text-2xl">
                  {t.landing.capabilities.title}
                </h2>
                <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                  {t.landing.capabilities.body}
                </p>
              </div>
            </div>

            <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
              {/* Flagship: approval gate, shown as a real proposal diff. */}
              <article className="rounded-card border border-border bg-background p-5 shadow-card">
                <div className="flex items-start gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground">
                    <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div>
                    <h3 className="text-sm font-semibold text-foreground">
                      {t.landing.capabilities.agentTitle}
                    </h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                      {t.landing.capabilities.agentBody}
                    </p>
                  </div>
                </div>

                <div className="mt-4 rounded-lg border border-border">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
                    <p className="font-mono text-[11px] text-foreground">
                      drop_if <span className="text-muted-foreground">pada</span> order_id
                    </p>
                    <Badge variant="warning" className="gap-1">
                      <CircleDashed className="h-3 w-3" aria-hidden="true" />
                      {t.landing.capabilities.agentDecision}
                    </Badge>
                  </div>
                  <div className="grid grid-cols-2 divide-x divide-border text-[11px]">
                    <div className="p-3">
                      <p className="font-medium text-muted-foreground">
                        {t.landing.capabilities.agentDiffBefore}
                      </p>
                      <p className="mt-1 font-mono text-foreground">(kosong)</p>
                    </div>
                    <div className="p-3">
                      <p className="font-medium text-muted-foreground">
                        {t.landing.capabilities.agentDiffAfter}
                      </p>
                      <p className="mt-1 flex items-center gap-1.5 font-mono text-foreground">
                        <XCircle className="h-3.5 w-3.5 text-destructive" aria-hidden="true" />
                        baris dibuang
                      </p>
                    </div>
                  </div>
                  <p className="border-t border-border px-3 py-2 text-[11px] text-warning-text">
                    {t.landing.capabilities.agentRisk}
                  </p>
                </div>
              </article>

              <dl className="grid gap-x-6 gap-y-5 sm:grid-cols-2">
                {t.landing.capabilities.items.map((item, index) => {
                  const Icon = CAPABILITY_ICONS[index] ?? CheckCircle2;
                  return (
                    <div key={item.title} className="border-t border-border pt-3">
                      <dt className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                        <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                        {item.title}
                      </dt>
                      <dd className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        {item.body}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </div>
          </div>
        </section>

        {/* Limits: the honest section most product pages skip. */}
        <section id="batas" className="scroll-mt-16 border-t border-border bg-muted/50">
          <div className="mx-auto w-full max-w-6xl px-4 py-14 md:px-6 md:py-20">
            <div className="grid gap-8 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:gap-16">
              <div>
                <h2 className="text-xl font-bold tracking-tight text-foreground md:text-2xl">
                  {t.landing.limits.title}
                </h2>
                <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
                  {t.landing.limits.body}
                </p>
              </div>
              <div>
                <ul className="divide-y divide-border border-y border-border">
                  {t.landing.limits.items.map((item) => (
                    <li key={item} className="flex items-start gap-2.5 py-3 text-sm text-foreground">
                      <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                      <span className="leading-relaxed">{item}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-4 text-xs text-muted-foreground">{t.landing.limits.note}</p>
              </div>
            </div>
          </div>
        </section>

        <section className="border-t border-border">
          <div className="mx-auto flex w-full max-w-6xl flex-col items-start justify-between gap-5 px-4 py-12 md:flex-row md:items-center md:px-6 md:py-14">
            <div>
              <h2 className="text-xl font-bold tracking-tight text-foreground">
                {t.landing.cta.title}
              </h2>
              <p className="mt-1.5 text-sm text-muted-foreground">{t.landing.cta.body}</p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <DemoCta className="h-11 px-5" />
              <Button asChild size="lg" variant="outline" className="h-11 px-5">
                <Link href="/register">{t.landing.cta.primary}</Link>
              </Button>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between md:px-6">
          <div className="flex items-center gap-2">
            <BrandMark size={20} className="shrink-0" />
            <span className="font-medium text-foreground">{t.app.name}</span>
            <span aria-hidden="true">·</span>
            <span>{t.landing.footer.rights}</span>
          </div>
          <div className="flex flex-col gap-1.5 sm:items-end">
            <div className="flex flex-wrap items-center gap-4">
              <span>{t.landing.footer.note}</span>
              <Link href="/login" className="inline-flex min-h-6 items-center hover:text-foreground">
                {t.landing.nav.signIn}
              </Link>
              <Link href="/register" className="inline-flex min-h-6 items-center hover:text-foreground">
                {t.landing.nav.signUp}
              </Link>
            </div>
            <p className="max-w-xl text-[11px] leading-relaxed text-muted-foreground">
              {t.landing.footer.copyright}
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
