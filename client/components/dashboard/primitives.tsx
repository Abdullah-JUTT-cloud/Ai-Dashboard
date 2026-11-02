"use client";

// Small presentational pieces the dashboard panels are built from. The KPI,
// chart and attention pieces are the Overview's visual reference; the filter
// and state notes are shared with the other tabs and keep their contract.

import { useId, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowUpRight, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A tiny, dependency-free sparkline. It only draws numbers the backend already
 * returned in the snapshot; it never invents or resamples a value.
 */
function Sparkline({
  values,
  className,
}: {
  values: number[];
  className?: string;
}) {
  const rawId = useId();
  const gradientId = `spark-${rawId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const series = values;
  const width = 160;
  const height = 32;
  const max = Math.max(...series);
  const min = Math.min(...series);
  const span = max - min || 1;
  const step = series.length > 1 ? width / (series.length - 1) : width;

  const points = series.map((value, index) => {
    const x = series.length > 1 ? index * step : width / 2;
    const y = height - 3 - ((value - min) / span) * (height - 6);
    return [x, y] as const;
  });

  const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `0,${height} ${line} ${width},${height}`;
  const last = points[points.length - 1];

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${gradientId})`} />
      <polyline
        points={line}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {last && (
        <circle cx={last[0]} cy={last[1]} r="2" fill="var(--accent)" />
      )}
    </svg>
  );
}

export function KpiCard({
  icon: Icon,
  label,
  value,
  detail,
  onActivate,
  featured = false,
  spark,
  className,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
  onActivate?: () => void;
  featured?: boolean;
  spark?: number[];
  className?: string;
}) {
  return (
    <article
      className={cn(
        "kpi-card",
        featured && "kpi-card--featured",
        className
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="kpi-label">{label}</p>
        <span className="kpi-icon">
          <Icon size={14} aria-hidden="true" />
        </span>
      </div>

      {onActivate ? (
        <button
          type="button"
          onClick={onActivate}
          title={`Open the ${label} tab`}
          className="kpi-value block w-full rounded-sm p-0 text-left transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          {value}
        </button>
      ) : (
        <p className="kpi-value">{value}</p>
      )}

      <p className="kpi-detail">{detail}</p>

      {onActivate && (
        <button type="button" onClick={onActivate} className="kpi-action self-start rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
          Open Leads
          <ArrowUpRight size={12} aria-hidden="true" />
        </button>
      )}

      {spark && spark.length > 1 && (
        <Sparkline values={spark} className="kpi-spark" />
      )}
    </article>
  );
}

export function ChartCard({
  title,
  subtitle,
  action,
  children,
  className,
}: {
  title: string;
  subtitle: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("chart-card", className)}>
      <div className="chart-card-head">
        <div className="min-w-0">
          <h2 className="chart-card-title">{title}</h2>
          <p className="chart-card-subtitle">{subtitle}</p>
        </div>
        {action}
      </div>
      <div className="mt-4 min-w-0">{children}</div>
    </section>
  );
}

export function AttentionRow({
  label,
  count,
  amount,
  detail,
  onClick,
}: {
  label: string;
  count?: number;
  amount?: string;
  detail?: string;
  onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick} className="attention-row group">
      <span className="attention-row-main">
        <span className="attention-row-label">{label}</span>
        {detail && <span className="attention-row-detail">{detail}</span>}
      </span>
      <span className="attention-row-meta">
        <span className="attention-row-count">
          {count === undefined ? "Loading..." : `${count.toLocaleString("en-US")} items`}
        </span>
        {amount && <span className="attention-row-amount">{amount}</span>}
        <ChevronRight
          size={14}
          className="attention-row-chevron"
          aria-hidden="true"
        />
      </span>
    </button>
  );
}

export interface FilterOption {
  label: string;
  value: number;
  options: number[];
  suffix: string;
  onChange: (value: number) => void;
}

export function FilterBar({
  title,
  description,
  filters,
}: {
  title: string;
  description: string;
  filters: FilterOption[];
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-surface p-5 shadow-sm">
      <div className="max-w-2xl">
        <h2 className="text-base font-semibold text-foreground tracking-tight">{title}</h2>
        <p className="mt-1 text-xs text-muted leading-relaxed">{description}</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {filters.map((filter) => (
          <label key={filter.label} className="flex items-center gap-2 text-xs font-medium text-muted">
            <span>{filter.label}</span>
            <select
              value={filter.value}
              onChange={(event) => filter.onChange(Number(event.target.value))}
              className="rounded-lg border border-border bg-surface-raised px-3 py-1.5 text-xs font-semibold text-foreground outline-none transition-all hover:border-border-hover focus:border-accent focus:ring-1 focus:ring-accent/30 cursor-pointer"
            >
              {filter.options.map((option) => (
                <option key={option} value={option} className="bg-surface text-foreground">
                  {option} {filter.suffix}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </div>
  );
}

export function LoadingNote() {
  return (
    <p
      className="rounded-xl border border-border bg-surface px-4 py-8 text-center text-sm font-medium text-muted shadow-sm"
      role="status"
    >
      Loading signals from the backend...
    </p>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-negative-border bg-negative-muted px-4 py-6 text-center shadow-sm">
      <p className="text-sm font-medium text-negative-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3.5 inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3.5 py-1.5 text-xs font-semibold text-foreground shadow-sm transition-all hover:border-accent/40 hover:bg-surface-raised"
      >
        Try again
      </button>
    </div>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="px-4 py-8 text-center text-xs text-muted">{children}</p>;
}
