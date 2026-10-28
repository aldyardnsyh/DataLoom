"use client";

import { useState } from "react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { t } from "@/lib/i18n/id";

/**
 * ConfirmDialog: every destructive action requires retyping the resource name
 * before the destructive button enables (PRD 12.5).
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  resourceName,
  confirmLabel = t.common.delete,
  onConfirm,
  pending = false,
  disabled = false,
  disabledReason,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  resourceName: string;
  confirmLabel?: string;
  onConfirm: () => void;
  pending?: boolean;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === resourceName.trim();

  const handleOpenChange = (next: boolean) => {
    if (!next) setTyped("");
    onOpenChange(next);
  };

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-1.5">
          <Label htmlFor="confirm-name">
            {t.confirm.typeToConfirm.replace("<nama>", resourceName)}
          </Label>
          <Input
            id="confirm-name"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoComplete="off"
            aria-invalid={typed.length > 0 && !matches}
            aria-describedby="confirm-hint"
          />
          <p id="confirm-hint" className="text-xs text-muted-foreground">
            {typed.length > 0 && !matches ? (
              <span className="font-medium text-destructive-text">{t.confirm.mismatch}</span>
            ) : (
              t.confirm.destructiveWarning
            )}
          </p>
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{t.common.cancel}</AlertDialogCancel>
          {/* Plain Button keeps the destructive styling but lets us stay
              disabled until the name matches (AlertDialogAction auto-closes). */}
          <Button
            variant="destructive"
            disabled={!matches || pending || disabled}
            title={disabled ? disabledReason : undefined}
            onClick={() => {
              onConfirm();
              handleOpenChange(false);
            }}
          >
            {pending ? "Menghapus…" : confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

