"use client";

// The dashboard shell: the page heading, the seven-tab command navigation and
// the wiring between the tabs. The data lives in hooks (use-live-data,
// use-signals, use-stuck-leads) and each tab renders a panel from
// components/dashboard/*.

import { useEffect, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Gauge,
  GitBranch,
  LayoutDashboard,
  MessagesSquare,
  ReceiptText,
  ShieldCheck,
  Sparkles,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useLiveData } from "@/hooks/use-live-data";
import { ROW_PAGE_SIZE, useSignals } from "@/hooks/use-signals";
import { useStuckLeads } from "@/hooks/use-stuck-leads";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OverviewPanel } from "@/components/dashboard/overview-panel";
import { LeadsPanel, PipelinePanel, ReceivablesPanel } from "@/components/dashboard/panels";
import { CommandCenter } from "@/components/command-center/command-center";
import { ChatHistoryPanel } from "@/components/chat-history";
import { formatDateTime } from "@/lib/format";

const TABS: { value: string; label: string; icon: LucideIcon }[] = [
  { value: "command-center", label: "Command Center", icon: LayoutDashboard },
  { value: "overview", label: "Overview", icon: Gauge },
  { value: "receivables", label: "Receivables", icon: ReceiptText },
  { value: "pipeline", label: "Pipeline", icon: GitBranch },
  { value: "leads", label: "Leads", icon: Users },
  { value: "ai-review", label: "AI review", icon: Sparkles },
  { value: "chats", label: "Chats", icon: MessagesSquare },
];

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "command-center": {
    title: "Command Center",
    subtitle:
      "Direct line to the Biztek operations brain — ask, inspect, and dispatch work.",
  },
  overview: {
    title: "Overview",
    subtitle:
      "A live read on revenue, receivables, pipeline, and the work that needs a human.",
  },
  receivables: {
    title: "Receivables",
    subtitle:
      "Every invoice carrying a balance, with the backend's overdue threshold applied.",
  },
  pipeline: {
    title: "Pipeline",
    subtitle: "Review open quotes and stalled orders reported by the backend.",
  },
  leads: {
    title: "Leads",
    subtitle: "Conversations that stalled before they became orders.",
  },
  "ai-review": {
    title: "AI Review",
    subtitle: "Review agent recommendations and drafts.",
  },
  chats: {
    title: "Chats",
    subtitle: "Stored conversations with the assistant, ready to inspect or continue.",
  },
};

export function Dashboard({
  actionCenter,
  onSelectCustomer,
  onTabChange,
}: {
  actionCenter: ReactNode;
  onSelectCustomer: (customerId: number | null) => void;
  onTabChange?: (tab: string) => void;
}) {
  const { snapshot, error: liveError, refresh } = useLiveData();
  const [activeTab, setActiveTab] = useState("command-center");
  const [verticalNav, setVerticalNav] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(min-width: 1024px)");
    const update = () => setVerticalNav(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  const signals = useSignals({
    activeTab,
    snapshotAt: snapshot?.generated_at ?? null,
  });
  const leads = useStuckLeads({
    activeTab,
    reloadKey: signals.reloadKey,
    reload: signals.retry,
  });

  function selectTab(tab: string) {
    setActiveTab(tab);
    onTabChange?.(tab);
    signals.onTabChange(tab);
    if (tab === "leads") leads.setLeadRowLimit(() => ROW_PAGE_SIZE);
  }

  const meta = PAGE_META[activeTab] ?? PAGE_META["command-center"];
  const hasError = Boolean(liveError || signals.signalsError);

  return (
    <section aria-labelledby="dashboard-title" className="space-y-5 sm:space-y-6">
      {/* Page heading: one consistent contract for every tab */}
      <header className="page-header">
        <div className="min-w-0">
          <p className="page-eyebrow">Biztek AI / Operations</p>
          <h1
            id="dashboard-title"
            className="page-title"
          >
            {meta.title}
          </h1>
          <p className="page-subtitle">{meta.subtitle}</p>
        </div>

        <div className="page-meta" aria-label="Data freshness">
          <div className="meta-item">
            <span className="meta-label">Metrics as of</span>
            <span className="meta-value">
              {snapshot ? formatDateTime(snapshot.generated_at) : "—"}
            </span>
          </div>
          <span className="meta-divider hidden sm:block" aria-hidden="true" />
          <div className="meta-item">
            <span className="meta-label">Signals as of</span>
            <span className="meta-value">
              {signals.signals ? formatDateTime(signals.signals.generated_at) : "—"}
            </span>
          </div>
        </div>
      </header>

      {hasError && (
        <div
          className="flex items-start gap-3 rounded-xl border border-negative-border bg-negative-muted px-4 py-3 shadow-sm"
          role="alert"
        >
          <AlertTriangle
            size={16}
            className="mt-0.5 shrink-0 text-negative"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-negative-foreground">
              Live data interrupted
            </p>
            {liveError && <p className="mt-0.5 text-xs leading-relaxed text-negative-foreground/90">{liveError}</p>}
            {signals.signalsError && <p className="mt-0.5 text-xs leading-relaxed text-negative-foreground/90">{signals.signalsError}</p>}
          </div>
        </div>
      )}

      <Tabs
        value={activeTab}
        onValueChange={(value) => selectTab(String(value))}
        orientation={verticalNav ? "vertical" : "horizontal"}
        className="dashboard-tabs"
      >
        {/* Desktop sidebar, horizontally scrollable on smaller screens. */}
        <nav className="dashboard-nav" aria-label="Dashboard navigation">
          <p className="dashboard-nav-label">Workspace</p>
          <TabsList
            variant="nav"
            aria-label="Dashboard sections"
            className="max-w-full"
          >
            {TABS.map((tab) => {
              const Icon = tab.icon;
              return (
                <TabsTrigger key={tab.value} value={tab.value} variant="nav">
                  <Icon
                    size={15}
                    className="nav-tab-icon shrink-0"
                    aria-hidden="true"
                  />
                  <span className="whitespace-nowrap">{tab.label}</span>
                </TabsTrigger>
              );
            })}
          </TabsList>
          <p className="dashboard-nav-hint">Scroll for more sections →</p>
        </nav>

        <TabsContent value="command-center">
          <CommandCenter />
        </TabsContent>

        <TabsContent value="overview">
          <OverviewPanel
            snapshot={snapshot}
            signals={signals.signals}
            stuckLeads={leads.stuckLeads}
            onSelectTab={selectTab}
            onRefresh={() => void refresh()}
            signalsError={signals.signalsError}
            leadsError={leads.stuckLeadsError}
            onRetrySignals={signals.retry}
          />
        </TabsContent>

        <TabsContent value="receivables">
          <ReceivablesPanel signals={signals} onSelectCustomer={onSelectCustomer} />
        </TabsContent>

        <TabsContent value="pipeline">
          <PipelinePanel signals={signals} onSelectCustomer={onSelectCustomer} />
        </TabsContent>

        <TabsContent value="leads">
          <LeadsPanel leads={leads} />
        </TabsContent>

        <TabsContent value="ai-review">{actionCenter}</TabsContent>

        <TabsContent value="chats">
          <ChatHistoryPanel onOpenThread={() => selectTab("command-center")} />
        </TabsContent>
      </Tabs>

      <footer className="flex items-center justify-center gap-2 border-t border-border/60 pt-5 text-center font-mono text-[11px] leading-relaxed text-muted">
        <ShieldCheck size={12} className="shrink-0" aria-hidden="true" />
        All totals are computed by the backend. This UI only formats and renders them.
      </footer>
    </section>
  );
}
