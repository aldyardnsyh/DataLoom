"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { AuthBrandPanel } from "@/app/(auth)/brand-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSession } from "@/components/providers/session-provider";
import { ApiClientError } from "@/lib/api";
import { BrandMark } from "@/components/domain/brand-mark";
import { t } from "@/lib/i18n/id";
import { cn } from "@/lib/utils";

/**
 * Login page (PRD 18.3 P1).
 * States: idle / submitting (disabled + spinner) / inline error below password.
 * The error area is rendered with role="alert" for screen readers.
 */
function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { login, session } = useSession();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{ message: string; requestId: string } | null>(null);

  // A user who opens /login while already signed in should land in the app,
  // not stare at a form. Only an *existing* session triggers this (never the
  // one produced by the submit below, which navigates on its own).
  useEffect(() => {
    if (session && !pending) {
      const next = params.get("next");
      router.replace(next && next.startsWith("/") ? next : "/dashboard");
    }
  }, [session, pending, params, router]);

  const disabled = pending || email.trim() === "" || password === "";

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await login(email.trim(), password);
      const next = params.get("next");
      router.replace(next && next.startsWith("/") ? next : "/dashboard");
    } catch (caught) {
      if (caught instanceof ApiClientError) {
        setError({ message: caught.message, requestId: caught.request_id });
      } else {
        setError({ message: t.errors.generic, requestId: "unknown" });
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex min-h-dvh">
      <div className="flex w-full flex-col justify-center px-6 py-10 lg:w-1/2 lg:px-16">
        <div className="mx-auto w-full max-w-sm">
          <Link href="/login" className="inline-flex items-center gap-2">
            <BrandMark size={32} className="shrink-0" />
            <span className="text-base font-bold tracking-tight">{t.app.name}</span>
          </Link>

          <h1 className="mt-8 text-xl font-bold tracking-tight">{t.auth.welcomeBack}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.auth.loginSubtitle}</p>

          <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="email">{t.auth.email}</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={pending}
                placeholder="nama@perusahaan.com"
              />
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">{t.auth.password}</Label>
                {/*
                  Password reset is not part of the MVP, so there is no dead
                  button here (R-26). The honest alternative is a plain note
                  that tells the user who can help, instead of a control that
                  silently does nothing.
                */}
                <span className="text-xs text-muted-foreground">{t.auth.forgot}</span>
              </div>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={pending}
                  aria-invalid={error !== null}
                  aria-describedby={error ? "login-error" : undefined}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((current) => !current)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={showPassword ? t.auth.hidePassword : t.auth.showPassword}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>

              {/* Inline error area below the password field (PRD P1). */}
              <div
                id="login-error"
                role="alert"
                aria-live="polite"
                className={cn("min-h-[1.25rem] text-xs", error ? "text-destructive-text" : "text-transparent")}
              >
                {error ? (
                  <>
                    <span className="font-medium">{error.message}</span>
                    <span className="ml-1 font-mono text-[11px] text-muted-foreground">
                      ({error.requestId})
                    </span>
                  </>
                ) : (
                  "placeholder"
                )}
              </div>
            </div>

            <Button type="submit" className="w-full" disabled={disabled} title={pending ? "Memproses" : undefined}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {pending ? t.auth.signingIn : t.auth.signIn}
            </Button>
          </form>

          <div className="mt-6 flex items-center gap-3">
            <span className="h-px flex-1 bg-border" aria-hidden="true" />
            <span className="text-xs text-muted-foreground">atau</span>
            <span className="h-px flex-1 bg-border" aria-hidden="true" />
          </div>

          <p className="mt-4 text-center text-sm text-muted-foreground">
            {t.auth.noAccount}{" "}
            <Link href="/register" className="inline-flex min-h-9 items-center font-medium text-primary underline-offset-4 hover:underline">
              {t.auth.createAccount}
            </Link>
          </p>
        </div>
      </div>

      <AuthBrandPanel />
    </div>
  );
}
/**
 * useSearchParams requires a Suspense boundary in the App Router, otherwise
 * the whole route becomes client-rendered with a build warning.
 */
export default function LoginPage() {
  return (
    <Suspense fallback={<div className="flex min-h-dvh items-center justify-center text-sm text-muted-foreground">Memuat…</div>}>
      <LoginForm />
    </Suspense>
  );
}

