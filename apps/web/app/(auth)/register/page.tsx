"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { AuthBrandPanel } from "@/app/(auth)/brand-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSession } from "@/components/providers/session-provider";
import { ApiClientError } from "@/lib/api";
import { BrandMark } from "@/components/domain/brand-mark";
import { t } from "@/lib/i18n/id";

/**
 * Register page. Creates the user AND their organization (D-01) in one call.
 * Client-side checks mirror the contract (password >= 10 chars) so the user
 * gets immediate feedback, while the server remains the authority.
 */
export default function RegisterPage() {
  const router = useRouter();
  const { register, session } = useSession();

  const [name, setName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{ message: string; requestId: string } | null>(null);

  // Signed-in users do not need the sign-up form (same rule as /login).
  useEffect(() => {
    if (session && !pending) router.replace("/dashboard");
  }, [session, pending, router]);

  const passwordTooShort = password.length > 0 && password.length < 10;
  const disabled =
    pending ||
    name.trim() === "" ||
    orgName.trim() === "" ||
    email.trim() === "" ||
    password.length < 10;

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await register({
        name: name.trim(),
        org_name: orgName.trim(),
        email: email.trim(),
        password,
      });
      router.replace("/dashboard");
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

          <h1 className="mt-8 text-xl font-bold tracking-tight">{t.auth.signupTitle}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.auth.signupSubtitle}</p>

          <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="name">{t.auth.fullName}</Label>
              <Input
                id="name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                autoComplete="name"
                required
                disabled={pending}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="org_name">{t.auth.orgName}</Label>
              <Input
                id="org_name"
                value={orgName}
                onChange={(event) => setOrgName(event.target.value)}
                autoComplete="organization"
                required
                disabled={pending}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="email">{t.auth.email}</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                required
                disabled={pending}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">{t.auth.password}</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={10}
                  disabled={pending}
                  aria-invalid={passwordTooShort}
                  aria-describedby="password-hint"
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
              <p
                id="password-hint"
                className={passwordTooShort ? "text-xs font-medium text-destructive-text" : "text-xs text-muted-foreground"}
              >
                Minimal 10 karakter.
              </p>
            </div>

            <div role="alert" aria-live="polite" className="min-h-[1.25rem] text-xs">
              {error ? (
                <>
                  <span className="font-medium text-destructive-text">{error.message}</span>
                  <span className="ml-1 font-mono text-[11px] text-muted-foreground">({error.requestId})</span>
                </>
              ) : null}
            </div>

            <Button type="submit" className="w-full" disabled={disabled}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {pending ? t.auth.signingIn : t.auth.createAccount}
            </Button>

            <p className="text-center text-[11px] text-muted-foreground">{t.auth.terms}</p>
          </form>

          <p className="mt-4 text-center text-sm text-muted-foreground">
            {t.auth.haveAccount}{" "}
            <Link href="/login" className="inline-flex min-h-9 items-center font-medium text-primary underline-offset-4 hover:underline">
              {t.auth.signIn}
            </Link>
          </p>
        </div>
      </div>

      <AuthBrandPanel />
    </div>
  );
}


