// Ambient module declarations for non-code imports.
//
// Next.js only ships type declarations for `*.module.css`/`*.module.scss`.
// Plain global stylesheet imports (e.g. AG Grid's prebuilt CSS) have no types,
// so some TypeScript servers report ts(2307) on them. Declaring the wildcard
// modules here makes bare `.css`/`.scss` imports valid under every TS version.
declare module "*.css";
declare module "*.scss";
