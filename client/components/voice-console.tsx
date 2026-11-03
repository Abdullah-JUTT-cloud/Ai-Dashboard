"use client";

import {
  useEffect,
  useRef,
  useState,
  useCallback,
  useSyncExternalStore,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  CheckCircle2,
  History,
  Maximize2,
  Mic,
  Plus,
  RefreshCw,
  Send,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useExecutionController } from "@/components/command-center/execution-context";
import { ChatMarkdown } from "@/components/chat-markdown";
import {
  deleteChatThread,
  fetchChatThread,
  fetchChatThreads,
  type ChatThreadSummary,
} from "@/services/api";
import {
  consumeVoiceTurn,
  detectLanguage,
  getDeterministicFallbackSummary,
  getSpeechRecognition,
  isSpeechRecognitionSupported,
  markVoiceTurn,
  speakVoiceSummary,
  stopSpeech,
  type SpeechRecognitionInstance,
} from "@/lib/voice";
import { useVoice } from "@/lib/voice-context";

export interface VoiceConsoleProps {
  initialOpen?: boolean;
  mode?: "sidebar" | "fullscreen";
  onClose?: () => void;
}

function formatWhen(value: string | null | undefined): string {
  if (!value) return "Just now";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recently";
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
  });
}

export function VoiceConsole({
  mode: propMode,
  onClose,
}: VoiceConsoleProps = {}) {
  const {
    messages,
    isSending,
    isLive,
    send,
    lastTurn,
    threadId,
    openThread,
    newChat,
    chatError,
    setWorkspaceOwnsActiveView,
  } = useExecutionController();

  const voiceCtx = useVoice();
  const [localFullScreen, setLocalFullScreen] = useState<boolean>(false);
  const isFullScreen =
    propMode === "fullscreen" ||
    (voiceCtx ? voiceCtx.isFullScreen || localFullScreen : localFullScreen);

  const browserSupported = useSyncExternalStore(
    () => () => {},
    isSpeechRecognitionSupported,
    () => true,
  );
  const [runtimeUnsupported, setRuntimeUnsupported] = useState<boolean>(false);
  const supported = browserSupported && !runtimeUnsupported;

  const [isListening, setIsListening] = useState<boolean>(false);
  const [isSpeaking, setIsSpeaking] = useState<boolean>(false);
  const [voiceError, setVoiceError] = useState<string>("");
  const [composerText, setComposerText] = useState<string>("");

  // Chat management state
  const [showChatsList, setShowChatsList] = useState<boolean>(false);
  const [chats, setChats] = useState<ChatThreadSummary[]>([]);
  const [isLoadingChats, setIsLoadingChats] = useState<boolean>(false);
  const [chatsError, setChatsError] = useState<string>("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const accumulatedFinalRef = useRef<string>("");
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelSpeechRef = useRef<(() => void) | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const pttActiveRef = useRef<boolean>(false);

  // Suppress execution overlay animations while voice view is mounted
  useEffect(() => {
    setWorkspaceOwnsActiveView(true);
    return () => {
      setWorkspaceOwnsActiveView(false);
    };
  }, [setWorkspaceOwnsActiveView]);

  // Load chats list when opened or refreshed
  const loadChats = useCallback(async () => {
    setIsLoadingChats(true);
    try {
      const list = await fetchChatThreads();
      setChats(list);
      setChatsError("");
    } catch {
      setChatsError("Failed to load conversations");
    } finally {
      setIsLoadingChats(false);
    }
  }, []);

  const handleToggleChatsList = useCallback(() => {
    setShowChatsList((prev) => {
      const next = !prev;
      if (next) {
        void loadChats();
      }
      return next;
    });
  }, [loadChats]);

  // New Chat action: starts fresh thread, clears text, reloads list if open
  const handleNewChat = useCallback(() => {
    newChat();
    setComposerText("");
    if (showChatsList) {
      void loadChats();
    }
  }, [newChat, showChatsList, loadChats]);

  // Switch active thread
  const handleOpenThread = useCallback(
    (id: string) => {
      openThread(id);
      setConfirmDeleteId(null);
    },
    [openThread],
  );

  // Delete thread with two-step confirm
  const handleDeleteThread = useCallback(
    async (id: string, e: React.MouseEvent) => {
      e.stopPropagation();
      if (confirmDeleteId !== id) {
        setConfirmDeleteId(id);
        return;
      }
      setConfirmDeleteId(null);
      try {
        await deleteChatThread(id);
        setChats((prev) => prev.filter((c) => c.thread_id !== id));
        if (threadId === id) {
          newChat();
        }
      } catch {
        setChatsError("Failed to delete chat");
      }
    },
    [confirmDeleteId, threadId, newChat],
  );

  // Refresh chats list
  const handleRefreshChats = useCallback(
    (e?: React.MouseEvent) => {
      e?.stopPropagation();
      setConfirmDeleteId(null);
      void loadChats();
    },
    [loadChats],
  );

  // Full-screen expand / close
  const handleExpand = useCallback(() => {
    if (voiceCtx?.openFullScreen) {
      voiceCtx.openFullScreen();
    }
    setLocalFullScreen(true);
  }, [voiceCtx]);

  const handleClose = useCallback(() => {
    if (onClose) onClose();
    if (voiceCtx?.closeFullScreen) {
      voiceCtx.closeFullScreen();
    }
    setLocalFullScreen(false);
  }, [onClose, voiceCtx]);

  // On every new turn (isSending), reset summary and cancel speech
  useEffect(() => {
    if (isSending) {
      if (cancelSpeechRef.current) {
        cancelSpeechRef.current();
        cancelSpeechRef.current = null;
      }
      stopSpeech();
      queueMicrotask(() => {
        setIsSpeaking(false);
      });
    }
  }, [isSending]);

  // Handle turn completion: bind summary strictly to current turn and auto-speak if voice-origin
  useEffect(() => {
    if (!lastTurn || lastTurn.phase !== "completed") return;
    const prompt = lastTurn.prompt;
    const taskId = lastTurn.taskId;

    // Check if this turn originated from voice. Typed turns MUST NOT auto-speak.
    const wasVoice = consumeVoiceTurn(prompt);

    let active = true;
    stopSpeech();
    queueMicrotask(() => {
      if (active) setIsSpeaking(false);
    });

    void (async () => {
      try {
        const thread = await fetchChatThread(threadId);
        if (!active) return;
        const turns = thread.turns || [];

        // Find the turn record by task_id first if available, else by prompt.
        const turnRecord =
          (taskId ? turns.find((t) => t.task_id === taskId) : null) ||
          [...turns].reverse().find((t) => t.question === prompt) ||
          null;

        let summary = turnRecord?.voice_summary || "";
        const answer = turnRecord?.answer || lastTurn.message || "";

        // If voice_summary is missing or empty, use deterministic fallback
        if (!summary || !summary.trim()) {
          summary = getDeterministicFallbackSummary(answer);
        }

        if (wasVoice && summary) {
          const lang = detectLanguage(answer || summary);
          const cancel = speakVoiceSummary(summary, lang, {
            onStart: () => {
              if (active) setIsSpeaking(true);
            },
            onEnd: () => {
              if (active) setIsSpeaking(false);
            },
            onError: () => {
              if (active) setIsSpeaking(false);
            },
          });
          cancelSpeechRef.current = cancel;
        }
      } catch (err) {
        console.warn("Could not fetch or speak voice summary", err);
        if (active) setIsSpeaking(false);
      }
    })();

    return () => {
      active = false;
    };
  }, [lastTurn, threadId]);

  // Auto-scroll on new messages or sending state change
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
    if (typeof messagesEndRef.current?.scrollIntoView === "function") {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isSending]);

  // Clean up recognition and speech on unmount
  useEffect(() => {
    return () => {
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      }
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch {
          // Ignore abort errors
        }
        recognitionRef.current = null;
      }
      if (cancelSpeechRef.current) {
        cancelSpeechRef.current();
        cancelSpeechRef.current = null;
      }
      stopSpeech();
    };
  }, []);

  const handleStopSpeech = useCallback(() => {
    if (cancelSpeechRef.current) {
      cancelSpeechRef.current();
      cancelSpeechRef.current = null;
    }
    stopSpeech();
    setIsSpeaking(false);
  }, []);

  // Submit the accumulated final transcript as typed text
  const submitListening = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }

    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch {
        // Ignore stop errors
      }
      recognitionRef.current = null;
    }
    setIsListening(false);

    // Never submit an interim/partial transcript
    const text = accumulatedFinalRef.current.trim();
    accumulatedFinalRef.current = "";
    setComposerText("");

    if (text) {
      stopSpeech();
      setIsSpeaking(false);
      markVoiceTurn(text);
      send(text);
      queueMicrotask(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
        if (typeof messagesEndRef.current?.scrollIntoView === "function") {
          messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
        }
      });
    }
  }, [send]);

  const startListening = useCallback(() => {
    setVoiceError("");

    // Stop any prior recognition
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch {
        // Ignore
      }
      recognitionRef.current = null;
    }

    const SpeechRec = getSpeechRecognition();
    if (!SpeechRec) {
      setRuntimeUnsupported(true);
      return;
    }

    try {
      const rec = new SpeechRec();
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = "en-US";
      accumulatedFinalRef.current = "";
      setComposerText("");

      rec.onstart = () => {
        setIsListening(true);
      };

      rec.onresult = (event) => {
        let finalChunk = "";
        let interimChunk = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const item = event.results[i];
          if (item.isFinal) {
            finalChunk += (finalChunk ? " " : "") + item[0].transcript.trim();
          } else {
            interimChunk += (interimChunk ? " " : "") + item[0].transcript.trim();
          }
        }
        if (finalChunk) {
          accumulatedFinalRef.current = (accumulatedFinalRef.current + " " + finalChunk).trim();
        }

        const currentLive = [accumulatedFinalRef.current, interimChunk].filter(Boolean).join(" ");
        // Requirement 2: live transcript fills the input box
        setComposerText(currentLive);

        // Reset the 3-second silence fallback timer on any detected speech
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }

        if (accumulatedFinalRef.current || interimChunk) {
          silenceTimerRef.current = setTimeout(() => {
            submitListening();
          }, 3000);
        }
      };

      rec.onerror = (event) => {
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }
        setIsListening(false);
        recognitionRef.current = null;
        if (event.error !== "no-speech" && event.error !== "aborted") {
          setVoiceError(`Voice input error: ${event.error || "unknown"}`);
        }
      };

      rec.onend = () => {
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }
        // If PTT is active, don't auto-submit on recognition end
        // PTT keyup handler will call submitListening
        if (pttActiveRef.current) {
          setIsListening(false);
          recognitionRef.current = null;
          return;
        }
        setIsListening(false);
        recognitionRef.current = null;

        const text = accumulatedFinalRef.current.trim();
        accumulatedFinalRef.current = "";
        setComposerText("");

        if (text) {
          stopSpeech();
          setIsSpeaking(false);
          markVoiceTurn(text);
          send(text);
          queueMicrotask(() => {
            if (scrollRef.current) {
              scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
            }
            if (typeof messagesEndRef.current?.scrollIntoView === "function") {
              messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
            }
          });
        }
      };

      recognitionRef.current = rec;
      rec.start();
    } catch (err) {
      setIsListening(false);
      recognitionRef.current = null;
      setVoiceError(err instanceof Error ? err.message : "Failed to start voice recognition");
    }
  }, [submitListening, send]);

  const toggleListening = useCallback(() => {
    if (isListening) {
      submitListening();
    } else {
      startListening();
    }
  }, [isListening, submitListening, startListening]);

  // Ctrl+M push-to-talk: hold to talk, release to submit
  useEffect(() => {
    function handleKeyDown(e: globalThis.KeyboardEvent) {
      if ((e.key === "m" || e.key === "M" || e.code === "KeyM") && e.ctrlKey) {
        e.preventDefault();
        e.stopPropagation();
        if (!e.repeat && !pttActiveRef.current && !isListening) {
          pttActiveRef.current = true;
          startListening();
        }
      }
    }

    function handleKeyUp(e: globalThis.KeyboardEvent) {
      if ((e.key === "m" || e.key === "M" || e.code === "KeyM") && pttActiveRef.current) {
        e.preventDefault();
        e.stopPropagation();
        pttActiveRef.current = false;
        submitListening();
      }
    }

    window.addEventListener("keydown", handleKeyDown, true);
    window.addEventListener("keyup", handleKeyUp, true);
    return () => {
      window.removeEventListener("keydown", handleKeyDown, true);
      window.removeEventListener("keyup", handleKeyUp, true);
    };
  }, [isListening, startListening, submitListening]);

  // Handle typed text submission through the input bar
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = composerText.trim();
    if (!text || isListening) return;
    setComposerText("");
    send(text);
    queueMicrotask(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
      if (typeof messagesEndRef.current?.scrollIntoView === "function") {
        messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
      }
    });
  }

  function handleComposerKey(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const text = composerText.trim();
      if (!text || isListening) return;
      setComposerText("");
      send(text);
      queueMicrotask(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
        if (typeof messagesEndRef.current?.scrollIntoView === "function") {
          messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
        }
      });
    }
  }

  // Conversation list UI block (shared between sidebar and full-screen)
  const renderChatsList = (isOverlay = false) => (
    <div
      className={`${
        isOverlay
          ? "w-80 shrink-0 border-r border-border bg-surface/95 backdrop-blur-md flex flex-col p-4 overflow-y-auto animate-in slide-in-from-left-4 duration-200"
          : "border-b border-border bg-surface-raised/95 p-3 max-h-64 overflow-y-auto space-y-2 shrink-0 animate-in slide-in-from-top-2 duration-150"
      }`}
      data-testid="voice-chats-list"
      aria-label="Conversation list"
    >
      <div className="flex items-center justify-between pb-2 border-b border-border/50 text-xs">
        <span className="font-semibold text-foreground">Conversations</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={handleRefreshChats}
            disabled={isLoadingChats}
            aria-label="Refresh chats list"
            data-testid="voice-refresh-chats"
            className="p-1 rounded text-muted hover:text-accent hover:bg-surface transition-colors"
            title="Refresh list"
          >
            <RefreshCw size={13} className={isLoadingChats ? "animate-spin" : ""} />
          </button>
          {!isOverlay && (
            <button
              type="button"
              onClick={() => setShowChatsList(false)}
              aria-label="Close chats list"
              className="p-1 rounded text-muted hover:text-foreground hover:bg-surface transition-colors"
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>

      {chatsError && (
        <p className="text-xs text-red-400 py-1">{chatsError}</p>
      )}

      {isLoadingChats && chats.length === 0 ? (
        <p className="text-xs text-muted py-4 text-center">Loading conversations...</p>
      ) : chats.length === 0 ? (
        <p className="text-xs text-muted py-4 text-center">No stored conversations</p>
      ) : (
        <div className="space-y-1.5 pt-1">
          {chats.map((c) => {
            const isActive = c.thread_id === threadId;
            const isConfirming = confirmDeleteId === c.thread_id;
            return (
              <div
                key={c.thread_id}
                data-testid="voice-thread-item"
                data-active={isActive ? "true" : "false"}
                className={`flex items-center justify-between gap-2 p-2 rounded-lg text-xs transition-colors border ${
                  isActive
                    ? "bg-accent/10 border-accent/40 text-foreground ring-1 ring-accent/20"
                    : "bg-surface/60 border-border/60 hover:bg-surface hover:border-border text-foreground/90"
                }`}
              >
                <div
                  className="min-w-0 flex-1 cursor-pointer"
                  onClick={() => handleOpenThread(c.thread_id)}
                >
                  <div className="flex items-center gap-1.5">
                    <p className="truncate font-medium">{c.title || "New conversation"}</p>
                    {isActive && (
                      <span
                        data-testid="voice-active-thread-badge"
                        className="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-accent/20 text-accent border border-accent/30"
                      >
                        Open now
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted truncate mt-0.5">
                    {c.turn_count} {c.turn_count === 1 ? "turn" : "turns"} · {formatWhen(c.updated_at)}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {!isActive && (
                    <button
                      type="button"
                      onClick={() => handleOpenThread(c.thread_id)}
                      aria-label="Open chat"
                      data-testid="voice-open-thread-button"
                      className="px-2 py-1 rounded bg-surface hover:bg-surface-overlay text-muted hover:text-foreground text-[11px] transition-colors"
                    >
                      Open
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={(e) => handleDeleteThread(c.thread_id, e)}
                    aria-label={isConfirming ? "Confirm delete chat" : "Delete chat"}
                    data-testid="voice-delete-thread-button"
                    className={`p-1 rounded transition-colors ${
                      isConfirming
                        ? "bg-destructive/20 text-destructive border border-destructive/40 text-[10px] px-1.5"
                        : "text-muted hover:text-destructive hover:bg-destructive/10"
                    }`}
                  >
                    {isConfirming ? "Confirm?" : <Trash2 size={13} />}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  // Message list items (shared between sidebar and full-screen)
  const renderMessageList = () => (
    <>
      {messages.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="grid h-12 w-12 place-items-center rounded-full bg-surface-raised text-muted border border-border">
            <Mic size={22} aria-hidden="true" />
          </div>
          <p className="mt-3 text-sm font-medium text-foreground">
            Voice chat
          </p>
          <p className="mt-1 max-w-sm text-xs text-muted">
            Type a message, tap the mic, or hold Ctrl+M to speak.
          </p>
        </div>
      )}

      {messages.map((entry) => {
        if (entry.role === "user") {
          return (
            <div key={entry.id} className="chat-row chat-row--user">
              <div className="chat-bubble chat-bubble--user" data-testid="voice-user-bubble">
                {entry.content}
              </div>
            </div>
          );
        }
        return (
          <div key={entry.id} className="chat-row chat-row--assistant">
            <div className="chat-body">
              {entry.content ? (
                <div className="chat-bubble chat-bubble--assistant" data-testid="voice-assistant-bubble">
                  <ChatMarkdown text={entry.content} />
                  {isLive && entry.id === messages[messages.length - 1]?.id && (
                    <span className="chat-streaming" aria-hidden="true" />
                  )}
                </div>
              ) : (
                isSending && (
                  <div className="chat-bubble chat-bubble--assistant" role="status">
                    <span className="chat-typing" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    <span className="sr-only">Processing your request</span>
                  </div>
                )
              )}
            </div>
          </div>
        );
      })}
      <div ref={messagesEndRef} aria-hidden="true" />
    </>
  );

  // Input bar (shared between sidebar and full-screen)
  const renderInputBar = () => (
    <div className="shrink-0 border-t border-border px-3 py-2 bg-background/50">
      {voiceError && (
        <p className="mb-2 text-xs text-red-400" role="alert">
          {voiceError}
        </p>
      )}
      {chatError && (
        <p className="mb-2 text-xs text-red-400" role="alert">
          {chatError}
        </p>
      )}

      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 rounded-xl border border-border bg-surface-raised px-2.5 py-1.5 transition-all focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15"
      >
        <textarea
          value={composerText}
          onChange={(event) => {
            if (!isListening) setComposerText(event.target.value);
          }}
          onKeyDown={handleComposerKey}
          placeholder={
            isListening
              ? "Listening..."
              : "Ask about the business..."
          }
          readOnly={isListening}
          aria-label="Voice chat input"
          data-testid="voice-input"
          rows={1}
          className="chat-composer-input min-w-0 flex-1 bg-transparent px-1 text-[0.95rem] text-foreground outline-none placeholder:text-muted"
        />

        {/* Mic button — on the side of the input */}
        <button
          type="button"
          onClick={toggleListening}
          disabled={!supported || (isSending && !isListening)}
          aria-label={
            isListening
              ? "Stop listening and send"
              : "Start voice input"
          }
          data-testid="voice-mic-button"
          className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg transition-all active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 ${
            isListening
              ? "bg-red-500 text-white"
              : "bg-surface text-muted hover:text-accent hover:bg-surface-overlay"
          }`}
        >
          <Mic size={16} aria-hidden="true" />
        </button>

        {/* Stop button — exactly ONE, shown only while speaking */}
        {isSpeaking && (
          <button
            type="button"
            onClick={handleStopSpeech}
            aria-label="Stop speech"
            data-testid="voice-stop-button"
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-negative-border bg-negative-muted text-negative transition-colors hover:bg-negative/20"
          >
            <Square size={14} aria-hidden="true" />
          </button>
        )}

        {/* Done button — while listening, user can tap to submit */}
        {isListening && (
          <button
            type="button"
            onClick={submitListening}
            aria-label="Done speaking"
            data-testid="voice-done-button"
            className="inline-flex items-center gap-1 rounded-lg border border-accent/40 bg-accent/20 px-2.5 py-1 text-xs font-semibold text-accent hover:bg-accent/30 transition-colors"
          >
            <CheckCircle2 size={13} aria-hidden="true" />
            Done
          </button>
        )}

        {/* Send button — for typed text */}
        {!isListening && (
          <button
            type="submit"
            aria-label="Send message"
            disabled={composerText.trim().length === 0}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground shadow-sm transition-all hover:bg-accent-hover active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Send size={15} aria-hidden="true" />
          </button>
        )}
      </form>
    </div>
  );

  // ───── FULL SCREEN VIEW ─────
  if (isFullScreen) {
    return (
      <section
        aria-label="Voice chat full screen"
        data-testid="voice-fullscreen-view"
        className="fixed inset-0 z-50 flex flex-col bg-background/98 backdrop-blur-md transition-all duration-300 ease-out animate-in fade-in-0 zoom-in-95"
      >
        {/* Full-screen Header */}
        <header className="flex items-center justify-between border-b border-border px-6 py-3.5 bg-surface/80 backdrop-blur-md shrink-0">
          <div className="flex items-center gap-3">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent">
              <Mic size={18} aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-foreground">Biztek Voice Assistant</h2>
              <p className="text-xs text-muted">Full-screen conversation</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleNewChat}
              aria-label="New chat"
              data-testid="voice-new-chat-button"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-surface hover:bg-surface-raised text-foreground transition-colors"
            >
              <Plus size={14} />
              New Chat
            </button>
            <button
              type="button"
              onClick={handleToggleChatsList}
              aria-label="Toggle chats list"
              data-testid="voice-chats-toggle"
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                showChatsList
                  ? "border-accent/40 bg-accent/15 text-accent"
                  : "border-border bg-surface hover:bg-surface-raised text-foreground"
              }`}
            >
              <History size={14} />
              Chats
            </button>
            <button
              type="button"
              onClick={handleClose}
              aria-label="Close full screen"
              data-testid="voice-close-fullscreen-button"
              className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:text-foreground hover:bg-surface-raised transition-colors"
            >
              <X size={18} />
            </button>
          </div>
        </header>

        {/* Content area: chats drawer + full-width messages */}
        <div className="flex flex-1 min-h-0 relative">
          {showChatsList && renderChatsList(true)}

          <div className="flex flex-col flex-1 min-w-0">
            <div
              ref={scrollRef}
              className="w-full max-w-5xl mx-auto flex-1 overflow-y-auto px-6 py-6"
              aria-live="polite"
              aria-label="Voice conversation messages"
              data-testid="voice-message-list"
            >
              {renderMessageList()}
            </div>

            <div className="w-full max-w-5xl mx-auto px-6 pb-6 shrink-0">
              {renderInputBar()}
            </div>
          </div>
        </div>
      </section>
    );
  }

  // ───── SIDEBAR VIEW ─────
  return (
    <section
      aria-label="Voice chat"
      data-testid="voice-chat-view"
      className="flex min-h-full flex-col"
    >
      {/* Sidebar Header Bar */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2 bg-surface/50 shrink-0">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-accent animate-pulse" aria-hidden="true" />
          <span className="text-xs font-semibold text-foreground">Voice Chat</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={handleNewChat}
            aria-label="New chat"
            data-testid="voice-new-chat-button"
            title="Start new conversation"
            className="p-1.5 rounded-lg text-muted hover:text-accent hover:bg-surface transition-colors"
          >
            <Plus size={15} />
          </button>
          <button
            type="button"
            onClick={handleToggleChatsList}
            aria-label="Toggle chats list"
            data-testid="voice-chats-toggle"
            title="Saved conversations"
            className={`p-1.5 rounded-lg transition-colors ${
              showChatsList ? "bg-accent/15 text-accent" : "text-muted hover:text-foreground hover:bg-surface"
            }`}
          >
            <History size={15} />
          </button>
          <button
            type="button"
            onClick={handleExpand}
            aria-label="Expand to full screen"
            data-testid="voice-expand-button"
            title="Full screen view"
            className="p-1.5 rounded-lg text-muted hover:text-accent hover:bg-surface transition-colors"
          >
            <Maximize2 size={15} />
          </button>
        </div>
      </div>

      {/* Conversation list drawer (when toggled open) */}
      {showChatsList && renderChatsList(false)}

      {/* Message list */}
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-3"
        aria-live="polite"
        aria-label="Voice conversation messages"
        data-testid="voice-message-list"
      >
        {renderMessageList()}
      </div>

      {/* Input bar */}
      {renderInputBar()}
    </section>
  );
}
