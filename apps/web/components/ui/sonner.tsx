"use client";

import { Toaster as Sonner, type ToasterProps } from "sonner";

/**
 * Toast host. Light theme only; uses our token colours.
 * Usage: import { toast } from "sonner".
 */
export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="light"
      position="bottom-right"
      richColors
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group rounded-lg border border-border bg-background text-foreground shadow-pop text-sm",
          description: "text-muted-foreground",
          actionButton: "bg-primary text-primary-foreground",
          cancelButton: "bg-muted text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}
