"use client";

import { FlaskConical } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { setDemoMode } from "@/lib/demo/mode";
import { t } from "@/lib/i18n/id";

/**
 * "Coba demo" entry point on the public landing page.
 *
 * The flag is set BEFORE navigating, so the first `/auth/me` on the dashboard
 * is already served by the in-browser demo backend.
 *
 * Renders ONLY the button: the caller owns layout and the helper line. A
 * wrapper `<div>` with its own note made three hero CTAs impossible to align
 * on one baseline.
 */
export function DemoCta({
  variant = "default",
  size = "lg",
  className,
  label,
}: {
  variant?: "default" | "outline" | "ghost";
  size?: "sm" | "lg";
  className?: string;
  label?: string;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      className={className}
      disabled={pending}
      onClick={() => {
        setPending(true);
        setDemoMode(true);
        router.push("/dashboard");
      }}
    >
      <FlaskConical className="h-4 w-4" aria-hidden="true" />
      {label ?? t.demo.landingCta}
    </Button>
  );
}