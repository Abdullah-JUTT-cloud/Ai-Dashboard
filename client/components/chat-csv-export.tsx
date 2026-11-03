"use client";

import { useState } from "react";
import { Check, Download, Eye, FileSpreadsheet } from "lucide-react";

export function downloadCsvBlob(filename: string, content: string) {
  if (typeof window === "undefined") return;
  const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function viewCsvBlob(content: string) {
  if (typeof window === "undefined") return;
  const blob = new Blob([content], { type: "text/plain;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
}

export function parseCsvRows(content: string): number {
  const lines = content.trim().split("\n").filter((l) => l.trim().length > 0);
  if (lines.length <= 1) return 0;
  return lines.length - 1; // subtract header row
}

export function findFilenameInText(text: string): string | null {
  const linkMatch = /\[(?:Download\b|View\b|[^\]]+\.csv\b)[^\]]*\]\((?:#download:([^)\s]+)|([^)\s]+\.csv))\)/i.exec(text);
  if (linkMatch) return linkMatch[1] || linkMatch[2];
  const csvMatch = /\b([a-zA-Z0-9_-]+\.csv)\b/i.exec(text);
  if (csvMatch) return csvMatch[1];
  return null;
}

export function ChatCsvCard({
  filename,
  content,
  rowCount,
}: {
  filename: string;
  content: string;
  rowCount?: number;
}) {
  const [downloaded, setDownloaded] = useState(false);
  const rows = rowCount ?? parseCsvRows(content);

  function handleDownload() {
    downloadCsvBlob(filename, content);
    setDownloaded(true);
    setTimeout(() => setDownloaded(false), 2000);
  }

  function handleView() {
    viewCsvBlob(content);
  }

  return (
    <div
      className="csv-export-card my-3 overflow-hidden rounded-lg border border-border bg-surface-raised shadow-sm"
      data-testid="csv-export-card"
    >
      <div className="flex items-center justify-between gap-3 border-b border-border bg-surface px-3 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <FileSpreadsheet size={16} className="text-accent shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <span
              className="font-mono text-xs font-semibold text-foreground truncate block"
              data-testid="csv-filename"
            >
              {filename}
            </span>
            <span className="text-[11px] text-muted" data-testid="csv-row-count">
              {rows} {rows === 1 ? "row" : "rows"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            onClick={handleView}
            className="inline-flex items-center gap-1 rounded border border-border bg-surface-raised px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-surface-overlay hover:text-accent"
            title="Open CSV in new tab for viewing"
            aria-label="View CSV file"
            data-testid="csv-view-btn"
          >
            <Eye size={12} aria-hidden="true" />
            View
          </button>
          <button
            type="button"
            onClick={handleDownload}
            className="inline-flex items-center gap-1 rounded bg-accent px-2.5 py-1 text-xs font-semibold text-accent-foreground transition-all hover:opacity-90"
            title="Download CSV file"
            aria-label="Download CSV file"
            data-testid="csv-download-btn"
          >
            {downloaded ? <Check size={12} aria-hidden="true" /> : <Download size={12} aria-hidden="true" />}
            {downloaded ? "Downloaded" : "Download"}
          </button>
        </div>
      </div>
      <pre className="cmd-pre max-h-48 overflow-auto p-3 text-xs font-mono text-muted leading-relaxed">
        <code>{content}</code>
      </pre>
    </div>
  );
}
