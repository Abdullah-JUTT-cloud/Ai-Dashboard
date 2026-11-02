"use client";

// The Overview tab: KPI tiles, the two charts and the "Needs attention" panel.
// Every number comes from the backend snapshot or the signals report; opening a
// tab from a card does exactly what clicking that tab would.

import {
  Activity,
  AlertTriangle,
  Boxes,
  Clock,
  DollarSign,
  FileText,
  RefreshCw,
  UserX,
  Users,
} from "lucide-react";
import { QuoteVolumeChart, RevenueTrendChart, TopRepsChart } from "@/components/charts";
import {
  AttentionRow,
  ChartCard,
  EmptyNote,
  KpiCard,
} from "@/components/dashboard/primitives";
import { Skeleton, SkeletonChartFrame, SkeletonKpiTile } from "@/components/ui/skeleton";
import { formatMoney } from "@/lib/format";
import type { DashboardSnapshot, SignalsReport, StuckLeadsResponse } from "@/types/api";

type Tab = "overview" | "receivables" | "pipeline" | "leads";

function AttentionSkeleton() {
  return (
    <div className="attention-row cursor-default" aria-hidden="true">
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-2.5 w-40" />
      </div>
      <Skeleton className="h-5 w-16 rounded-md" />
    </div>
  );
}

function NeedsAttention({
  signals,
  error,
  onSelectTab,
  onRetry,
}: {
  signals: SignalsReport | null;
  error: string;
  onSelectTab: (tab: Tab) => void;
  onRetry: () => void;
}) {
  return (
    <section className="attention-card" aria-labelledby="needs-attention-heading">
      <div className="attention-head">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-warning-border bg-warning-muted text-warning">
            <AlertTriangle size={15} aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h2 id="needs-attention-heading" className="attention-title">
              Needs attention
            </h2>
            <p className="attention-subtitle">
              Deterministic counts from GET /api/signals
            </p>
          </div>
        </div>
        <span className="attention-total">Signal report</span>
      </div>

      <div className="attention-list">
        {!signals && error ? (
          <div className="state-panel" role="alert">
            <p className="state-title">Signals are unavailable</p>
            <p className="state-detail">{error}</p>
            <button
              type="button"
              onClick={onRetry}
              className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-[11px] font-semibold text-foreground transition-colors hover:border-accent-border hover:text-accent"
            >
              <RefreshCw size={11} aria-hidden="true" />
              Try again
            </button>
          </div>
        ) : !signals ? (
          <div role="status" aria-label="Loading signals" className="space-y-2">
            <AttentionSkeleton />
            <AttentionSkeleton />
            <AttentionSkeleton />
          </div>
        ) : (
          <>
            <AttentionRow
              label="Stale quotes"
              count={signals.summary.stale_quotes.count}
              amount={formatMoney(signals.summary.stale_quotes.total_amount)}
              detail="Open quotes past the stale threshold · open Pipeline"
              onClick={() => onSelectTab("pipeline")}
            />
            <AttentionRow
              label="Unpaid invoices"
              count={signals.summary.unpaid_invoices.count}
              amount={formatMoney(signals.summary.unpaid_invoices.total_amount)}
              detail={`${signals.summary.unpaid_invoices.overdue_count} overdue · ${signals.summary.unpaid_invoices.partially_paid_count} partially paid · open Receivables`}
              onClick={() => onSelectTab("receivables")}
            />
            <AttentionRow
              label="Stalled orders"
              count={signals.summary.stalled_orders.count}
              amount={formatMoney(signals.summary.stalled_orders.total_amount)}
              detail="Signal is marked unreliable by the backend · open Pipeline"
              onClick={() => onSelectTab("pipeline")}
            />
          </>
        )}
      </div>

      <div className="border-t border-border px-4 py-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
        Select a signal to open its records
      </div>
    </section>
  );
}

