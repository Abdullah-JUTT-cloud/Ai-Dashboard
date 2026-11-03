import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ChatMarkdown } from "@/components/chat-markdown";
import { ChatCsvCard, parseCsvRows, findFilenameInText } from "@/components/chat-csv-export";

describe("CSV Export Delivery", () => {
  it("renders a downloadable and viewable CSV file card when fenced code is csv", () => {
    const csvData = [
      'Name,Record Type,Company,Direction,Time,Message',
      '"Mrs Saad","Lead","","Inbound","2026-11-02T17:22:17+00:00","[call] Voice call (10s)"',
      '"Alaa Alaa","Lead","Alaa Co.","Outbound","2026-10-26T18:08:27+00:00","Hi Alaa"',
    ].join("\n");

    const answer = [
      "I have prepared the CSV file with 2 rows. You can download or view it below:",
      "[Download whatsapp-latest-2026-11-03.csv](#download:whatsapp-latest-2026-11-03.csv)",
      "",
      "```csv",
      csvData,
      "```",
    ].join("\n");

    const { container } = render(<ChatMarkdown text={answer} />);

    // Renders the filename and row count
    expect(screen.getByTestId("csv-filename")).toHaveTextContent("whatsapp-latest-2026-11-03.csv");
    expect(screen.getByTestId("csv-row-count")).toHaveTextContent("2 rows");

    // Both View and Download buttons exist
    const viewBtn = screen.getByTestId("csv-view-btn");
    const downloadBtn = screen.getByTestId("csv-download-btn");
    expect(viewBtn).toBeInTheDocument();
    expect(downloadBtn).toBeInTheDocument();

    // The download markdown link also renders with the filename
    const dlLink = screen.getByTestId("csv-download-link");
    expect(dlLink).toBeInTheDocument();
    expect(dlLink).toHaveAttribute("href", "#download:whatsapp-latest-2026-11-03.csv");

    // Pre contains verbatim CSV content
    const code = container.querySelector(".csv-export-card pre code");
    expect(code).toHaveTextContent('"Mrs Saad"');
    expect(code).toHaveTextContent('"Alaa Co."');
  });

  it("preserves strict company attribution and quoting in CSV content", () => {
    const csvData = [
      'Name,Record Type,Company,Direction,Time,Message',
      '"Mrs Saad","Lead","","Inbound","2026-11-02T17:22:17+00:00","[call] Voice call (10s)"',
      '"frank jr","Lead","Cape,s","Outbound","2026-10-21T15:12:31+00:00","Hi"',
      '"Frank Jr","Contact","Cape,s","Outbound","2026-10-21T15:12:31+00:00","Hi"',
    ].join("\n");

    render(<ChatCsvCard filename="whatsapp-latest-2026-11-03.csv" content={csvData} />);

    expect(parseCsvRows(csvData)).toBe(3);
    expect(screen.getByTestId("csv-row-count")).toHaveTextContent("3 rows");
  });

  it("triggers download and view on button clicks", () => {
    const createObjectURLMock = vi.fn().mockReturnValue("blob:mock-url");
    const revokeObjectURLMock = vi.fn();
    const openMock = vi.fn();

    global.URL.createObjectURL = createObjectURLMock;
    global.URL.revokeObjectURL = revokeObjectURLMock;
    global.window.open = openMock;

    const csvData = "Name,Company\nAlice,Wonderland\nBob,";
    render(<ChatCsvCard filename="test-export.csv" content={csvData} />);

    // Click download
    const downloadBtn = screen.getByTestId("csv-download-btn");
    fireEvent.click(downloadBtn);
    expect(createObjectURLMock).toHaveBeenCalled();
    expect(downloadBtn).toHaveTextContent("Downloaded");

    // Click view
    const viewBtn = screen.getByTestId("csv-view-btn");
    fireEvent.click(viewBtn);
    expect(openMock).toHaveBeenCalledWith("blob:mock-url", "_blank");
  });

  it("extracts filename from text helper", () => {
    const text1 = "Download the file here: [Download treatday-payments.csv](#download:treatday-payments.csv)";
    expect(findFilenameInText(text1)).toBe("treatday-payments.csv");

    const text2 = "Here is the export: whatsapp-latest-2026-11-03.csv";
    expect(findFilenameInText(text2)).toBe("whatsapp-latest-2026-11-03.csv");
  });
});
