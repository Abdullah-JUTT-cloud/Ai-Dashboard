"use client";

import * as React from "react";
import { RefreshCw, Users } from "lucide-react";
import { BrandLogo } from "@/components/brand-logo";
import { SystemHealthBadge } from "@/components/system-health";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface GlobalHeaderProps {
  liveStatus: "live" | "connecting" | "reconnecting" | "offline";
  liveError?: string;
  lastEventAt?: string | null;
  onRefresh: () => void | Promise<void>;
  onOpenCustomerContext: () => void;
  className?: string;
}

export function GlobalHeader({
  liveStatus,
  liveError,
  lastEventAt,
  onRefresh,
  onOpenCustomerContext,
  className,
}: GlobalHeaderProps) {
  const [isRefreshing, setIsRefreshing] = React.useState(false);

  async function handleRefresh() {
    setIsRefreshing(true);
    try {
      await onRefresh();
    } finally {
      // Small visual feedback window
      setTimeout(() => setIsRefreshing(false), 300);
    }
  }

  const liveLabel =
    liveStatus === "live"
      ? "Live stream connected"
      : liveStatus === "connecting"
        ? "Connecting to live stream..."
        : liveStatus === "reconnecting"
          ? "Live stream reconnecting..."
          : "Live stream offline";

  const liveShort =
    liveStatus === "live"
      ? "Live"
      : liveStatus === "offline"
        ? "Offline"
        : "Syncing";

  const liveDotClass =
    liveStatus === "live"
      ? "bg-emerald-400"
      : liveStatus === "offline"
        ? "bg-red-400"
        : "bg-amber-300";

  return (
    <header
      data-slot="global-header"
      className={cn(
        "sticky top-0 z-40 w-full border-b border-border bg-background/95 backdrop-blur-md",
        "transition-colors duration-normal ease-default",
        className
      )}
    >
      <div className="shell-container flex h-14 items-center justify-between gap-3 sm:h-16">
        {/* Left: brand lockup and the workspace label */}
        <div className="flex min-w-0 items-center gap-3">
          <BrandLogo />
          <span className="hidden items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-muted lg:inline-flex">
            <span className="h-1 w-1 rounded-full bg-accent" aria-hidden="true" />
            Operations
          </span>
        </div>

        {/* Right: health, live state and the two global actions */}
        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <div className="hidden md:flex">
            <SystemHealthBadge />
          </div>

          <div
            className="live-pill"
            role="status"
            title={liveError || liveLabel}
          >
            <span
              className={cn("live-dot transition-colors", liveDotClass)}
              aria-hidden="true"
            />
            <span className="hidden sm:inline">{liveLabel}</span>
            <span className="sm:hidden">{liveShort}</span>
          </div>

          <span className="mx-0.5 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />

          {lastEventAt && (
            <span className="hidden select-none font-mono text-[11px] text-muted xl:inline">
              Updated {formatDateTime(lastEventAt)}
            </span>
          )}

          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleRefresh()}
            className="gap-1.5 font-normal text-muted hover:text-foreground"
            title="Refresh dashboard snapshot and briefing"
            aria-label="Refresh dashboard data"
          >
            <RefreshCw
              size={13}
              className={cn(
                "transition-transform duration-normal",
                isRefreshing && "animate-spin text-accent"
              )}
              aria-hidden="true"
            />
            <span className="hidden sm:inline">Refresh</span>
          </Button>

          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onOpenCustomerContext}
            className="gap-1.5 border-border font-normal text-muted hover:border-accent-border hover:text-accent"
            title="Look up a customer's quotes, orders, payments and leads"
            aria-label="Open customer context"
          >
            <Users size={13} aria-hidden="true" />
            <span className="hidden lg:inline">Customer context</span>
          </Button>
        </div>
      </div>
    </header>
  );
}
