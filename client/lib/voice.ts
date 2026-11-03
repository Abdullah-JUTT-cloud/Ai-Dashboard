// Browser Web Speech utilities for SpeechRecognition and speechSynthesis.
// Supports feature detection, Urdu/English language detection, voice-origin
// turn tracking, and speech playback with cancellation and error resilience.

// Global set tracking which turn prompts originated from voice input.
// Typed turns must not speak automatically.
const voiceOriginPrompts = new Set<string>();

export function markVoiceTurn(prompt: string): void {
  const normalized = prompt.trim();
  if (normalized) {
    voiceOriginPrompts.add(normalized);
  }
}

export function isVoiceTurn(prompt: string): boolean {
  const normalized = prompt.trim();
  return voiceOriginPrompts.has(normalized);
}

export function consumeVoiceTurn(prompt: string): boolean {
  const normalized = prompt.trim();
  if (voiceOriginPrompts.has(normalized)) {
    voiceOriginPrompts.delete(normalized);
    return true;
  }
  return false;
}

export function clearVoiceTurns(): void {
  voiceOriginPrompts.clear();
}

/**
 * Computes a deterministic voice summary fallback if voice_summary is missing or empty.
 * Fallback chain: (1) first sentence of the answer (<= 280 chars),
 * (2) if no sentence boundary, first 280 chars of the answer.
 */
export function getDeterministicFallbackSummary(answer: string): string {
  const trimmed = (answer || "").trim();
  if (!trimmed) {
    return "";
  }
  // Match first sentence ending in period, exclamation, question mark, or newline
  const match = trimmed.match(/^([^.!?\n]+[.!?]?)/);
  const first = match ? match[1].trim() : "";
  if (first && first.length <= 280) {
    return first;
  }
  // No sentence boundary or too long: return first 280 chars
  return trimmed.slice(0, 280);
}

export function isSpeechRecognitionSupported(): boolean {
  if (typeof window === "undefined") return false;
  return Boolean(
    (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition ||
    (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition,
  );
}

export function isSpeechSynthesisSupported(): boolean {
  if (typeof window === "undefined") return false;
  return (
    "speechSynthesis" in window &&
    typeof window.SpeechSynthesisUtterance !== "undefined"
  );
}

export type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance;

export interface SpeechRecognitionInstance extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: Event & { error?: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

export interface SpeechRecognitionEvent {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: {
      isFinal: boolean;
      length: number;
      [index: number]: {
        transcript: string;
        confidence: number;
      };
    };
  };
}

export function getSpeechRecognition(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const globalWin = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return globalWin.SpeechRecognition || globalWin.webkitSpeechRecognition || null;
}

/**
 * Detect language from text content.
 * Returns "ur-PK" if Arabic/Urdu unicode range characters are detected,
 * otherwise defaults to "en-US".
 */
export function detectLanguage(text: string): string {
  if (/[\u0600-\u06FF]/.test(text)) {
    return "ur-PK";
  }
  return "en-US";
}

export interface SpeakOptions {
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (error: unknown) => void;
}

/**
 * Immediately cancels ongoing browser speech playback.
 */
export function stopSpeech(): void {
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    try {
      window.speechSynthesis.cancel();
    } catch {
      // Ignore speech cancellation errors
    }
  }
}

/**
 * Speaks the provided voice summary using the browser's speechSynthesis API.
 * Speech errors fall back to text only and never throw or break chat.
 */
export function speakVoiceSummary(
  summary: string,
  lang = "en-US",
  options?: SpeakOptions,
): () => void {
  stopSpeech();

  const trimmed = summary.trim();
  if (!trimmed || !isSpeechSynthesisSupported()) {
    options?.onEnd?.();
    return () => {};
  }

  let settled = false;
  const finish = () => {
    if (!settled) {
      settled = true;
      options?.onEnd?.();
    }
  };

  try {
    const utterance = new window.SpeechSynthesisUtterance(trimmed);
    utterance.lang = lang || "en-US";

    utterance.onstart = () => {
      options?.onStart?.();
    };

    utterance.onend = () => {
      finish();
    };

    utterance.onerror = (event) => {
      // Speech errors fall back safely without breaking chat
      try {
        options?.onError?.(event);
      } finally {
        finish();
      }
    };

    window.speechSynthesis.speak(utterance);
  } catch (error) {
    options?.onError?.(error);
    finish();
  }

  return () => {
    stopSpeech();
    finish();
  };
}
