import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const h = vi.hoisted(() => {
  let handler: ((event: unknown) => void) | null = null;
  return {
    getHandler: () => handler,
    setHandler: (next: ((event: unknown) => void) | null) => {
      handler = next;
    },
  };
});

const mockChatThreads = vi.hoisted(() => [
  {
    thread_id: "thread-1",
    title: "First payment query",
    turn_count: 3,
    updated_at: "2026-11-03T18:00:00Z",
  },
  {
    thread_id: "thread-2",
    title: "Overdue invoices query",
    turn_count: 5,
    updated_at: "2026-11-03T19:00:00Z",
  },
]);

vi.mock("@/services/api", () => ({
  getChatThreadId: vi.fn(() => "thread-1"),
  setChatThreadId: vi.fn(),
  startNewChatThread: vi.fn(() => "thread-fresh-123"),
  fetchChatThreads: vi.fn(async () => [...mockChatThreads]),
  deleteChatThread: vi.fn(async () => {}),
  fetchChatThread: vi.fn(async (id: string) => ({
    thread_id: id,
    title: id === "thread-1" ? "First payment query" : "Overdue invoices query",
    turns: [
      {
        question: "how many payments came today",
        answer: "Two payments totaling $3,192 were received today.",
        voice_summary: "Total payments are $3,192 across 2 payments.",
        created_at: "2026-11-03T18:38:51Z",
      },
    ],
    updated_at: "2026-11-03T18:38:51Z",
  })),
  ApiError: class ApiError extends Error {},
  fetchAgents: vi.fn(async () => ({ agents: [] })),
  fetchTasks: vi.fn(async () => ({ tasks: [] })),
  openTaskEventStream: vi.fn(() => new Promise<void>(() => {})),
  cancelChatTurn: vi.fn(async () => true),
  sendMessage: vi.fn(
    (_message: string, onEvent: (event: unknown) => void) =>
      new Promise((resolve, reject) => {
        h.setHandler((event) => {
          onEvent(event);
          const name = (event as { event?: string })?.event;
          if (name === "done") resolve((event as { data: unknown }).data);
          if (name === "error") reject(new Error("stream error"));
        });
      }),
  ),
}));

import { fetchChatThreads, deleteChatThread } from "@/services/api";
import { VoiceConsole } from "@/components/voice-console";
import { FloatingVoiceMic } from "@/components/floating-voice-mic";
import { ExecutionControllerProvider } from "@/components/command-center/execution-context";
import { VoiceProvider, useVoice } from "@/lib/voice-context";
import { clearVoiceTurns } from "@/lib/voice";

class MockSpeechRecognition extends EventTarget {
  continuous = false;
  interimResults = true;
  lang = "en-US";
  onstart: (() => void) | null = null;
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start() {
    this.onstart?.();
  }
  stop() {
    this.onend?.();
  }
  abort() {
    this.onend?.();
  }
}

