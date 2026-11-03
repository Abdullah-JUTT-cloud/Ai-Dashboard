import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearVoiceTurns,
  consumeVoiceTurn,
  detectLanguage,
  getDeterministicFallbackSummary,
  isSpeechRecognitionSupported,
  isSpeechSynthesisSupported,
  isVoiceTurn,
  markVoiceTurn,
  speakVoiceSummary,
  stopSpeech,
} from "@/lib/voice";

describe("voice utilities", () => {
  beforeEach(() => {
    clearVoiceTurns();
  });

  afterEach(() => {
    clearVoiceTurns();
    vi.restoreAllMocks();
  });

  describe("voice origin tracking", () => {
    it("tracks voice origin for spoken prompts and ignores untracked typed prompts", () => {
      markVoiceTurn("which invoices are overdue");

      expect(isVoiceTurn("which invoices are overdue")).toBe(true);
      expect(isVoiceTurn("which invoices are overdue ")).toBe(true); // trims whitespace
      expect(isVoiceTurn("typed question")).toBe(false);

      // consumeVoiceTurn returns true once, then false
      expect(consumeVoiceTurn("which invoices are overdue")).toBe(true);
      expect(consumeVoiceTurn("which invoices are overdue")).toBe(false);
      expect(isVoiceTurn("which invoices are overdue")).toBe(false);
    });

    it("clears voice turns completely on reset", () => {
      markVoiceTurn("turn 1");
      markVoiceTurn("turn 2");
      clearVoiceTurns();
      expect(isVoiceTurn("turn 1")).toBe(false);
      expect(isVoiceTurn("turn 2")).toBe(false);
    });
  });

  describe("language detection", () => {
    it("detects Urdu script and sets ur-PK", () => {
      expect(detectLanguage("کتنے بل واجب الادا ہیں؟")).toBe("ur-PK");
      expect(detectLanguage("Overdue invoices کتنے ہیں؟")).toBe("ur-PK");
    });

    it("defaults to en-US for English and other standard text", () => {
      expect(detectLanguage("Which invoices are overdue?")).toBe("en-US");
      expect(detectLanguage("3 customers in queue")).toBe("en-US");
    });
  });

  describe("speech synthesis helper", () => {
    let mockCancel: ReturnType<typeof vi.fn>;
    let mockSpeak: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      mockCancel = vi.fn();
      mockSpeak = vi.fn();

      class MockUtterance {
        text: string;
        lang = "en-US";
        onstart: (() => void) | null = null;
        onend: (() => void) | null = null;
        onerror: ((err: unknown) => void) | null = null;
        constructor(text: string) {
          this.text = text;
        }
      }

      Object.defineProperty(window, "speechSynthesis", {
        value: {
          cancel: mockCancel,
          speak: mockSpeak,
        },
        writable: true,
        configurable: true,
      });

      Object.defineProperty(window, "SpeechSynthesisUtterance", {
        value: MockUtterance,
        writable: true,
        configurable: true,
      });
    });

    it("cancels prior speech and speaks the summary", () => {
      const onStart = vi.fn();
      const onEnd = vi.fn();

      const cancel = speakVoiceSummary(
        "Overdue balance is $1,777,747 across 270 invoices.",
        "en-US",
        { onStart, onEnd },
      );

      expect(mockCancel).toHaveBeenCalled();
      expect(mockSpeak).toHaveBeenCalledTimes(1);

      // Trigger the utterance onstart & onend
      const utterance = mockSpeak.mock.calls[0][0];
      expect(utterance.text).toBe("Overdue balance is $1,777,747 across 270 invoices.");
      expect(utterance.lang).toBe("en-US");

      utterance.onstart?.();
      expect(onStart).toHaveBeenCalledTimes(1);

      utterance.onend?.();
      expect(onEnd).toHaveBeenCalledTimes(1);

      cancel();
      expect(mockCancel).toHaveBeenCalledTimes(2);
    });

    it("handles speech errors gracefully without throwing", () => {
      const onError = vi.fn();
      const onEnd = vi.fn();

      speakVoiceSummary("Short summary", "en-US", { onError, onEnd });
      const utterance = mockSpeak.mock.calls[0][0];

      utterance.onerror?.(new Error("Audio hardware error"));
      expect(onError).toHaveBeenCalledTimes(1);
      expect(onEnd).toHaveBeenCalledTimes(1);
    });

    it("stopSpeech calls window.speechSynthesis.cancel safely", () => {
      stopSpeech();
      expect(mockCancel).toHaveBeenCalledTimes(1);
    });
  });

  describe("feature detection", () => {
    it("detects when STT is present or absent", () => {
      delete (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition;
      delete (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
      expect(isSpeechRecognitionSupported()).toBe(false);

      (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition = vi.fn();
      expect(isSpeechRecognitionSupported()).toBe(true);
    });

    it("detects when TTS is present or absent", () => {
      expect(isSpeechSynthesisSupported()).toBe(true);
    });
  });

  describe("getDeterministicFallbackSummary", () => {
    it("returns first sentence when under 280 characters", () => {
      const answer = "Two payments were received today, totaling $3,192. Here are more details that follow.";
      expect(getDeterministicFallbackSummary(answer)).toBe("Two payments were received today, totaling $3,192.");
    });

    it("returns empty string when answer is empty or whitespace", () => {
      expect(getDeterministicFallbackSummary("")).toBe("");
      expect(getDeterministicFallbackSummary("   ")).toBe("");
    });

    it("returns first 280 chars when first sentence exceeds 280 characters", () => {
      const veryLong = "A".repeat(300) + ". Second sentence.";
      const result = getDeterministicFallbackSummary(veryLong);
      expect(result).toHaveLength(280);
      expect(result).toBe("A".repeat(280));
    });

    it("NEVER returns the banned line", () => {
      const banned = "The full answer is shown in text.";
      // Empty
      expect(getDeterministicFallbackSummary("")).not.toBe(banned);
      // Whitespace
      expect(getDeterministicFallbackSummary("   ")).not.toBe(banned);
      // Very long
      expect(getDeterministicFallbackSummary("A".repeat(300) + ". End.")).not.toBe(banned);
      // Normal
      expect(getDeterministicFallbackSummary("Normal sentence here.")).not.toBe(banned);
      // No sentence boundary
      expect(getDeterministicFallbackSummary("No period at end")).not.toBe(banned);
    });
  });
});