export function OverviewPanel({
  snapshot,
  signals,
  stuckLeads,
  onSelectTab,
  onRefresh,
  signalsError = "",
  leadsError = "",
  onRetrySignals,
}: {
  snapshot: DashboardSnapshot | null;
  signals: SignalsReport | null;
  stuckLeads: StuckLeadsResponse | null;
  onSelectTab: (tab: Tab) => void;
  onRefresh: () => void;
  signalsError?: string;
  leadsError?: string;
  onRetrySignals?: () => void;
}) {
  if (!snapshot) {
    return (
      <div className="space-y-5 sm:space-y-6">
        <section className="overflow-hidden rounded-xl border border-border bg-surface shadow-sm" role="status">
          <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
            <div className="grid h-11 w-11 place-items-center rounded-xl border border-accent-border bg-accent-muted text-accent">
              <Activity size={20} aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-semibold text-foreground">
                Waiting for the first dashboard snapshot
              </p>
              <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-muted">
                The backend builds one on its scheduler cycle (GET /api/metrics returns 503 until then).
              </p>
            </div>
            <button
              type="button"
              onClick={onRefresh}
              className="mt-1 inline-flex items-center gap-2 rounded-lg border border-border bg-surface-raised px-4 py-2 text-xs font-semibold text-foreground shadow-sm transition-all hover:border-accent/40 hover:bg-surface-overlay"
            >
              <RefreshCw size={13} aria-hidden="true" />
              Try again
            </button>
          </div>
        </section>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-12">
          <SkeletonKpiTile className="sm:col-span-2 lg:col-span-1 2xl:col-span-5" />
          <SkeletonKpiTile className="2xl:col-span-4" />
          <SkeletonKpiTile className="2xl:col-span-3" />
          <SkeletonKpiTile className="2xl:col-span-3" />
          <SkeletonKpiTile className="2xl:col-span-3" />
          <SkeletonKpiTile className="2xl:col-span-3" />
          <SkeletonKpiTile className="2xl:col-span-3" />
          <SkeletonKpiTile className="2xl:col-span-6" />
          <SkeletonKpiTile className="2xl:col-span-6" />
        </div>

        <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]">
          <SkeletonChartFrame />
          <SkeletonChartFrame />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5 sm:space-y-6">
      {/* KPI hierarchy: one featured revenue tile, then the operating totals */}
      <section aria-labelledby="overview-kpis-heading" className="space-y-3">
        <div className="section-heading">
          <div>
            <h2 id="overview-kpis-heading" className="section-title">
              Performance snapshot
            </h2>
            <p className="section-subtitle">
              Backend-computed totals for today, this month and the open book.
            </p>
          </div>
          <span className="section-chip">9 metrics</span>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-12">
          <KpiCard
            featured
            className="sm:col-span-2 lg:col-span-1 2xl:col-span-5"
            icon={DollarSign}
            label="Revenue today"
            value={snapshot.revenue_today_display}
            detail={`${snapshot.payments_count_today} payments received`}
            spark={snapshot.revenue_per_day.map((row) => row.total)}
          />
          <KpiCard
            className="2xl:col-span-4"
            icon={Activity}
            label="Month to date"
            value={snapshot.revenue_month_to_date_display}
            detail="Payments received this month"
          />
          <KpiCard
            className="2xl:col-span-3"
            icon={Clock}
            label="Last 7 days"
            value={snapshot.revenue_last_7_days_display}
            detail="Today plus the previous 6 days"
          />
          <KpiCard
            className="2xl:col-span-3"
            icon={FileText}
            label="Open quotes"
            value={`${snapshot.open_quotes_count}`}
            detail={`${snapshot.open_quotes_value_display} in open value`}
          />
          <KpiCard
            className="2xl:col-span-3"
            icon={AlertTriangle}
            label="Unpaid balance"
            value={snapshot.unpaid_total_display}
            detail={`${snapshot.unpaid_count} invoices with a balance`}
          />
          <KpiCard
            className="2xl:col-span-3"
            icon={Boxes}
            label="Overdue balance"
            value={snapshot.overdue_total_display}
            detail={`${snapshot.overdue_count} invoices past the overdue threshold`}
          />
          <KpiCard
            className="2xl:col-span-3"
            icon={FileText}
            label="Stale quotes"
            value={`${signals?.summary.stale_quotes.count ?? "—"}`}
            detail={
              signals
                ? `${formatMoney(signals.summary.stale_quotes.total_amount)} in stalled value`
                : signalsError ? "Signals unavailable · see the error above" : "Waiting for signals"
            }
          />
          <KpiCard
            className="2xl:col-span-6"
            icon={Users}
            label="Records scanned"
            value={`${snapshot.scanned.quotes.toLocaleString("en-US")} quotes`}
            detail={`${snapshot.scanned.orders.toLocaleString("en-US")} orders · ${snapshot.scanned.payments.toLocaleString("en-US")} payments`}
          />
          <KpiCard
            className="2xl:col-span-6"
            icon={UserX}
            label="Stuck leads"
            value={stuckLeads ? stuckLeads.total.toLocaleString("en-US") : "—"}
            detail={
              stuckLeads
                ? `Active in the last ${stuckLeads.days} days with a description · open the Leads tab`
                : leadsError ? "Leads unavailable · open the Leads tab to retry" : "Waiting for stuck leads"
            }
            onActivate={() => onSelectTab("leads")}
          />
        </div>
      </section>

      {/* Revenue and quote flow */}
      <section aria-labelledby="overview-flow-heading" className="space-y-3">
        <div className="section-heading">
          <div>
            <h2 id="overview-flow-heading" className="section-title">
              Revenue &amp; quote flow
            </h2>
            <p className="section-subtitle">
              Fourteen days of backend-recorded activity.
            </p>
          </div>
        </div>

        <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]">
          <ChartCard
            title="Revenue received"
            subtitle="Last 14 days · from payments"
            action={
              <span className="chart-legend">
                <span className="chart-legend-dot" aria-hidden="true" />
                Payments
              </span>
            }
          >
            {snapshot.revenue_per_day.length ? (
              <RevenueTrendChart data={snapshot.revenue_per_day} />
            ) : <EmptyNote>No daily revenue data is available.</EmptyNote>}
          </ChartCard>
          <ChartCard
            title="Quotes created"
            subtitle="Last 14 days · from quotes"
            action={
              <span className="chart-legend">
                <span
                  className="chart-legend-dot chart-legend-dot--info"
                  aria-hidden="true"
                />
                Quotes
              </span>
            }
          >
            {snapshot.quotes_per_day.length ? (
              <QuoteVolumeChart data={snapshot.quotes_per_day} />
            ) : <EmptyNote>No daily quote data is available.</EmptyNote>}
          </ChartCard>
        </div>
      </section>

      {/* Rep ranking and the deterministic attention queue */}
      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <ChartCard
          title="Top reps this month"
          subtitle="Ranked by payments received"
          action={<span className="chart-legend">This month</span>}
        >
          {snapshot.top_reps_this_month.length > 0 ? (
            <TopRepsChart reps={snapshot.top_reps_this_month} />
          ) : (
            <EmptyNote>No payments have been recorded this month.</EmptyNote>
          )}
        </ChartCard>

        <NeedsAttention
          signals={signals}
          error={signalsError}
          onSelectTab={onSelectTab}
          onRetry={onRetrySignals ?? onRefresh}
        />
      </div>
    </div>
  );
}
