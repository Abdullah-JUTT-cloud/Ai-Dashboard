"use client";

import { Mic } from "lucide-react";
import { useVoice } from "@/lib/voice-context";

export function FloatingVoiceMic() {
  const { isFullScreen, openFullScreen } = useVoice();

  if (isFullScreen) return null;

  return (
    <button
      type="button"
      onClick={() => openFullScreen({ newChat: true })}
      aria-label="Start voice chat"
      data-testid="voice-floating-mic"
      className="fixed bottom-6 right-6 z-40 grid h-14 w-14 place-items-center rounded-full bg-accent text-accent-foreground shadow-2xl transition-all duration-200 hover:scale-105 hover:bg-accent-hover hover:shadow-[0_0_24px_rgba(163,230,53,0.45)] active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Mic size={24} aria-hidden="true" />
      <span className="sr-only">Start new voice chat</span>
    </button>
  );
}
