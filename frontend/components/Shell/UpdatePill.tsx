import { useEffect, useState } from "react";
import { ArrowUpCircle, Check, Copy, X } from "lucide-react";
import { updateApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { UpdateCheck } from "@/lib/types";

/**
 * "Update available" pill in the top bar.
 *
 * Updating already works — re-running the installer upgrades in place. What was
 * missing is that nothing ever told the user a newer version exists, so an admin
 * could sit on a months-old build indefinitely. This closes that loop and spells
 * out the three commands, because "run the install command again" assumes the
 * user still remembers a URL they pasted once.
 *
 * Deliberately NOT a one-click in-app upgrade: the running process would be
 * replacing the environment it is executing from, and on Windows the running
 * executable is locked. Telling the user what to type in the terminal they
 * already have open is the reliable version of the same thing.
 */

const DISMISS_KEY = "ts-admin-update-dismissed";

export default function UpdatePill() {
  const [info, setInfo] = useState<UpdateCheck | null>(null);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);

  useEffect(() => {
    setDismissed(localStorage.getItem(DISMISS_KEY));

    // A failed check resolves to null and renders nothing — an offline admin
    // gets no banner and no error, which is the correct amount of noise.
    updateApi.check().then(setInfo).catch(() => setInfo(null));
  }, []);

  if (!info?.update_available || !info.latest) return null;
  if (dismissed === info.latest) return null;

  const dismiss = () => {
    // Per-version, so the next release surfaces again rather than being
    // silenced forever by one dismissal.
    localStorage.setItem(DISMISS_KEY, info.latest!);
    setDismissed(info.latest);
    setOpen(false);
  };

  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={`Version ${info.latest} is available — you are on ${info.current}`}
        style={{
          display: "flex", alignItems: "center", gap: 5,
          padding: "4px 10px", borderRadius: 20, cursor: "pointer",
          background: theme.color.accentSoft,
          border: `1px solid ${theme.color.accentBorder}`,
          fontSize: 12, fontWeight: 500, color: theme.color.accent2,
          fontFamily: theme.font.sans, whiteSpace: "nowrap",
        }}
      >
        <ArrowUpCircle size={12} />
        Update available
      </button>

      {open && <UpdateInstructions info={info} onClose={() => setOpen(false)} onDismiss={dismiss} />}
    </div>
  );
}

function UpdateInstructions({ info, onClose, onDismiss }: {
  info: UpdateCheck;
  onClose: () => void;
  onDismiss: () => void;
}) {
  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 10 }} />

      <div style={{
        position: "absolute", right: 0, top: "calc(100% + 6px)",
        width: 340, zIndex: 20, overflow: "hidden",
        background: theme.color.surface, border: `1px solid ${theme.color.border}`,
        borderRadius: 8, boxShadow: theme.shadow.md, fontFamily: theme.font.sans,
      }}>
        <div style={{
          display: "flex", alignItems: "flex-start", justifyContent: "space-between",
          gap: 8, padding: "10px 12px", borderBottom: `1px solid ${theme.color.border}`,
        }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: theme.color.textPrimary }}>
              Update to v{info.latest}
            </div>
            <div style={{ fontSize: 11, color: theme.color.textMuted, marginTop: 2 }}>
              You are running v{info.current}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background: "none", border: "none", cursor: "pointer", color: theme.color.textMuted, padding: 2, lineHeight: 0 }}
          >
            <X size={13} />
          </button>
        </div>

        <div style={{ padding: "12px 12px 4px", display: "flex", flexDirection: "column", gap: 12 }}>
          <Step n={1} text="Stop the toolkit — press Ctrl+C in the terminal window running it." />
          <Step n={2} text="Then run:" command={info.command} />
          <Step n={3} text="Start it again:" command="ts-admin-toolkit serve" />
        </div>

        <div style={{
          padding: "10px 12px", marginTop: 8,
          borderTop: `1px solid ${theme.color.border}`,
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
        }}>
          <span style={{ fontSize: 11, color: theme.color.textMuted }}>
            Nothing to set up again — your instances, sign-ins and synced data all stay.
          </span>
          <button
            onClick={onDismiss}
            style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: 11, color: theme.color.textMuted, textDecoration: "underline", whiteSpace: "nowrap" }}
          >
            Not now
          </button>
        </div>

        <a
          href={info.release_url}
          target="_blank"
          rel="noreferrer"
          style={{
            display: "block", padding: "8px 12px",
            borderTop: `1px solid ${theme.color.border}`,
            fontSize: 11, color: theme.color.accent2, textDecoration: "none",
          }}
        >
          What&apos;s new in v{info.latest} →
        </a>
      </div>
    </>
  );
}

function Step({ n, text, command }: { n: number; text: string; command?: string }) {
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <span style={{
        flexShrink: 0, width: 16, height: 16, borderRadius: "50%",
        background: theme.color.surface3, color: theme.color.textSecondary,
        fontSize: 10, fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        {n}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: theme.color.textSecondary, lineHeight: 1.45 }}>{text}</div>
        {command && <CommandLine command={command} />}
      </div>
    </div>
  );
}

function CommandLine({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(command).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      },
      // Clipboard access can be refused by the browser; the command is
      // selectable text either way, so a failed copy needs no error state.
      () => undefined,
    );
  };

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 6, marginTop: 5,
      padding: "5px 6px 5px 8px", borderRadius: 5,
      background: theme.color.codeBg, border: `1px solid ${theme.color.border}`,
    }}>
      <code style={{
        flex: 1, minWidth: 0, fontFamily: theme.font.mono, fontSize: 11.5,
        color: theme.color.codeFg, overflowX: "auto", whiteSpace: "nowrap",
      }}>
        {command}
      </code>
      <button
        onClick={copy}
        aria-label={`Copy "${command}"`}
        title="Copy"
        style={{ background: "none", border: "none", cursor: "pointer", padding: 2, lineHeight: 0, color: copied ? theme.color.success : theme.color.textMuted }}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
    </div>
  );
}
