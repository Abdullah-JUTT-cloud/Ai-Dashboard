import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The charts mount real ECharts; the panels are under test, not the canvas.
vi.mock("@/components/charts", () => ({
  RevenueTrendChart: () => <div data-testid="revenue-chart" />,
  QuoteVolumeChart: () => <div data-testid="quote-chart" />,
  TopRepsChart: () => <div data-testid="reps-chart" />,
}));

import { OverviewPanel } from "@/components/dashboard/overview-panel";
import { LeadsPanel, PipelinePanel, ReceivablesPanel } from "@/components/dashboard/panels";
import type { SignalsState } from "@/hooks/use-signals";
import type { StuckLeadsState } from "@/hooks/use-stuck-leads";
import {
  emptySignalsReport,
  emptyStuckLeads,
  signalsReport,
  snapshotFixture,
  stuckLead,
  stuckLeadsFixture,
} from "./fixtures";
import type { SignalsQuery, SignalsReport, StalledOrder } from "@/types/api";

function signalsState(
  overrides: Partial<SignalsState> & { signals?: SignalsReport | null } = {},
): SignalsState {
  const filters: SignalsQuery = { staleDays: 3, stalledDays: 2, overdueDays: 30 };
  return {
    filters,
    signals: signalsReport(),
    signalsError: "",
    isLoadingSignals: false,
    stalledOrders: null,
    stalledOrdersError: "",
    isLoadingStalledOrders: false,
    rowLimit: 25,
    reloadKey: 0,
    setRowLimit: vi.fn(),
    applyFilter: vi.fn(),
    retry: vi.fn(),
    onTabChange: vi.fn(),
    ...overrides,
  };
}

function leadsState(overrides: Partial<StuckLeadsState> = {}): StuckLeadsState {
  return {
    stuckLeads: stuckLeadsFixture(),
    stuckLeadsError: "",
    isLoadingStuckLeads: false,
    leadRowLimit: 25,
    setLeadRowLimit: vi.fn(),
    retry: vi.fn(),
    ...overrides,
  };
}

