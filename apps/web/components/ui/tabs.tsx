"use client";

import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";

import { cn } from "@/lib/utils";

const Tabs = TabsPrimitive.Root;

/**
 * Segmented control.
 *
 * Affordance rules learned from a real usability complaint: a filter row of
 * plain words on a near-white background is indistinguishable from body text,
 * and users could not tell the tabs were clickable at all. Three things make it
 * unmistakable now:
 *
 * 1. The strip is a **visible container** (border + inset shadow), so it reads
 *    as one control rather than five loose labels.
 * 2. Every trigger has a **hover state**, which is the cheapest possible "this
 *    is interactive" signal and was missing entirely before.
 * 3. The active trigger is a raised chip (surface + border + shadow) instead of
 *    a barely-lighter rectangle.
 *
 * Geometry never shifts between states: every trigger always carries a 1px
 * transparent border, so activating one does not resize the row.
 */
const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      // `max-w-full overflow-x-auto` keeps a long strip inside a 390px viewport
      // by scrolling instead of widening the page. `scrollbar-none` hides the
      // bar because the affordance is the cut-off tab, not a scrollbar.
      "scrollbar-none inline-flex h-9 max-w-full items-center justify-start gap-1 overflow-x-auto rounded-lg border border-border bg-secondary p-1 text-muted-foreground shadow-[inset_0_1px_2px_rgba(15,23,42,0.07)]",
      className,
    )}
    {...props}
  />
));
TabsList.displayName = TabsPrimitive.List.displayName;

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      [
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-transparent px-3 py-1",
        "text-sm font-medium text-muted-foreground transition-[background-color,color,box-shadow,border-color] duration-150",
        // Hover: previously absent, which is why the row looked like static text.
        "cursor-pointer hover:bg-background hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        "disabled:pointer-events-none disabled:opacity-50",
        // Active is a SOLID primary chip, not a white one: white on a light-grey
        // track was almost the same value, so the selected filter was invisible.
        "data-[state=active]:border-primary data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-card",
      ].join(" "),
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      "mt-4 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      className,
    )}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsList, TabsTrigger, TabsContent };