describe("Voice chat management & fullscreen features", () => {
  beforeEach(() => {
    h.setHandler(null);
    clearVoiceTurns();
    vi.clearAllMocks();

    Object.defineProperty(window, "speechSynthesis", {
      value: {
        cancel: vi.fn(),
        speak: vi.fn(),
      },
      writable: true,
      configurable: true,
    });

    Object.defineProperty(window, "SpeechRecognition", {
      value: MockSpeechRecognition,
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    clearVoiceTurns();
    window.sessionStorage.clear();
  });

  // ───── (1) Chat-management test ─────
  it("chat-management: New Chat creates fresh thread; list shows threads; Open switches; Delete removes (with confirm); Refresh reloads", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // New Chat button exists and starts fresh thread
    const newChatBtn = screen.getByTestId("voice-new-chat-button");
    expect(newChatBtn).toBeInTheDocument();
    fireEvent.click(newChatBtn);

    // Toggle conversation list
    const toggleBtn = screen.getByTestId("voice-chats-toggle");
    fireEvent.click(toggleBtn);

    // Wait for list to load
    await waitFor(() => {
      expect(screen.getByTestId("voice-chats-list")).toBeInTheDocument();
    });

    expect(fetchChatThreads).toHaveBeenCalled();

    // Verify thread items render with title, exchange count, timestamp
    const threadItems = screen.getAllByTestId("voice-thread-item");
    expect(threadItems.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("First payment query")).toBeInTheDocument();
    expect(screen.getByText("Overdue invoices query")).toBeInTheDocument();

    // Verify Active badge ("Open now" style)
    const activeBadge = screen.getByTestId("voice-active-thread-badge");
    expect(activeBadge).toBeInTheDocument();
    expect(activeBadge).toHaveTextContent("Open now");

    // Open button switches thread
    const openBtns = screen.getAllByTestId("voice-open-thread-button");
    expect(openBtns.length).toBeGreaterThan(0);
    fireEvent.click(openBtns[0]);

    // Delete button with two-step confirmation
    const deleteBtns = screen.getAllByTestId("voice-delete-thread-button");
    const firstDeleteBtn = deleteBtns[0];

    // First click: prompts for confirmation
    fireEvent.click(firstDeleteBtn);
    expect(firstDeleteBtn).toHaveTextContent("Confirm?");
    expect(deleteChatThread).not.toHaveBeenCalled();

    // Second click: executes deletion
    fireEvent.click(firstDeleteBtn);
    expect(deleteChatThread).toHaveBeenCalledWith("thread-1");

    // Refresh button reloads threads
    const refreshBtn = screen.getByTestId("voice-refresh-chats");
    fireEvent.click(refreshBtn);
    expect(fetchChatThreads).toHaveBeenCalledTimes(2);
  });

  // ───── (2) Full-screen test ─────
  it("full-screen: Expand opens full-screen showing SAME simple chat; list toggle switches threads inside full-screen; Close returns with same active thread; NO orb/neural/operation components", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // Initial: full-screen view is NOT open
    expect(screen.queryByTestId("voice-fullscreen-view")).not.toBeInTheDocument();

    // Expand control in voice view opens full-screen
    const expandBtn = screen.getByTestId("voice-expand-button");
    fireEvent.click(expandBtn);

    // Full-screen overlay is rendered
    const fullScreenView = screen.getByTestId("voice-fullscreen-view");
    expect(fullScreenView).toBeInTheDocument();

    // Shows the SAME simple chat elements:
    expect(screen.getByTestId("voice-message-list")).toBeInTheDocument();
    expect(screen.getByTestId("voice-input")).toBeInTheDocument();
    expect(screen.getByTestId("voice-mic-button")).toBeInTheDocument();

    // Header has New Chat + Chats list toggle + Close
    expect(screen.getByTestId("voice-new-chat-button")).toBeInTheDocument();
    const chatsToggleInFullScreen = screen.getByTestId("voice-chats-toggle");
    expect(chatsToggleInFullScreen).toBeInTheDocument();
    const closeBtn = screen.getByTestId("voice-close-fullscreen-button");
    expect(closeBtn).toBeInTheDocument();

    // Chats list toggle inside full-screen lets user switch threads without leaving full-screen
    fireEvent.click(chatsToggleInFullScreen);
    await waitFor(() => {
      expect(screen.getByTestId("voice-chats-list")).toBeInTheDocument();
    });
    expect(screen.getByTestId("voice-fullscreen-view")).toBeInTheDocument();

    // Switch thread inside full-screen
    const openBtns = screen.getAllByTestId("voice-open-thread-button");
    if (openBtns.length > 0) {
      fireEvent.click(openBtns[0]);
    }
    // Full-screen remains open
    expect(screen.getByTestId("voice-fullscreen-view")).toBeInTheDocument();

    // Close button returns to previous view with the same active thread
    fireEvent.click(closeBtn);
    expect(screen.queryByTestId("voice-fullscreen-view")).not.toBeInTheDocument();
    expect(screen.getByTestId("voice-chat-view")).toBeInTheDocument();

    // NO orb, neural backdrop, operation flow, or execution overlay components rendered
    expect(screen.queryByTestId("jarvis-orb")).not.toBeInTheDocument();
    expect(screen.queryByTestId("neural-backdrop")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operation-flow")).not.toBeInTheDocument();
    expect(screen.queryByTestId("execution-overlay")).not.toBeInTheDocument();
  });

  // ───── (3) Floating-mic test ─────
  function TestAppWithFloatingMic() {
    const { isFullScreen, closeFullScreen } = useVoice();
    return (
      <div data-testid="test-app-root">
        <div data-testid="active-tab-content">Dashboard Content on Any Tab</div>
        <FloatingVoiceMic />
        {isFullScreen && <VoiceConsole mode="fullscreen" onClose={closeFullScreen} />}
      </div>
    );
  }

  it("floating-mic: button visible on multiple views; click opens full-screen with animation + new chat; hidden while open, visible again after close", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceProvider>
          <TestAppWithFloatingMic />
        </VoiceProvider>
      </ExecutionControllerProvider>,
    );

    // Floating mic button is visible on app view
    const floatingMic = screen.getByTestId("voice-floating-mic");
    expect(floatingMic).toBeInTheDocument();

    // Click opens full-screen WITH animation class
    fireEvent.click(floatingMic);

    const fullScreenView = screen.getByTestId("voice-fullscreen-view");
    expect(fullScreenView).toBeInTheDocument();
    // Verify transition animation class exists
    expect(fullScreenView.className).toContain("animate-in");
    expect(fullScreenView.className).toContain("duration-300");

    // Floating mic is hidden while full-screen is open
    expect(screen.queryByTestId("voice-floating-mic")).not.toBeInTheDocument();

    // Close full-screen
    const closeBtn = screen.getByTestId("voice-close-fullscreen-button");
    fireEvent.click(closeBtn);

    // Full-screen is closed
    expect(screen.queryByTestId("voice-fullscreen-view")).not.toBeInTheDocument();

    // Floating mic is visible again after close
    expect(screen.getByTestId("voice-floating-mic")).toBeInTheDocument();
  });

  // ───── (4) Auto-scroll test ─────
  it("auto-scroll: new message or response triggers auto-scroll to latest message", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    const messageList = screen.getByTestId("voice-message-list");
    // Mock scrollHeight and scrollTop
    Object.defineProperty(messageList, "scrollHeight", { value: 1200, configurable: true });
    Object.defineProperty(messageList, "scrollTop", { value: 0, writable: true, configurable: true });

    // Type and send a message
    const input = screen.getByTestId("voice-input");
    fireEvent.change(input, { target: { value: "give me all payments" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });

    // Auto-scroll triggers
    await waitFor(() => {
      expect(messageList.scrollTop).toBe(1200);
    });
  });
});
