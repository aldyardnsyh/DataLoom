/**
 * Pure helpers for the installation admin form.
 *
 * They live outside `app/(app)/admin/installation/page.tsx` because a Next.js
 * page module may only export the page component plus a small allowlist of
 * route config. Exporting `buildUpdatePayload` from the page broke
 * `next build` with "Property 'buildUpdatePayload' is incompatible with index
 * signature" — a type error that only appears at build time, not in `tsc`.
 */

import type { InstallationLlmUpdate } from "@/lib/types";

export type InstallationFormState = {
  base_url: string;
  model_default: string;
  model_small: string;
  timeout_seconds: string;
  price_in_per_1k: string;
  price_out_per_1k: string;
  api_key: string;
};

export const EMPTY_INSTALLATION_FORM: InstallationFormState = {
  base_url: "",
  model_default: "",
  model_small: "",
  timeout_seconds: "",
  price_in_per_1k: "",
  price_out_per_1k: "",
  api_key: "",
};

/** Parse an optional numeric field: blank means "leave unchanged". */
export function optionalNumber(value: string): number | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * Build the PUT body from the form.
 *
 * Only fields the operator actually filled are sent, so an empty box never
 * overwrites a stored value with null. The API key follows the same rule and is
 * the one field where sending an empty string would be actively harmful: it
 * would look like "clear the key" instead of "keep the key".
 */
export function buildUpdatePayload(form: InstallationFormState): InstallationLlmUpdate {
  const payload: InstallationLlmUpdate = {};

  const baseUrl = form.base_url.trim();
  if (baseUrl !== "") payload.base_url = baseUrl;

  const modelDefault = form.model_default.trim();
  if (modelDefault !== "") payload.model_default = modelDefault;

  const modelSmall = form.model_small.trim();
  if (modelSmall !== "") payload.model_small = modelSmall;

  const timeout = optionalNumber(form.timeout_seconds);
  if (timeout !== undefined) payload.timeout_seconds = timeout;

  const priceIn = optionalNumber(form.price_in_per_1k);
  if (priceIn !== undefined) payload.price_in_per_1k = priceIn;

  const priceOut = optionalNumber(form.price_out_per_1k);
  if (priceOut !== undefined) payload.price_out_per_1k = priceOut;

  if (form.api_key.trim() !== "") payload.api_key = form.api_key.trim();

  return payload;
}
