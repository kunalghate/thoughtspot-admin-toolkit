import type { AppProps } from "next/app";
import Head from "next/head";

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
      `}</style>
      <Component {...pageProps} />
    </>
  );
}
