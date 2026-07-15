import type { AppProps } from "next/app";
import Head from "next/head";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ToastProvider } from "../components/Toast";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <>
      <Head>
        <title>ThoughtSpot Admin Toolkit</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </Head>
      <style global jsx>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
          font-family: 'Geist', sans-serif;
          background: #F2EDE3;
          color: #1A1714;
          -webkit-font-smoothing: antialiased;
        }
        a { color: inherit; }
        button { font-family: inherit; }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        @keyframes popoverIn {
          from { opacity: 0; transform: translateY(-4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        /* Indeterminate sync bar — slides a segment when the total is unknown. */
        @keyframes syncSlide {
          from { transform: translateX(-100%); }
          to   { transform: translateX(250%); }
        }

        /* Hide number-input spinner arrows in popovers (cleaner look) */
        .stale-num::-webkit-outer-spin-button,
        .stale-num::-webkit-inner-spin-button {
          -webkit-appearance: none;
          margin: 0;
        }
        .stale-num { -moz-appearance: textfield; }

        /* ── AG Grid filter popup polish ─────────────────────────────── */
        .ag-theme-alpine .ag-popup .ag-menu,
        .ag-theme-alpine .ag-popup .ag-filter,
        .ag-theme-alpine .ag-popup .ag-tabs {
          font-family: 'Geist', sans-serif;
          width: 220px;
          border: 1px solid #E8E1D5;
          border-radius: 10px;
          box-shadow:
            0 1px 2px rgba(26, 23, 20, 0.04),
            0 12px 28px -8px rgba(26, 23, 20, 0.18);
          background: #FFFFFF;
          overflow: hidden;
          animation: popoverIn 140ms ease-out;
        }
        .ag-theme-alpine .ag-filter-wrapper,
        .ag-theme-alpine .ag-filter-body-wrapper {
          padding: 12px 12px 14px !important;
        }
        .ag-theme-alpine .ag-filter-body-wrapper > * + * {
          margin-top: 8px !important;
        }

        /* Inputs (operator dropdown + text/number/date input) */
        .ag-theme-alpine .ag-picker-field-wrapper,
        .ag-theme-alpine .ag-input-field-input,
        .ag-theme-alpine .ag-text-field input,
        .ag-theme-alpine .ag-number-field input,
        .ag-theme-alpine .ag-date-field input {
          box-sizing: border-box !important;
          width: 100% !important;
          height: 32px !important;
          padding: 0 10px !important;
          border: 1px solid #EBE3D5 !important;
          border-radius: 6px !important;
          font-size: 13px !important;
          font-family: 'Geist', sans-serif !important;
          color: #1A1714 !important;
          background: #FAF8F4 !important;
          box-shadow: none !important;
          transition: border-color 120ms ease, box-shadow 120ms ease, background 120ms ease;
        }
        .ag-theme-alpine .ag-input-field-input::placeholder,
        .ag-theme-alpine .ag-text-field input::placeholder,
        .ag-theme-alpine .ag-number-field input::placeholder {
          color: #A39B91 !important;
        }
        .ag-theme-alpine .ag-picker-field-wrapper:focus,
        .ag-theme-alpine .ag-picker-field-wrapper:focus-within,
        .ag-theme-alpine .ag-input-field-input:focus,
        .ag-theme-alpine .ag-text-field input:focus,
        .ag-theme-alpine .ag-number-field input:focus,
        .ag-theme-alpine .ag-date-field input:focus {
          background: #FFFFFF !important;
          border-color: #8B5CF6 !important;
          box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.18) !important;
          outline: none !important;
        }
        .ag-theme-alpine .ag-picker-field-icon {
          color: #8B7E73 !important;
        }

        /* Footer (Apply panel) — quiet section, no cream background */
        .ag-theme-alpine .ag-filter-apply-panel {
          display: flex !important;
          flex-direction: row !important;
          justify-content: flex-end !important;
          align-items: center !important;
          padding: 8px 12px !important;
          border-top: 1px solid #F0EAE0 !important;
          background: #FFFFFF !important;
          gap: 4px !important;
        }
        .ag-theme-alpine .ag-filter-apply-panel > * {
          flex: 0 0 auto !important;
          margin: 0 !important;
        }
        /* Reset — ghost / text-button style (secondary action) */
        .ag-theme-alpine .ag-filter-apply-panel-button,
        .ag-theme-alpine .ag-filter-apply-panel button,
        .ag-theme-alpine .ag-standard-button {
          display: inline-flex !important;
          align-items: center !important;
          justify-content: center !important;
          box-sizing: border-box !important;
          font-family: 'Geist', sans-serif !important;
          font-size: 12px !important;
          font-weight: 500 !important;
          height: 30px !important;
          padding: 0 12px !important;
          margin: 0 !important;
          line-height: 1 !important;
          border-radius: 6px !important;
          border: 1px solid transparent !important;
          background: transparent !important;
          color: #6B6056 !important;
          cursor: pointer !important;
          min-width: auto !important;
          letter-spacing: 0.1px !important;
          transition: background 120ms ease, color 120ms ease, transform 120ms ease;
        }
        .ag-theme-alpine .ag-filter-apply-panel-button:hover,
        .ag-theme-alpine .ag-filter-apply-panel button:hover,
        .ag-theme-alpine .ag-standard-button:hover {
          background: #F4EFE6 !important;
          color: #1A1714 !important;
        }
        /* Apply — primary purple button */
        .ag-theme-alpine .ag-filter-apply-panel [data-ref="applyFilterButton"],
        .ag-theme-alpine .ag-filter-apply-panel [ref="applyFilterButton"],
        .ag-theme-alpine .ag-filter-apply-panel button.ag-filter-apply-panel-button:last-of-type,
        .ag-theme-alpine .ag-filter-apply-panel button:last-of-type {
          padding: 0 14px !important;
          min-width: 64px !important;
          font-weight: 600 !important;
          background: #8B5CF6 !important;
          border-color: #8B5CF6 !important;
          color: #FFFFFF !important;
          box-shadow: 0 1px 2px rgba(139, 92, 246, 0.25) !important;
        }
        .ag-theme-alpine .ag-filter-apply-panel [data-ref="applyFilterButton"]:hover,
        .ag-theme-alpine .ag-filter-apply-panel [ref="applyFilterButton"]:hover,
        .ag-theme-alpine .ag-filter-apply-panel button.ag-filter-apply-panel-button:last-of-type:hover,
        .ag-theme-alpine .ag-filter-apply-panel button:last-of-type:hover {
          background: #7C3AED !important;
          border-color: #7C3AED !important;
          color: #FFFFFF !important;
        }
        .ag-theme-alpine .ag-filter-apply-panel [data-ref="applyFilterButton"]:active,
        .ag-theme-alpine .ag-filter-apply-panel button:last-of-type:active {
          transform: translateY(1px);
        }

        /* Header funnel icon — subtle until filter is active */
        .ag-theme-alpine .ag-header-cell-menu-button,
        .ag-theme-alpine .ag-header-icon {
          color: #B0A498;
          opacity: 0.6;
          transition: opacity 120ms ease, color 120ms ease, background 120ms ease;
          border-radius: 4px;
          padding: 2px;
        }
        .ag-theme-alpine .ag-header-cell-menu-button:hover,
        .ag-theme-alpine .ag-header-icon:hover {
          opacity: 1;
          color: #4A3F35;
        }
        .ag-theme-alpine .ag-header-cell-filtered .ag-header-icon {
          opacity: 1;
          color: #8B5CF6;
          background: #F3EFFE;
        }

        /* Operator dropdown menu (the popover that opens on "Contains" click) */
        .ag-theme-alpine .ag-list,
        .ag-theme-alpine .ag-virtual-list-viewport {
          border-radius: 8px;
        }
        .ag-theme-alpine .ag-list-item {
          font-family: 'Geist', sans-serif;
          font-size: 13px;
          padding: 7px 12px;
          color: #1A1714;
          transition: background 100ms ease;
        }
        .ag-theme-alpine .ag-list-item:hover {
          background: #F9F7F2;
        }
        .ag-theme-alpine .ag-list-item.ag-active-item {
          background: #F3EFFE;
          color: #6D28D9;
          font-weight: 500;
          box-shadow: inset 3px 0 0 #8B5CF6;
        }
      `}</style>
      <ErrorBoundary>
        <ToastProvider>
          <Component {...pageProps} />
        </ToastProvider>
      </ErrorBoundary>
    </>
  );
}
