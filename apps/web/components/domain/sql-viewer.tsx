"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { copyToClipboard } from "@/lib/format";
import { t } from "@/lib/i18n/id";

/**
 * SqlViewer: monospace SQL block with line numbers and a copy action.
 * JetBrains Mono only for SQL (PRD 12.1).
 */
export function SqlViewer({
  sql,
  className,
  showCopy = true,
  maxHeight = 320,
}: {
  sql: string | null | undefined;
  className?: string;
  showCopy?: boolean;
  maxHeight?: number;
}) {
  const [copied, setCopied] = useState(false);

  if (!sql) {
    return (
      <div className={cn("console p-3 text-muted-foreground", className)}>
        SQL belum tersedia.
      </div>
    );
  }

  const lines = sql.split("\n");

  const handleCopy = async () => {
    const ok = await copyToClipboard(sql);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className={cn("relative overflow-hidden rounded-lg border border-border bg-muted", className)}>
      <div className="flex items-center justify-between border-b border-border bg-background px-3 py-1.5">
        <span className="font-mono text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          SQL
        </span>
        {showCopy ? (
          <Button variant="ghost" size="sm" onClick={handleCopy} aria-live="polite">
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? t.common.copied : t.ask.copySql}
          </Button>
        ) : null}
      </div>
      <pre
        className="overflow-auto p-3 font-mono text-xs leading-relaxed text-foreground"
        style={{ maxHeight }}
      >
        <code>
          {lines.map((line, index) => (
            <span key={index} className="grid grid-cols-[2.5rem_1fr]">
              <span className="select-none text-right pr-3 text-muted-foreground/70" aria-hidden="true">
                {index + 1}
              </span>
              <span className="whitespace-pre-wrap break-words">{line || " "}</span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
