"use client";

// The Receivables, Pipeline and Leads tabs: their filter bar, their tables and
// the loading / error states around them.

import { ErrorNote, FilterBar, LoadingNote } from "@/components/dashboard/primitives";
import { StaleQuoteTable } from "@/components/dashboard/stale-quote-table";
import { StalledOrderTable } from "@/components/dashboard/stalled-order-table";
import { StuckLeadsTable } from "@/components/dashboard/stuck-leads-table";
import { UnpaidTable } from "@/components/dashboard/unpaid-table";
import { ROW_PAGE_SIZE, type SignalsState } from "@/hooks/use-signals";
import type { StuckLeadsState } from "@/hooks/use-stuck-leads";
import { formatDateTime } from "@/lib/format";

const STALE_DAY_OPTIONS = [3, 5, 7, 14, 30];
const STALLED_DAY_OPTIONS = [2, 3, 5, 7, 14];
const OVERDUE_DAY_OPTIONS = [7, 14, 30, 60, 90];

type SelectCustomer = (customerId: number | null) => void;

export function ReceivablesPanel({
  signals,
  onSelectCustomer,
}: {
  signals: SignalsState;
  onSelectCustomer: SelectCustomer;
}) {
  const showMore = () => signals.setRowLimit((current) => current + ROW_PAGE_SIZE);
  return (
    <div className="space-y-6">
      <FilterBar
        title="Receivables"
        description="Every invoice with a balance greater than zero. The overdue threshold changes which rows are flagged, not which rows are returned."
        filters={[
          {
            label: "Overdue after",
            value: signals.filters.overdueDays ?? 30,
            options: OVERDUE_DAY_OPTIONS,
            suffix: "days",
            onChange: (days) => signals.applyFilter({ overdueDays: days }),
          },
        ]}
      />
      {signals.isLoadingSignals && !signals.signals ? (
        <LoadingNote />
      ) : signals.signals ? (
        <UnpaidTable
          rows={signals.signals.unpaid_invoices}
          totalCount={signals.signals.summary.unpaid_invoices.count}
          rowLimit={signals.rowLimit}
          onShowMore={showMore}
          onSelectCustomer={onSelectCustomer}
        />
      ) : (
        <ErrorNote
          message={signals.signalsError || "Signals are unavailable."}
          onRetry={signals.retry}
        />
      )}
    </div>
  );
}

export function PipelinePanel({
  signals,
  onSelectCustomer,
}: {
  signals: SignalsState;
  onSelectCustomer: SelectCustomer;
}) {
  const showMore = () => signals.setRowLimit((current) => current + ROW_PAGE_SIZE);
  return (
    <div className="space-y-6">
      <FilterBar
        title="Pipeline"
        description="Open quotes and stalled orders. Both lists are computed by the backend; stalled orders are explicitly unreliable because the ERP has no status timestamps."
        filters={[
          {
            label: "Quotes stale after",
            value: signals.filters.staleDays ?? 3,
            options: STALE_DAY_OPTIONS,
            suffix: "days",
            onChange: (days) => signals.applyFilter({ staleDays: days }),
          },
          {
            label: "Orders stalled after",
            value: signals.filters.stalledDays ?? 2,
            options: STALLED_DAY_OPTIONS,
            suffix: "days",
            onChange: (days) => signals.applyFilter({ stalledDays: days }),
          },
        ]}
      />
      {signals.isLoadingSignals && !signals.signals ? (
        <LoadingNote />
      ) : signals.signals ? (
        <div className="space-y-6">
          <StaleQuoteTable
            rows={signals.signals.stale_quotes}
            totalCount={signals.signals.summary.stale_quotes.count}
            rowLimit={signals.rowLimit}
            onShowMore={showMore}
            onSelectCustomer={onSelectCustomer}
          />
          {signals.isLoadingStalledOrders && !signals.stalledOrders ? (
            <LoadingNote />
          ) : signals.stalledOrdersError ? (
            <ErrorNote message={signals.stalledOrdersError} onRetry={signals.retry} />
          ) : signals.stalledOrders ? (
            <StalledOrderTable
              rows={signals.stalledOrders}
              totalCount={signals.signals.summary.stalled_orders.count}
              reliable={signals.signals.stalled_orders.reliable}
              rowLimit={signals.rowLimit}
              onShowMore={showMore}
              onSelectCustomer={onSelectCustomer}
            />
          ) : null}
        </div>
      ) : (
        <ErrorNote
          message={signals.signalsError || "Signals are unavailable."}
          onRetry={signals.retry}
        />
      )}
    </div>
  );
}

