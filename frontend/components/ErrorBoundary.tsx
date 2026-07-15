/**
 * Top-level React error boundary.
 *
 * Without this, an uncaught render error anywhere in the tree unmounts the
 * whole app and the user is left staring at a blank page. This catches it and
 * shows a recoverable fallback instead, so one broken component doesn't take
 * the toolkit down.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surfaced in the console / support bundle; the UI stays usable.
    console.error("Unhandled UI error:", error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          background: "#F2EDE3",
        }}
      >
        <div
          style={{
            background: "#FAF8F4",
            border: "1px solid #E8E1D5",
            borderRadius: 10,
            padding: "32px 36px",
            maxWidth: 480,
            textAlign: "center",
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              margin: "0 auto 18px",
              background: "linear-gradient(135deg, #C0392B, #8E2A1E)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontWeight: 700,
            }}
          >
            !
          </div>
          <h1 style={{ fontSize: 18, color: "#1A1714", margin: "0 0 8px" }}>Something broke on this screen</h1>
          <p style={{ fontSize: 13, color: "#7A7068", margin: "0 0 20px", lineHeight: 1.6 }}>
            The rest of the toolkit is fine. Try again, and if it keeps happening, grab the support bundle from
            Settings → Diagnostics.
          </p>
          <pre
            style={{
              textAlign: "left",
              fontSize: 11,
              color: "#6B6056",
              background: "#F4EFE6",
              border: "1px solid #EBE3D5",
              borderRadius: 6,
              padding: "8px 10px",
              margin: "0 0 20px",
              overflowX: "auto",
              maxHeight: 120,
            }}
          >
            {error.message}
          </pre>
          <button
            onClick={this.reset}
            style={{
              padding: "8px 18px",
              background: "#8B5CF6",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}
