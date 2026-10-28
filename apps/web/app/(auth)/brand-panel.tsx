import { ArrowRight, Database, Sparkles, Warehouse } from "lucide-react";

import { BrandMark } from "@/components/domain/brand-mark";
import { t } from "@/lib/i18n/id";

/**
 * Right-hand brand panel for auth screens: soft blue tint, a simple
 * schematic pipeline. No gradients, no glassmorphism (PRD 12.1/18.3).
 */
export function AuthBrandPanel() {
  const steps = [
    { icon: Database, label: "Sumber", hint: "CSV · Webhook · API" },
    { icon: Sparkles, label: "Bersihkan", hint: "Silver" },
    { icon: Warehouse, label: "Gudang data", hint: "Gold" },
  ];

  return (
    <aside className="hidden flex-1 flex-col justify-center bg-accent px-10 lg:flex" aria-hidden="true">
      <div className="mx-auto w-full max-w-md">
        <div className="flex items-center gap-2">
          <BrandMark size={30} className="shrink-0" />
          <h2 className="text-xl font-bold text-accent-foreground">{t.app.name}</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{t.app.tagline}</p>

        <ol className="mt-10 flex items-center gap-2">
          {steps.map((step, index) => {
            const Icon = step.icon;
            return (
              <li key={step.label} className="flex flex-1 items-center gap-2">
                <div className="flex-1 rounded-card border border-border bg-background p-3 shadow-card">
                  <Icon className="h-4 w-4 text-primary" />
                  <p className="mt-2 text-xs font-semibold text-foreground">{step.label}</p>
                  <p className="text-[11px] text-muted-foreground">{step.hint}</p>
                </div>
                {index < steps.length - 1 ? (
                  <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                ) : null}
              </li>
            );
          })}
        </ol>

        <div className="mt-10 rounded-card border border-border bg-background p-4 shadow-card">
          <p className="text-xs font-medium text-muted-foreground">Jalur data panas 100% deterministik.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            LLM hanya dipakai untuk membantu menyusun aturan, dan tidak pernah tanpa persetujuan Anda.
          </p>
        </div>
      </div>
    </aside>
  );
}
