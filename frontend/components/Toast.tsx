/**
 * Minimal toast system — no external dependency (keeps the static-export build
 * simple). Wrap the app in <ToastProvider> once, then call useToast() anywhere:
 *
 *   const toast = useToast();
 *   toast.error("Sync failed", { hint: "Reconnect the cluster." });
 *   toast.success("Saved");
 *
 * Toasts auto-dismiss (errors linger longer than successes) and can be
 * dismissed manually. This is the app-wide channel for surfacing failures that
 * previously vanished into inline state or a buried /jobs row.
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

type ToastKind = "success" | "error" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  hint?: string;
  /** Optional action shown as a link/button on the toast. */
  action?: { label: string; onClick: () => void };
}

interface ToastApi {
  success: (message: string, opts?: { hint?: string; durationMs?: number }) => void;
  error: (message: string, opts?: { hint?: string; durationMs?: number; action?: Toast["action"] }) => void;
  info: (message: string, opts?: { hint?: string; durationMs?: number }) => void;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const COLORS: Record<ToastKind, { bg: string; border: string; fg: string; accent: string }> = {
  success: { bg: "#F0FAF3", border: "#BBE8C9", fg: "#1A1714", accent: "#1E9E5A" },
  error: { bg: "#FDF2F2", border: "#F3C6C6", fg: "#1A1714", accent: "#C0392B" },
  info: { bg: "#F4EFFE", border: "#D8C9F7", fg: "#1A1714", accent: "#7C3AED" },
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (kind: ToastKind, message: string, opts?: { hint?: string; durationMs?: number; action?: Toast["action"] }) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, kind, message, hint: opts?.hint, action: opts?.action }]);
      const duration = opts?.durationMs ?? (kind === "error" ? 8000 : 4000);
      if (duration > 0) {
        setTimeout(() => dismiss(id), duration);
      }
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      success: (m, o) => push("success", m, o),
      error: (m, o) => push("error", m, o),
      info: (m, o) => push("info", m, o),
      dismiss,
    }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        style={{
          position: "fixed",
          bottom: 20,
          right: 20,
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: 10,
          maxWidth: 380,
        }}
      >
        {toasts.map((t) => {
          const c = COLORS[t.kind];
          return (
            <div
              key={t.id}
              role="status"
              style={{
                background: c.bg,
                border: `1px solid ${c.border}`,
                borderLeft: `3px solid ${c.accent}`,
                borderRadius: 8,
                padding: "12px 14px",
                boxShadow: "0 8px 24px -8px rgba(26,23,20,0.22)",
                color: c.fg,
                fontSize: 13,
                lineHeight: 1.5,
                animation: "popoverIn 160ms ease-out",
              }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600 }}>{t.message}</div>
                  {t.hint && <div style={{ marginTop: 2, color: "#6B6056" }}>{t.hint}</div>}
                  {t.action && (
                    <button
                      onClick={() => {
                        t.action!.onClick();
                        dismiss(t.id);
                      }}
                      style={{
                        marginTop: 8,
                        background: c.accent,
                        color: "#fff",
                        border: "none",
                        borderRadius: 6,
                        padding: "5px 12px",
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      {t.action.label}
                    </button>
                  )}
                </div>
                <button
                  onClick={() => dismiss(t.id)}
                  aria-label="Dismiss"
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "#A39B91",
                    cursor: "pointer",
                    fontSize: 16,
                    lineHeight: 1,
                    padding: 0,
                  }}
                >
                  ×
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

/**
 * Access the toast API. Returns a no-op implementation if used outside a
 * provider so a missing wrapper never crashes a page.
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx) return ctx;
  const noop = () => undefined;
  return { success: noop, error: noop, info: noop, dismiss: noop };
}
