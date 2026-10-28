"use client";

import * as React from "react";

/**
 * The DataLoom mark.
 *
 * `useId` gives every instance its own gradient id: three marks on one page
 * (header, footer, auth panel) would otherwise emit duplicate `id` attributes,
 * which is invalid HTML and lets a stale gradient win.
 *
 * The name is the design brief: a loom weaves separate threads into one cloth,
 * which is what the product does to data. The glyph is three warp threads and
 * three weft threads interlocking with a real over/under weave (each horizontal
 * thread is drawn as segments with a gap where it passes behind a vertical one),
 * so it reads as a woven lattice rather than a generic grid.
 *
 * Why a plain SVG component and not an image: it must stay crisp at 16px in the
 * browser tab, tint with the theme, and add zero bytes to the bundle. The same
 * geometry is duplicated in `app/icon.svg` (favicon) and `app/apple-icon.svg`
 * because those files must be static.
 */


const WARP = [10, 16, 22];
const WEFT = [10, 16, 22];

/** Horizontal thread segments; the gap is where it dives behind a warp thread. */
const WEFT_SEGMENTS: Record<number, [number, number][]> = {
  10: [
    [6, 14.2],
    [17.8, 26],
  ],
  16: [
    [6, 8.2],
    [11.8, 20.2],
    [23.8, 26],
  ],
  22: [
    [6, 14.2],
    [17.8, 26],
  ],
};

export function BrandMark({
  size = 28,
  className,
  /** `true` renders the tile only (no rounded background). */
  bare = false,
}: {
  size?: number;
  className?: string;
  bare?: boolean;
}) {
  const gradientId = React.useId();

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="DataLoom"
    >
      {bare ? null : (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
              <stop stopColor="#3E8BF6" />
              <stop offset="1" stopColor="#1550B8" />
            </linearGradient>
          </defs>
          <rect width="32" height="32" rx="8" fill={`url(#${gradientId})`} />
        </>
      )}

      <g stroke={bare ? "currentColor" : "#FFFFFF"} strokeWidth="2.1" strokeLinecap="round" opacity={bare ? 1 : 0.97}>
        {WARP.map((x) => (
          <line key={`warp-${x}`} x1={x} y1="6" x2={x} y2="26" />
        ))}
        {WEFT.map((y) =>
          (WEFT_SEGMENTS[y] ?? []).map(([x1, x2], index) => (
            <line key={`weft-${y}-${index}`} x1={x1} y1={y} x2={x2} y2={y} />
          )),
        )}
      </g>
    </svg>
  );
}

/** Wordmark used in the header and footer: mark plus product name. */
export function BrandLockup({
  size = 28,
  className,
  textClassName,
}: {
  size?: number;
  className?: string;
  textClassName?: string;
}) {
  return (
    <span className={className}>
      <BrandMark size={size} />
      <span className={textClassName}>DataLoom</span>
    </span>
  );
}
