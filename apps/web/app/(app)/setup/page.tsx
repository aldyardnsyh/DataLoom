"use client";

import { SetupWizard } from "@/components/domain/setup-wizard";
import { t } from "@/lib/i18n/id";

export default function SetupPage() {
  return (
    <div className="page-container space-y-4">
      <header>
        <h1 className="text-xl font-bold tracking-tight">{t.setup.title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{t.setup.subtitle}</p>
      </header>
      <SetupWizard />
    </div>
  );
}
