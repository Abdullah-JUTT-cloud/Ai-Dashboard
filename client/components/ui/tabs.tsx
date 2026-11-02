"use client";

import * as React from "react";
import { Tabs as TabsPrimitive } from "@base-ui/react/tabs";
import { cn } from "@/lib/utils";

function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-5", className)}
      {...props}
    />
  );
}

function TabsList({
  className,
  variant = "default",
  children,
  ...props
}: TabsPrimitive.List.Props & { variant?: "default" | "nav" }) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        variant === "default"
          ? "relative inline-flex w-fit items-center gap-1 rounded-xl border border-border bg-surface-sunken p-1 text-muted shadow-sm"
          : "nav-tabs relative flex w-full max-w-full items-center gap-0.5 overflow-x-auto text-muted",
        className
      )}
      {...props}
    >
      {variant === "default" && (
        <TabsPrimitive.Indicator
          className="pointer-events-none absolute z-0 rounded-lg border border-border/80 bg-surface-raised shadow-sm transition-all duration-normal ease-default data-[orientation=horizontal]:top-1 data-[orientation=horizontal]:h-[calc(100%-8px)]"
          aria-hidden="true"
        />
      )}
      {children}
    </TabsPrimitive.List>
  );
}

function TabsTrigger({
  className,
  variant = "default",
  children,
  ...props
}: TabsPrimitive.Tab.Props & { variant?: "default" | "nav" }) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-trigger"
      className={cn(
        "relative z-10 inline-flex select-none cursor-pointer items-center justify-center font-medium transition-colors duration-fast",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        "data-[active]:font-semibold",
        variant === "default"
          ? "h-9 rounded-lg px-3.5 text-xs text-muted hover:bg-surface-raised/50 hover:text-foreground data-[active]:text-foreground sm:text-sm"
          : "nav-tab h-11 shrink-0 gap-2 rounded-none px-3 text-[13px] text-muted hover:text-foreground data-[active]:text-foreground sm:px-3.5",
        className
      )}
      {...props}
    >
      {children}
    </TabsPrimitive.Tab>
  );
}

function TabsContent({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-content"
      className={cn("min-w-0 outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}
      {...props}
    />
  );
}

const TabsIndicator = TabsPrimitive.Indicator;

export { Tabs, TabsContent, TabsList, TabsTrigger, TabsIndicator };