export function LeadsPanel({ leads }: { leads: StuckLeadsState }) {
  const currentCategory = leads.selectedCategory ?? "Stuck";
  const counts = leads.categoryCounts ?? leads.allLeads?.category_counts ?? {};

  const getCategoryCount = (cat: string) => {
    if (counts[cat] !== undefined) return counts[cat];
    if (cat === "Stuck" && leads.stuckLeads) return leads.stuckLeads.total;
    return 0;
  };

  const rows = leads.allLeads?.items ?? leads.stuckLeads?.items ?? [];
  const totalCount = leads.allLeads
    ? leads.allLeads.matched_total
    : (leads.stuckLeads?.total ?? 0);

  const isLoading =
    leads.isLoadingAllLeads ||
    (leads.isLoadingStuckLeads && !leads.stuckLeads && !leads.allLeads);
  const error = leads.allLeadsError || leads.stuckLeadsError;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 rounded-2xl border border-border/80 bg-surface/80 p-5 shadow-sm backdrop-blur-md">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="max-w-3xl">
            <h2 className="text-base font-semibold tracking-tight text-foreground">Leads</h2>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              Full lead pipeline partitioned by status, age, and activity. Stuck leads use the CRM
              live rule (status New / Attempted to Contact with activity within 14 days and description
              present; LeadDesk flags applied where recorded); aged leads move to Dormant; actively
              engaged to Working; converted and lost categorized from verified CRM records.
            </p>
          </div>
          <div className="inline-flex items-center gap-2 self-start sm:self-auto rounded-lg border border-border/80 bg-surface/60 px-2.5 py-1 text-[11px] font-mono text-muted">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            <span>
              CRM live · {leads.allLeads?.synced_at ? `synced ${formatDateTime(leads.allLeads.synced_at)}` : "synced live"}
            </span>
          </div>
        </div>

        {/* Category Filter Pills */}
        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/40">
          {(["Stuck", "New", "Working", "Dormant", "Converted", "Lost"] as const).map((cat) => {
            const isSelected = currentCategory === cat;
            const count = getCategoryCount(cat);
            return (
              <button
                key={cat}
                type="button"
                onClick={() => leads.setSelectedCategory?.(cat)}
                className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-1.5 text-xs font-medium transition-all ${
                  isSelected
                    ? "border border-accent bg-accent/15 text-accent font-semibold shadow-sm"
                    : "border border-border/80 bg-surface/60 text-muted hover:border-border hover:bg-surface-raised hover:text-foreground"
                }`}
              >
                <span>{cat}</span>
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[11px] font-mono tabular-nums ${
                    isSelected
                      ? "bg-accent/25 text-accent"
                      : "bg-surface-raised text-muted"
                  }`}
                >
                  {count.toLocaleString()}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {isLoading && rows.length === 0 ? (
        <LoadingNote />
      ) : rows.length > 0 || !error ? (
        <StuckLeadsTable
          rows={rows}
          totalCount={totalCount}
          rowLimit={leads.leadRowLimit}
          onShowMore={() => leads.setLeadRowLimit((current) => current + ROW_PAGE_SIZE)}
          title={currentCategory === "Stuck" ? "Stuck leads" : `${currentCategory} leads`}
          emptyMessage={
            currentCategory === "Stuck"
              ? "No leads match the stuck-lead filter right now."
              : `No leads in the ${currentCategory} category right now.`
          }
        />
      ) : (
        <ErrorNote
          message={error || "Leads are unavailable."}
          onRetry={leads.retry}
        />
      )}
    </div>
  );
}
