import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * `cn` merges conditional class names and resolves Tailwind conflicts.
 * This is the canonical shadcn/ui helper: every UI component imports it.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
