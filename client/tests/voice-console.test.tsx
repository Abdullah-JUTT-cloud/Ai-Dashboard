import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const h = vi.hoisted(() => {
  let handler: ((event: unknown) => void) | null = null;
  return {
    getHandler: () => handler,
    setHandler: (next: ((event: unknown) => void) | null) => {
      handler = next;
    },
  };
});

vi.mock("@/services/api", () => ({
  getChatThreadId: vi.fn(() => "test-thread"),
  setChatThreadId: vi.fn(),
  startNewChatThread: vi.fn(() => "test-thread-new"),
  fetchChatThread: vi.fn(async () => ({
    thread_id: "test-thread",
    title: "Test Thread",
    turns: [
      {
        question: "which invoices are overdue",
        answer: "Overdue balance is $1,777,747 across 270 invoices.",
        voice_summary: "Overdue balance is $1,777,747 across 270 invoices. Largest is Pure 7 at $357,500.",
        created_at: new Date().toISOString(),
      },
      {
        question: "typed question",
        answer: "Here is a typed answer.",
        voice_summary: "Here is a typed summary.",
        created_at: new Date().toISOString(),
      },
    ],
    updated_at: null,
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

import { sendMessage } from "@/services/api";
import { VoiceConsole } from "@/components/voice-console";
import { ChatInterface } from "@/components/chat-interface";
import { ExecutionControllerProvider } from "@/components/command-center/execution-context";
import { clearVoiceTurns } from "@/lib/voice";

class MockSpeechSynthesisUtterance {
  text: string;
  lang = "en-US";
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  constructor(text: string) {
    this.text = text;
  }
}

let activeRecognitionInstance: MockSpeechRecognition | null = null;

class MockSpeechRecognition extends EventTarget {
  continuous = false;
  interimResults = true;
  lang = "en-US";
  onstart: (() => void) | null = null;
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;

  constructor() {
    super();
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    activeRecognitionInstance = this;
  }

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

describe("VoiceConsole component", () => {
  let mockCancel: ReturnType<typeof vi.fn>;
  let mockSpeak: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    h.setHandler(null);
    clearVoiceTurns();
    activeRecognitionInstance = null;
    mockCancel = vi.fn();
    mockSpeak = vi.fn();

    Object.defineProperty(window, "speechSynthesis", {
      value: {
        cancel: mockCancel,
        speak: mockSpeak,
      },
      writable: true,
      configurable: true,
    });

    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      value: MockSpeechSynthesisUtterance,
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

  // ───── Requirement 1: Chat layout test ─────

  it("chat-layout: renders message list + input + side mic; no popup overlay", () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // Voice chat view is rendered
    expect(screen.getByTestId("voice-chat-view")).toBeInTheDocument();
    // Message list exists
    expect(screen.getByTestId("voice-message-list")).toBeInTheDocument();
    // Input exists
    expect(screen.getByTestId("voice-input")).toBeInTheDocument();
    // Mic button on the side of input
    expect(screen.getByTestId("voice-mic-button")).toBeInTheDocument();

    // NO popup overlay component
    expect(screen.queryByTestId("voice-fullscreen-popup")).not.toBeInTheDocument();
    // NO orb, neural, operation components
    expect(screen.queryByTestId("jarvis-orb")).not.toBeInTheDocument();
    expect(screen.queryByTestId("neural-backdrop")).not.toBeInTheDocument();
    expect(screen.queryByTestId("operation-flow")).not.toBeInTheDocument();
    expect(screen.queryByTestId("execution-overlay")).not.toBeInTheDocument();
  });

  it("chat-layout: assistant bubble shows FULL answer text, no collapsed details, no summary card", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // Submit a voice turn
    const micButton = screen.getByTestId("voice-mic-button");
    fireEvent.click(micButton);

    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "which invoices are overdue", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    // Complete the turn
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-voice", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Overdue balance is $1,777,747 across 270 invoices.",
          thread_id: "test-thread",
          task_id: "task-voice",
        },
      });
    });

    // Assistant bubble shows full text
    await waitFor(() => {
      expect(
        screen.getAllByText(/Overdue balance is \$1,777,747 across 270 invoices/i).length,
      ).toBeGreaterThan(0);
    });

    // NO <details> elements (collapsed disclosures)
    const details = document.querySelectorAll("details");
    expect(details.length).toBe(0);

    // NO summary card in chat (no "Spoken Summary" heading)
    expect(screen.queryByText("Spoken Summary")).not.toBeInTheDocument();
  });

  it("chat-layout: exactly ONE Stop control while speaking, no other badges", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // Trigger voice
    fireEvent.click(screen.getByTestId("voice-mic-button"));
    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "which invoices are overdue", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    // Complete turn
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-voice", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Overdue balance is $1,777,747 across 270 invoices.",
          thread_id: "test-thread",
          task_id: "task-voice",
        },
      });
    });

    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(1);
    });

    // Trigger onstart to set isSpeaking = true
    const utterance = mockSpeak.mock.calls[0][0];
    await act(async () => {
      utterance.onstart?.();
    });

    // Exactly ONE Stop button
    const stopButtons = screen.getAllByTestId("voice-stop-button");
    expect(stopButtons).toHaveLength(1);

    // No "Speaking summary" badge / "Spoken audio playing" banner
    expect(screen.queryByText("Speaking summary")).not.toBeInTheDocument();
    expect(screen.queryByText("Spoken audio playing")).not.toBeInTheDocument();

    // Tapping stop halts speech
    fireEvent.click(stopButtons[0]);
    expect(mockCancel).toHaveBeenCalled();
  });

  // ───── Requirement 2: Typed-prompt test ─────

  it("typed-prompt: transcript appears as input text during listening; submitted text equals finalized transcript", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    const input = screen.getByTestId("voice-input") as HTMLTextAreaElement;

    // Start listening
    fireEvent.click(screen.getByTestId("voice-mic-button"));

    // Simulate interim transcript
    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: false,
            0: { transcript: "which invoices", confidence: 0.8 },
            length: 1,
          },
        ],
      });
    });

    // Input shows the interim text
    expect(input.value).toContain("which invoices");

    // Simulate final transcript
    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "which invoices are overdue", confidence: 0.95 },
            length: 1,
          },
        ],
      });
    });

    expect(input.value).toContain("which invoices are overdue");

    // Submit via Done
    fireEvent.click(screen.getByLabelText("Done speaking"));

    // Submitted message equals the finalized transcript
    expect(sendMessage).toHaveBeenCalledWith(
      "which invoices are overdue",
      expect.any(Function),
    );
  });

  // ───── Requirement 3: Summary-only-speech test ─────

  it("summary-only-speech: spoken text equals the turn's voice_summary, never the full answer", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    fireEvent.click(screen.getByTestId("voice-mic-button"));
    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "which invoices are overdue", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    // Complete the voice turn
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-voice", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Overdue balance is $1,777,747 across 270 invoices.",
          thread_id: "test-thread",
          task_id: "task-voice",
        },
      });
    });

    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(1);
    });

    const utterance = mockSpeak.mock.calls[0][0];
    // Spoken text is the voice_summary from the server
    expect(utterance.text).toBe(
      "Overdue balance is $1,777,747 across 270 invoices. Largest is Pure 7 at $357,500.",
    );
    // And NOT the full answer text
    expect(utterance.text).not.toBe(
      "Overdue balance is $1,777,747 across 270 invoices.",
    );
  });

  // ───── Requirement 4: Banned-line test (client) ─────

  it("banned-line: getDeterministicFallbackSummary NEVER returns the banned string", async () => {
    const { getDeterministicFallbackSummary: getFallback } = await import("@/lib/voice");
    const banned = "The full answer is shown in text.";

    // Normal case
    expect(getFallback("Two payments received.")).not.toBe(banned);
    expect(getFallback("Two payments received.")).toBe("Two payments received.");

    // Empty
    expect(getFallback("")).not.toBe(banned);

    // Very long without sentence boundary
    const longText = "A".repeat(300);
    expect(getFallback(longText)).not.toBe(banned);
    expect(getFallback(longText).length).toBe(280);

    // Very long with sentence
    const longSentence = "A".repeat(300) + ". End.";
    expect(getFallback(longSentence)).not.toBe(banned);

    // All results are non-empty speakable text (except empty input)
    expect(getFallback("Two payments received.").length).toBeGreaterThan(0);
    expect(getFallback(longText).length).toBeGreaterThan(0);
    expect(getFallback(longSentence).length).toBeGreaterThan(0);
  });

  // ───── Requirement 5: Ctrl+M push-to-talk test ─────

  it("ctrl-m: keydown (ctrl+m) starts listening; keyup stops + submits; key-repeat ignored; empty transcript no submit; no 'm' inserted", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    const input = screen.getByTestId("voice-input") as HTMLTextAreaElement;

    // 1. Press Ctrl+M (not repeat) -> starts listening
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "m", ctrlKey: true, repeat: false, bubbles: true }),
      );
    });

    expect(activeRecognitionInstance).not.toBeNull();

    // 2. Key repeat should NOT restart
    const firstInstance = activeRecognitionInstance;
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "m", ctrlKey: true, repeat: true, bubbles: true }),
      );
    });
    // Same instance, not replaced
    expect(activeRecognitionInstance).toBe(firstInstance);

    // 3. Simulate speech while held
    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "show overdue invoices", confidence: 0.95 },
            length: 1,
          },
        ],
      });
    });

    // Input shows transcript (no 'm' character inserted)
    expect(input.value).toContain("show overdue invoices");
    expect(input.value).not.toMatch(/^m/); // no 'm' at start

    // 4. Release Ctrl+M -> stops + submits
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keyup", { key: "m", bubbles: true }),
      );
    });

    expect(sendMessage).toHaveBeenCalledWith(
      "show overdue invoices",
      expect.any(Function),
    );
  });

  it("ctrl-m: empty transcript on release submits nothing", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // Press Ctrl+M
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "m", ctrlKey: true, repeat: false, bubbles: true }),
      );
    });

    expect(activeRecognitionInstance).not.toBeNull();

    // Release immediately (no speech)
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keyup", { key: "m", bubbles: true }),
      );
    });

    // Nothing submitted
    expect(sendMessage).not.toHaveBeenCalled();
  });

  // ───── Existing tests (preserved) ─────

  it("shows unsupported message when SpeechRecognition is not available", () => {
    delete (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition;
    delete (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;

    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // Mic button should be disabled
    const micButton = screen.getByTestId("voice-mic-button");
    expect(micButton).toBeDisabled();
  });

  it("voice turn speaks voice_summary automatically, but typed turn does NOT auto-speak", async () => {
    render(
      <ExecutionControllerProvider>
        <div data-testid="chat-and-voice">
          <VoiceConsole />
          <ChatInterface />
        </div>
      </ExecutionControllerProvider>,
    );

    // 1. Submit a typed turn via ChatInterface
    const chatInput = screen.getByLabelText("Message Biztek Assistant");
    fireEvent.change(chatInput, { target: { value: "typed question" } });
    fireEvent.click(chatInput.closest("form")!.querySelector('button[type="submit"]')!);

    expect(sendMessage).toHaveBeenCalledWith("typed question", expect.any(Function));

    // Complete the typed turn
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-typed", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Here is a typed answer.",
          thread_id: "test-thread",
          task_id: "task-typed",
        },
      });
    });

    // Typed turn must NOT speak
    expect(mockSpeak).not.toHaveBeenCalled();

    // 2. Now start a voice turn via VoiceConsole
    const micButton = screen.getByTestId("voice-mic-button");
    fireEvent.click(micButton);

    expect(activeRecognitionInstance).not.toBeNull();

    // Simulate speech recognition delivering transcript
    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "which invoices are overdue", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    expect(sendMessage).toHaveBeenCalledWith("which invoices are overdue", expect.any(Function));

    // Complete the voice turn
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-voice", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Overdue balance is $1,777,747 across 270 invoices.",
          thread_id: "test-thread",
          task_id: "task-voice",
        },
      });
    });

    // Voice turn MUST speak voice_summary!
    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(1);
    });

    const utterance = mockSpeak.mock.calls[0][0];
    expect(utterance.text).toBe(
      "Overdue balance is $1,777,747 across 270 invoices. Largest is Pure 7 at $357,500.",
    );
  });

  it("Stop control immediately halts ongoing speech", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // Trigger voice recognition
    fireEvent.click(screen.getByTestId("voice-mic-button"));

    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "which invoices are overdue", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    // Complete the turn
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-voice", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Overdue balance is $1,777,747 across 270 invoices.",
          thread_id: "test-thread",
          task_id: "task-voice",
        },
      });
    });

    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(1);
    });

    // Utterance started speaking
    const utterance = mockSpeak.mock.calls[0][0];
    await act(async () => {
      utterance.onstart?.();
    });

    // Click Stop speech
    const stopButton = screen.getByTestId("voice-stop-button");
    fireEvent.click(stopButton);

    expect(mockCancel).toHaveBeenCalled();
  });

  it("speech error falls back gracefully to text only without breaking chat", async () => {
    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    fireEvent.click(screen.getByTestId("voice-mic-button"));

    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "which invoices are overdue", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    // Complete the turn
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-voice", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Overdue balance is $1,777,747 across 270 invoices.",
          thread_id: "test-thread",
          task_id: "task-voice",
        },
      });
    });

    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(1);
    });

    const utterance = mockSpeak.mock.calls[0][0];
    // Trigger speech error
    await act(async () => {
      utterance.onerror?.(new Error("Audio device disconnected"));
    });

    // Verify it settled cleanly, no crash, full text answer is displayed
    expect(
      screen.getAllByText(/Overdue balance is \$1,777,747 across 270 invoices/i).length,
    ).toBeGreaterThan(0);
  });

  it("pause test: simulated speech with 1-2s mid-speech pause does NOT submit; submits on >= 3s silence fallback", async () => {
    vi.useFakeTimers();
    try {
      render(
        <ExecutionControllerProvider>
          <VoiceConsole />
        </ExecutionControllerProvider>,
      );

      fireEvent.click(screen.getByTestId("voice-mic-button"));

      expect(activeRecognitionInstance).not.toBeNull();
      expect(activeRecognitionInstance?.continuous).toBe(true);

      // 1. User says first chunk: "how many payments"
      act(() => {
        activeRecognitionInstance?.onresult?.({
          resultIndex: 0,
          results: [
            {
              isFinal: true,
              0: { transcript: "how many payments", confidence: 0.95 },
              length: 1,
            },
          ],
        });
      });

      // 2. User pauses for 1.5s (1500ms) to think
      act(() => {
        vi.advanceTimersByTime(1500);
      });

      // Must NOT submit yet!
      expect(sendMessage).not.toHaveBeenCalled();

      // 3. User resumes speaking: "came today" (resets silence timer)
      act(() => {
        activeRecognitionInstance?.onresult?.({
          resultIndex: 1,
          results: [
            {
              isFinal: true,
              0: { transcript: "how many payments", confidence: 0.95 },
              length: 1,
            },
            {
              isFinal: true,
              0: { transcript: "came today", confidence: 0.95 },
              length: 1,
            },
          ],
        });
      });

      // 4. Another 1.5s pause
      act(() => {
        vi.advanceTimersByTime(1500);
      });

      // Still must NOT submit!
      expect(sendMessage).not.toHaveBeenCalled();

      // 5. Continuous silence reaches 3 seconds (remaining 1600ms)
      act(() => {
        vi.advanceTimersByTime(1600);
      });

      // Now >= 3s continuous silence fallback has fired!
      expect(sendMessage).toHaveBeenCalledTimes(1);
      expect(sendMessage).toHaveBeenCalledWith(
        "how many payments came today",
        expect.any(Function),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("pause test: simulated speech with short pause submits immediately on explicit user action (Done button)", async () => {
    vi.useFakeTimers();
    try {
      render(
        <ExecutionControllerProvider>
          <VoiceConsole />
        </ExecutionControllerProvider>,
      );

      fireEvent.click(screen.getByTestId("voice-mic-button"));

      // Speech arrives
      act(() => {
        activeRecognitionInstance?.onresult?.({
          resultIndex: 0,
          results: [
            {
              isFinal: true,
              0: { transcript: "show overdue invoices", confidence: 0.95 },
              length: 1,
            },
          ],
        });
      });

      // 1s pause (< 3s)
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(sendMessage).not.toHaveBeenCalled();

      // Explicit user action: click "Done"
      const doneButton = screen.getByLabelText("Done speaking");
      fireEvent.click(doneButton);

      // Immediately submits
      expect(sendMessage).toHaveBeenCalledTimes(1);
      expect(sendMessage).toHaveBeenCalledWith(
        "show overdue invoices",
        expect.any(Function),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("summary-binding test: two consecutive turns with different answers; second turn speaks second turn's summary", async () => {
    const { fetchChatThread } = await import("@/services/api");
    const mockFetchThread = vi.mocked(fetchChatThread);

    mockFetchThread.mockImplementation(async () => ({
      thread_id: "test-thread",
      title: "Test Thread",
      turns: [
        {
          task_id: "task-turn-1",
          question: "turn 1 prompt",
          answer: "Answer to turn 1.",
          voice_summary: "First Turn Summary.",
          created_at: new Date().toISOString(),
        },
      ],
      updated_at: null,
    }));

    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    // 1. First Turn
    fireEvent.click(screen.getByTestId("voice-mic-button"));

    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "turn 1 prompt", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    // Complete Turn 1
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-turn-1", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Answer to turn 1.",
          thread_id: "test-thread",
          task_id: "task-turn-1",
        },
      });
    });

    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(1);
    });
    expect(mockSpeak.mock.calls[0][0].text).toBe("First Turn Summary.");

    // 2. Prepare mock for Second Turn
    mockFetchThread.mockImplementation(async () => ({
      thread_id: "test-thread",
      title: "Test Thread",
      turns: [
        {
          task_id: "task-turn-1",
          question: "turn 1 prompt",
          answer: "Answer to turn 1.",
          voice_summary: "First Turn Summary.",
          created_at: new Date().toISOString(),
        },
        {
          task_id: "task-turn-2",
          question: "how many payments came today and how much price",
          answer: "Two payments were received today, totaling $3,192.",
          voice_summary: "Two payments received today totaling $3,192.",
          created_at: new Date().toISOString(),
        },
      ],
      updated_at: null,
    }));

    // Start Turn 2
    fireEvent.click(screen.getByTestId("voice-mic-button"));

    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "how many payments came today and how much price", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    // Complete Turn 2
    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-turn-2", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Two payments were received today, totaling $3,192.",
          thread_id: "test-thread",
          task_id: "task-turn-2",
        },
      });
    });

    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(2);
    });

    // Second turn's spoken summary MUST be Turn 2's summary, NEVER Turn 1's
    expect(mockSpeak.mock.calls[1][0].text).toBe("Two payments received today totaling $3,192.");
    expect(mockSpeak.mock.calls[1][0].text).not.toBe("First Turn Summary.");
  });

  it("missing-summary test: turn with empty voice_summary shows deterministic fallback, never stale summary", async () => {
    const { fetchChatThread } = await import("@/services/api");
    const mockFetchThread = vi.mocked(fetchChatThread);

    mockFetchThread.mockImplementation(async () => ({
      thread_id: "test-thread",
      title: "Test Thread",
      turns: [
        {
          task_id: "task-turn-fallback",
          question: "how many payments came today and how much price",
          answer: "Two payments were received today, totaling $3,192. Details are below.",
          voice_summary: "", // empty voice_summary
          created_at: new Date().toISOString(),
        },
      ],
      updated_at: null,
    }));

    render(
      <ExecutionControllerProvider>
        <VoiceConsole />
      </ExecutionControllerProvider>,
    );

    fireEvent.click(screen.getByTestId("voice-mic-button"));

    await act(async () => {
      activeRecognitionInstance?.onresult?.({
        resultIndex: 0,
        results: [
          {
            isFinal: true,
            0: { transcript: "how many payments came today and how much price", confidence: 0.95 },
            length: 1,
          },
        ],
      });
      activeRecognitionInstance?.onend?.();
    });

    await act(async () => {
      h.getHandler()?.({
        event: "task",
        data: { task_id: "task-turn-fallback", thread_id: "test-thread" },
      });
      h.getHandler()?.({
        event: "done",
        data: {
          text: "Two payments were received today, totaling $3,192. Details are below.",
          thread_id: "test-thread",
          task_id: "task-turn-fallback",
        },
      });
    });

    await waitFor(() => {
      expect(mockSpeak).toHaveBeenCalledTimes(1);
    });

    // Fallback must be first sentence of answer
    expect(mockSpeak.mock.calls[0][0].text).toBe(
      "Two payments were received today, totaling $3,192.",
    );
  });
});
