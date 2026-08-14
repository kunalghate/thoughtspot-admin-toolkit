import type { AppProps } from "next/app";
import Head from "next/head";
// Self-hosted fonts — bundled into the static export so the app renders
// identically offline / air-gapped (no Google Fonts CDN dependency).
import "@fontsource/inter/300.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/inter/800.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "@fontsource/jetbrains-mono/700.css";
import "@/styles/theme.css";
import { ThemeProvider } from "@/lib/theme";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ToastProvider } from "../components/Toast";

// Global styles, design tokens, fonts, and the AG Grid theme all live in
// styles/theme.css (imported above). Fonts + the no-flash theme script live in
// pages/_document.tsx. This keeps _app focused on providers.

export default function App({ Component, pageProps }: AppProps) {
  return (
    <>
      <Head>
        <title>ThoughtSpot Admin Toolkit</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>
      <ThemeProvider>
        <ErrorBoundary>
          <ToastProvider>
            <Component {...pageProps} />
          </ToastProvider>
        </ErrorBoundary>
      </ThemeProvider>
    </>
  );
}
