"use client";

// Data fetching for GET /api/leads/stuck, which feeds the Overview card and the
// Leads tab. Fetched once per reload generation; the in-flight promise is shared
// so StrictMode's double effect mount makes one request, and all state is
// written only after the request settles.

import { useEffect, useState } from "react";
import { fetchLeads } from "@/services/api";
import type { LeadsResponse, StuckLeadsResponse } from "@/types/api";
import { ROW_PAGE_SIZE } from "@/hooks/use-signals";

export interface StuckLeadsState {
  stuckLeads: StuckLeadsResponse | null;
  stuckLeadsError: string;
  isLoadingStuckLeads: boolean;
  leadRowLimit: number;
  setLeadRowLimit: (updater: (current: number) => number) => void;
  retry: () => void;
  allLeads?: LeadsResponse | null;
  allLeadsError?: string;
  isLoadingAllLeads?: boolean;
  selectedCategory?: string;
  setSelectedCategory?: (category: string) => void;
  categoryCounts?: Record<string, number>;
}

export function useStuckLeads({
  activeTab,
  reloadKey,
  reload,
}: {
  activeTab: string;
  reloadKey: number;
  reload: () => void;
}): StuckLeadsState {
  const [stuckLeads, setStuckLeads] = useState<StuckLeadsResponse | null>(null);
  const [stuckLeadsError, setStuckLeadsError] = useState("");
  const [isLoadingStuckLeads, setIsLoadingStuckLeads] = useState(true);
  const [leadRowLimit, setLeadRowLimit] = useState(ROW_PAGE_SIZE);

  const [selectedCategory, setSelectedCategory] = useState("Stuck");
  const [allLeads, setAllLeads] = useState<LeadsResponse | null>(null);
  const [allLeadsError, setAllLeadsError] = useState("");
  const [isLoadingAllLeads, setIsLoadingAllLeads] = useState(false);
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({});

  // Fetch live CRM leads (feeds both the Overview STUCK LEADS tile and the Leads tab)
  useEffect(() => {
    if (activeTab !== "leads" && activeTab !== "overview") return undefined;
    let isActive = true;

    fetchLeads({ category: selectedCategory, limit: leadRowLimit })
      .then((result) => {
        if (!isActive) return;
        setAllLeads(result);
        setAllLeadsError("");
        if (result.category_counts) {
          setCategoryCounts(result.category_counts);
        }
        const crmStuckCount =
          result.category_counts?.["Stuck"] ??
          (selectedCategory === "Stuck" ? result.matched_total : 0);
        setStuckLeads((prev) => ({
          generated_at: result.generated_at,
          days: 14,
          total: crmStuckCount,
          items:
            selectedCategory === "Stuck"
              ? result.items
              : (prev?.items ?? []),
        }));
        setStuckLeadsError("");
      })
      .catch((error: unknown) => {
        if (!isActive) return;
        const msg =
          error instanceof Error ? error.message : "Could not load leads.";
        setAllLeadsError(msg);
        setStuckLeadsError(msg);
      })
      .finally(() => {
        if (isActive) {
          setIsLoadingAllLeads(false);
          setIsLoadingStuckLeads(false);
        }
      });

    return () => {
      isActive = false;
    };
  }, [activeTab, reloadKey, selectedCategory, leadRowLimit]);

  function changeCategory(cat: string) {
    setIsLoadingAllLeads(true);
    setSelectedCategory(cat);
  }

  function changeRowLimit(updater: (current: number) => number) {
    setIsLoadingAllLeads(true);
    setLeadRowLimit(updater);
  }

  function retry() {
    setIsLoadingStuckLeads(true);
    setIsLoadingAllLeads(true);
    setStuckLeadsError("");
    setAllLeadsError("");
    reload();
  }

  return {
    stuckLeads,
    stuckLeadsError,
    isLoadingStuckLeads,
    leadRowLimit,
    setLeadRowLimit: changeRowLimit,
    retry,
    allLeads,
    allLeadsError,
    isLoadingAllLeads,
    selectedCategory,
    setSelectedCategory: changeCategory,
    categoryCounts,
  };
}
