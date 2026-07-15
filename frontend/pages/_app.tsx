import type { AppProps } from "next/app";
import Head from "next/head";
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