describe("OverviewPanel", () => {
  const noSelect = () => undefined;

  it("renders the KPI tiles and the attention counts from the fixtures", () => {
    render(
      <OverviewPanel
        snapshot={snapshotFixture()}
        signals={signalsReport()}
        stuckLeads={stuckLeadsFixture()}
        onSelectTab={noSelect}
        onRefresh={noSelect}
      />,
    );

    expect(screen.getByText("$410")).toBeInTheDocument();
    expect(screen.getByText("4 payments received")).toBeInTheDocument();
    expect(screen.getByText("7,389 quotes")).toBeInTheDocument();
    // "Stale quotes" is both a KPI label and an attention row
    expect(screen.getAllByText("Stale quotes").length).toBeGreaterThan(0);
    // the open-quotes tile and the stale-quotes tile report the same fixture count
    expect(screen.getAllByText("311")).toHaveLength(2);
    expect(screen.getByText("$987,574.61 in stalled value")).toBeInTheDocument();
    expect(screen.getByText(/238 overdue/)).toBeInTheDocument();
    // revenue, quotes and top-reps charts
    expect(screen.getAllByTestId(/chart/)).toHaveLength(3);
  });

  it("asks for the stuck leads on the Leads tab", () => {
    const onSelectTab = vi.fn();
    render(
      <OverviewPanel
        snapshot={snapshotFixture()}
        signals={signalsReport()}
        stuckLeads={stuckLeadsFixture()}
        onSelectTab={onSelectTab}
        onRefresh={noSelect}
      />,
    );

    fireEvent.click(screen.getByText("1"));
    expect(onSelectTab).toHaveBeenCalledWith("leads");
    fireEvent.click(screen.getByText("Unpaid invoices"));
    expect(onSelectTab).toHaveBeenCalledWith("receivables");
  });

  it("waits for the first snapshot instead of rendering empty tiles", () => {
    const onRefresh = vi.fn();
    render(
      <OverviewPanel
        snapshot={null}
        signals={null}
        stuckLeads={null}
        onSelectTab={noSelect}
        onRefresh={onRefresh}
      />,
    );

    expect(screen.getByText(/Waiting for the first dashboard snapshot/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("says so when no rep has been paid this month", () => {
    render(
      <OverviewPanel
        snapshot={snapshotFixture({ top_reps_this_month: [] })}
        signals={signalsReport()}
        stuckLeads={stuckLeadsFixture()}
        onSelectTab={noSelect}
        onRefresh={noSelect}
      />,
    );

    expect(screen.getByText(/No payments have been recorded this month/)).toBeInTheDocument();
  });
});

describe("ReceivablesPanel", () => {
  it("shows the unpaid invoice row and the backend totals", () => {
    render(<ReceivablesPanel signals={signalsState()} onSelectCustomer={vi.fn()} />);

    expect(screen.getByText("27608-2025-10")).toBeInTheDocument();
    expect(screen.getByText("Pure 7")).toBeInTheDocument();
    // the column header and the row badge both read "Overdue"
    expect(screen.getAllByText("Overdue").length).toBe(2);
    expect(screen.getByText("Showing 1 of 363")).toBeInTheDocument();
  });

  it("renders the empty report honestly", () => {
    render(
      <ReceivablesPanel
        signals={signalsState({ signals: emptySignalsReport() })}
        onSelectCustomer={vi.fn()}
      />,
    );

    expect(screen.getByText(/No invoices currently carry a balance/)).toBeInTheDocument();
    expect(screen.getByText("Showing 0 of 0")).toBeInTheDocument();
  });

  it("shows the loading note before the first report arrives", () => {
    render(
      <ReceivablesPanel
        signals={signalsState({ signals: null, isLoadingSignals: true })}
        onSelectCustomer={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/Loading signals/);
  });

  it("offers a retry when the report failed", () => {
    const signals = signalsState({ signals: null, isLoadingSignals: false, signalsError: "boom" });
    render(<ReceivablesPanel signals={signals} onSelectCustomer={vi.fn()} />);

    expect(screen.getByText("boom")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Try again" })[0]);
    expect(signals.retry).toHaveBeenCalled();
  });

  it("pages the table through the hook", () => {
    const signals = signalsState();
    render(<ReceivablesPanel signals={signals} onSelectCustomer={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Show 25 more" }));
    expect(signals.setRowLimit).toHaveBeenCalled();
  });
});

describe("PipelinePanel", () => {
  it("warns that the stalled list is unreliable", () => {
    const stalled: StalledOrder[] = [];
    const signals = signalsState({ stalledOrders: stalled, isLoadingStalledOrders: false });
    render(<PipelinePanel signals={signals} onSelectCustomer={vi.fn()} />);

    expect(screen.getByText("20021-2026-10")).toBeInTheDocument();
    expect(screen.getByText(/marks this signal unreliable/)).toBeInTheDocument();
  });

  it("passes the stale and stalled thresholds to the hook", () => {
    const signals = signalsState({ stalledOrders: [] });
    render(<PipelinePanel signals={signals} onSelectCustomer={vi.fn()} />);

    fireEvent.change(screen.getByDisplayValue("3 days"), { target: { value: "5" } });
    expect(signals.applyFilter).toHaveBeenCalled();
  });

  it("surfaces a stalled-orders failure", () => {
    const signals = signalsState({ stalledOrdersError: "stalled down", stalledOrders: null });
    render(<PipelinePanel signals={signals} onSelectCustomer={vi.fn()} />);

    expect(screen.getByText("stalled down")).toBeInTheDocument();
  });
});

describe("LeadsPanel", () => {
  it("renders the parsed lead fields the backend sent", () => {
    render(<LeadsPanel leads={leadsState()} />);

    expect(screen.getByText("Keiwan Bentley")).toBeInTheDocument();
    expect(screen.getByText("Zaskii Boyz")).toBeInTheDocument();
    // the column header and the artwork state badge
    expect(screen.getAllByText("Missing").length).toBe(2);
    expect(screen.getByText("mylar bag")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 1")).toBeInTheDocument();
  });

  it("says when nothing is stuck", () => {
    render(<LeadsPanel leads={leadsState({ stuckLeads: emptyStuckLeads() })} />);

    expect(screen.getByText(/No leads match the stuck-lead filter/)).toBeInTheDocument();
  });

  it("retries a failed lead fetch", () => {
    const leads = leadsState({ stuckLeads: null, stuckLeadsError: "leads down" });
    render(<LeadsPanel leads={leads} />);

    expect(screen.getByText("leads down")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(leads.retry).toHaveBeenCalled();
  });

  it("renders category filter pills with counts and triggers category selection", () => {
    const setSelectedCategory = vi.fn();
    const leads = leadsState({
      selectedCategory: "Stuck",
      setSelectedCategory,
      categoryCounts: {
        Stuck: 0,
        New: 0,
        Working: 486,
        Dormant: 12365,
        Converted: 4472,
        Lost: 768,
      },
      allLeads: {
        generated_at: "2026-11-03T00:00:00Z",
        universe: "db.leads",
        total: 18091,
        matched_total: 0,
        category_counts: {
          Stuck: 0,
          New: 0,
          Working: 486,
          Dormant: 12365,
          Converted: 4472,
          Lost: 768,
        },
        items: [],
        limit: 10,
        offset: 0,
        next_offset: null,
        has_more: false,
      },
    });

    render(<LeadsPanel leads={leads} />);

    // Verify all categories and counts are rendered
    expect(screen.getByRole("button", { name: /Working.*486/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Dormant.*12,365/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Converted.*4,472/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Lost.*768/ })).toBeInTheDocument();

    // Verify 0 stuck displays honestly with the other counts
    expect(screen.getByText(/No leads match the stuck-lead filter right now/)).toBeInTheDocument();

    // Clicking a category invokes setSelectedCategory
    fireEvent.click(screen.getByRole("button", { name: /Working.*486/ }));
    expect(setSelectedCategory).toHaveBeenCalledWith("Working");
  });

  it("overview stuck leads tile count matches leads tab stuck count exactly (tile == tab)", () => {
    const crmStuckCount = 1834;
    const testLead = { ...stuckLead({ lead_id: "CRM-1" }), category: "Stuck" };
    const stuckLeadsData = {
      generated_at: "2026-11-03T16:26:30Z",
      days: 14,
      total: crmStuckCount,
      items: [testLead],
    };
    const leads = leadsState({
      stuckLeads: stuckLeadsData,
      selectedCategory: "Stuck",
      categoryCounts: {
        Stuck: crmStuckCount,
        New: 451,
        Working: 489,
        Dormant: 11203,
        Converted: 4472,
        Lost: 777,
      },
      allLeads: {
        generated_at: "2026-11-03T16:26:30Z",
        universe: "customer360_leads",
        total: 19226,
        matched_total: crmStuckCount,
        category_counts: {
          Stuck: crmStuckCount,
          New: 451,
          Working: 489,
          Dormant: 11203,
          Converted: 4472,
          Lost: 777,
        },
        items: [testLead],
        limit: 10,
        offset: 0,
        next_offset: null,
        has_more: false,
        synced_at: "2026-11-03T16:26:30.649812+00:00",
      },
    });

    const { unmount: unmountOverview } = render(
      <OverviewPanel
        snapshot={snapshotFixture()}
        signals={signalsReport()}
        stuckLeads={leads.stuckLeads}
        onSelectTab={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    // Overview tile displays formatted CRM stuck count and CRM live rule label
    expect(screen.getByText("1,834")).toBeInTheDocument();
    expect(screen.getByText(/CRM live rule/)).toBeInTheDocument();
    unmountOverview();

    // Leads tab renders the same stuck count in the Stuck category pill and CRM live indicator
    render(<LeadsPanel leads={leads} />);
    expect(screen.getByRole("button", { name: /Stuck.*1,834/ })).toBeInTheDocument();
    expect(screen.getAllByText(/CRM live/).length).toBeGreaterThanOrEqual(1);
  });
});

