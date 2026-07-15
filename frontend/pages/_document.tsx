import { Html, Head, Main, NextScript } from "next/document";

/**
 * Runs before first paint: reads the saved theme preference (same key as
 * ThemeProvider) and stamps `data-theme` on <html> so the correct theme is
 * applied with no flash of the wrong colors. Kept dependency-free and inlined.
 */
const NO_FLASH_THEME = `
(function () {
  try {
    var KEY = 'ts_admin_theme';
    var LIGHT = 'compendium-light', DARK = 'compendium-dark';
    var pref = localStorage.getItem(KEY);
    var id;
    if (!pref || pref === 'system') {
      id = window.matchMedia('(prefers-color-scheme: dark)').matches ? DARK : LIGHT;
    } else {
      id = pref;
    }
    document.documentElement.setAttribute('data-theme', id);
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'compendium-light');
  }
})();
`;

export default function Document() {
  return (
    <Html lang="en" data-theme="compendium-light">
      <Head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_THEME }} />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
