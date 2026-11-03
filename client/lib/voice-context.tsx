"use client";

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import { useExecutionController } from "@/components/command-center/execution-context";

export interface VoiceContextType {
  isFullScreen: boolean;
  openFullScreen: (options?: { newChat?: boolean }) => void;
  closeFullScreen: () => void;
  toggleFullScreen: () => void;
}

const VoiceContext = createContext<VoiceContextType | null>(null);

export function VoiceProvider({ children }: { children: ReactNode }) {
  const [isFullScreen, setIsFullScreen] = useState(false);
  const { newChat } = useExecutionController();

  const openFullScreen = useCallback(
    (options?: { newChat?: boolean }) => {
      if (options?.newChat) {
        newChat();
      }
      setIsFullScreen(true);
    },
    [newChat],
  );

  const closeFullScreen = useCallback(() => {
    setIsFullScreen(false);
  }, []);

  const toggleFullScreen = useCallback(() => {
    setIsFullScreen((prev) => !prev);
  }, []);

  return (
    <VoiceContext.Provider
      value={{
        isFullScreen,
        openFullScreen,
        closeFullScreen,
        toggleFullScreen,
      }}
    >
      {children}
    </VoiceContext.Provider>
  );
}

export function useVoice(): VoiceContextType {
  const ctx = useContext(VoiceContext);
  if (!ctx) {
    return {
      isFullScreen: false,
      openFullScreen: () => {},
      closeFullScreen: () => {},
      toggleFullScreen: () => {},
    };
  }
  return ctx;
}
