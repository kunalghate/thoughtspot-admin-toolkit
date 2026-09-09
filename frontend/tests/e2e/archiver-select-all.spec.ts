/**
 * Archiver "select all N matching" — the flow that turns a filter into an
 * explicit object list.
 *
 * AG Grid's infinite row model has no working header checkbox: the grid only
 * ever holds a few cached blocks, so selecting "all 2,000 stale objects"
 * cannot be answered client-side. The page instead asks the server to expand
 * the current filters (POST /archiver/resolve) and feeds the result into the
 * ordinary dry-run flow.
 *
 * What this spec pins:
 *   - the offer only appears when the filter matches more than is selected
 *   - unticking a loaded row excludes it, and the count reflects that
 *   - the resolved GUIDs are what reach /dryrun — the filter is never sent
 *     to a destructive endpoint
 *
 * Backend is fully stubbed via page.route — no FastAPI required.
 */

import { test, expect, type Route, type Page } from "@playwright/test";

const ROWS = [
  { guid: "lb-1", name: "Stale Board One" },
  { guid: "lb-2", name: "Stale Board Two" },
  { guid: "lb-3", name: "Stale Board Three" },
];

/** Everything the filter matches — far more than the grid hands back. */
const TOTAL_MATCHING = 2000;

type Captured = { resolveBody?: any; dryrunBody?: any; executeBody?: any };

const installStubs = async (page: Page, captured: Captured) => {
  const json = (route: Route, status: number, body: unknown) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );

  await page.route("**/api/v1/clusters", (route) =>
    json(route, 200, [
      { id: "c1", name: "Prod", url: "https://prod.example", username: "admin", auth_type: "basic", is_active: true },
    ])
  );
  await page.route("**/api/v1/clusters/c1/test", (route) => json(route, 200, { success: true, ts_version: "10.0.0" }));
  await page.route("**/api/v1/clusters/c1/orgs*", (route) =>
    json(route, 200, [{ org_id: 0, name: "Default", is_primary: true }])
  );

  await page.route("**/api/v1/archiver/preview*", (route) =>
    json(route, 200, { total: TOTAL_MATCHING, by_type: { LIVEBOARD: TOTAL_MATCHING }, criteria_summary: "stale 90d" })
  );

  // The grid returns only the first block, but reports the real total — the
  // exact gap "select all matching" exists to close.
  await page.route("**/api/v1/archiver/results*", (route) =>
    json(route, 200, {
      items: ROWS.map((r) => ({
        ts_guid: r.guid,
        name: r.name,
        object_type: "LIVEBOARD",
        owner_guid: "u1",
        owner_name: "Alice",
        org_id: 0,
        last_accessed_at: "2024-01-01T00:00:00Z",
        modified_at: "2024-01-01T00:00:00Z",
        created_at: "2023-01-01T00:00:00Z",
        view_count: 0,
        days_unused: 365,
        tags: [],
      })),
      total: TOTAL_MATCHING,
      record_offset: 0,
      page_size: 200,
    })
  );

  await page.route("**/api/v1/archiver/tags*", (route) => json(route, 200, []));
  await page.route("**/api/v1/archiver/history*", (route) =>
    json(route, 200, { items: [], total: 0, record_offset: 0, page_size: 20 })
  );
  await page.route("**/api/v1/archiver/records*", (route) =>
    json(route, 200, { items: [], total: 0, record_offset: 0, page_size: 200 })
  );

  await page.route("**/api/v1/archiver/resolve", (route) => {
    captured.resolveBody = JSON.parse(route.request().postData() ?? "{}");
    const excluded: string[] = captured.resolveBody.excluded_guids ?? [];
    // Stand in for the server expanding the filter: every matching GUID minus
    // whatever the admin unticked.
    const all = Array.from({ length: TOTAL_MATCHING }, (_, i) => `all-${i}`).concat(ROWS.map((r) => r.guid));
    const guids = all.filter((g) => !excluded.includes(g)).slice(0, TOTAL_MATCHING - excluded.length);
    return json(route, 200, { guids, total: guids.length });
  });

  await page.route("**/api/v1/archiver/execute", (route) => {
    captured.executeBody = JSON.parse(route.request().postData() ?? "{}");
    return json(route, 202, {
      job_id: "job-export",
      action: captured.executeBody.action,
      total: captured.executeBody.object_ids.length,
    });
  });

  await page.route("**/api/v1/archiver/dryrun", (route) => {
    captured.dryrunBody = JSON.parse(route.request().postData() ?? "{}");
    return json(route, 202, { job_id: "job-dryrun", total: captured.dryrunBody.object_ids.length });
  });

  await page.route("**/api/v1/archiver/dryrun/*/objects*", (route) =>
    json(route, 200, { items: [], total: 0, record_offset: 0, page_size: 100 })
  );

  await page.route("**/api/v1/jobs/*", (route) =>
    json(route, 200, {
      id: "job-dryrun",
      job_type: "archive_dryrun",
      status: "COMPLETE",
      progress: 1,
      total: 1,
      result: {
        total: TOTAL_MATCHING,
        by_type: { LIVEBOARD: TOTAL_MATCHING },
        shared_count: 0,
        affected_principals: [],
        dependency_warnings: [],
        errors: [],
      },
    })
  );
};

const firstRowCheckbox = (page: Page) =>
  page.getByRole("checkbox", { name: /toggle row selection/i });

/**
 * Take the whole filtered set. Both entry points — the header checkbox and the
 * selection bar's link — now go through the same confirmation, because the next
 * click after either can be "Delete selected" over thousands of objects.
 */
const selectAllMatching = async (page: Page, via: "header" | "link" = "link") => {
  await page.getByTestId(via === "header" ? "select-all-header" : "select-all-matching").click();
  await expect(page.getByTestId("confirm-select-all")).toBeVisible();
  await page.getByTestId("confirm-select-all-ok").click();
};

test("select-all offer appears only once something is checked", async ({ page }) => {
  const captured: Captured = {};
  await installStubs(page, captured);
  await page.goto("/archiver");

  await expect(page.locator(".ag-row", { hasText: "Stale Board One" })).toBeVisible({ timeout: 15_000 });

  // Nothing checked yet — no selection bar, so no offer.
  await expect(page.getByTestId("select-all-matching")).toHaveCount(0);

  await firstRowCheckbox(page).first().click();
  await expect(page.getByTestId("select-all-matching")).toBeVisible();
  await expect(page.getByTestId("select-all-matching")).toContainText("2,000");
});

test("select all → untick one → dry-run receives the resolved list, not a filter", async ({ page }) => {
  const captured: Captured = {};
  await installStubs(page, captured);
  await page.goto("/archiver");

  await expect(page.locator(".ag-row", { hasText: "Stale Board One" })).toBeVisible({ timeout: 15_000 });

  await firstRowCheckbox(page).first().click();
  await selectAllMatching(page);

  // The bar now claims the whole matching set.
  await expect(page.getByTestId("select-all-badge")).toBeVisible();
  await expect(page.locator("body")).toContainText("2,000 selected");

  // Untick a loaded row — it becomes an exclusion, and the count drops by one.
  await firstRowCheckbox(page).first().click();
  await expect(page.getByTestId("select-all-badge")).toContainText("1 excluded");
  await expect(page.locator("body")).toContainText("1,999 selected");

  await page.getByTestId("open-dryrun-modal").click();
  await expect(page.getByTestId("dryrun-modal")).toBeVisible();
  // The modal appearing is not the POST landing — poll for the request rather
  // than reading `captured` the instant it renders. On a cold dev server the
  // gap is wide enough to read as "the dry-run was never sent".
  await expect.poll(() => captured.dryrunBody).toBeTruthy();

  // The resolve call carried the exclusion...
  expect(captured.resolveBody.excluded_guids).toEqual(["lb-1"]);
  // ...and the destructive endpoint got an explicit GUID list, never a filter.
  expect(Array.isArray(captured.dryrunBody.object_ids)).toBe(true);
  expect(captured.dryrunBody.object_ids).toHaveLength(1999);
  expect(captured.dryrunBody.object_ids).not.toContain("lb-1");
  expect(captured.dryrunBody).not.toHaveProperty("stale_activity_days");
});

test("changing the filter drops a standing select-all", async ({ page }) => {
  const captured: Captured = {};
  await installStubs(page, captured);
  await page.goto("/archiver");

  await expect(page.locator(".ag-row", { hasText: "Stale Board One" })).toBeVisible({ timeout: 15_000 });
  await firstRowCheckbox(page).first().click();
  await selectAllMatching(page);
  await expect(page.getByTestId("select-all-badge")).toBeVisible();

  // "All matching" means something different now, so the standing selection
  // must not silently re-point at a different set of objects.
  await page.getByPlaceholder(/search by name/i).fill("board two");
  await expect(page.getByTestId("select-all-badge")).toHaveCount(0, { timeout: 10_000 });
});

/**
 * The same rule, for the OTHER half of the filter set.
 *
 * The toolbar criteria live in React state; AG Grid's column filters live in a
 * ref (so that re-rendering cannot close an open filter popup mid-typing). The
 * invalidation effect only ever watched the first, so a column filter change
 * left "ALL MATCHING" standing over a different set — measured on se-demo:
 * select all 2,081 → filter Owner down to nothing → clear the filter again, and
 * the badge still claimed 2,081 objects the admin had never reviewed, still
 * carrying an exclusion GUID from the previous set.
 */
test("changing a COLUMN filter drops a standing select-all", async ({ page }) => {
  const captured: Captured = {};
  await installStubs(page, captured);
  await page.goto("/archiver");

  await expect(page.locator(".ag-row", { hasText: "Stale Board One" })).toBeVisible({ timeout: 15_000 });
  await firstRowCheckbox(page).first().click();
  await selectAllMatching(page);
  await expect(page.getByTestId("select-all-badge")).toBeVisible();

  await page.locator(".ag-header-cell", { hasText: "Owner" }).locator(".ag-header-icon").first().click();
  await page.locator(".ag-filter input").first().fill("alice");
  await page.keyboard.press("Enter");

  await expect(page.getByTestId("select-all-badge")).toHaveCount(0, { timeout: 10_000 });
});

/**
 * The header checkbox is the control admins actually reach for. AG Grid's own
 * `headerCheckboxSelection` is inert under the infinite row model, so the header
 * used to render nothing at all and the only route to the whole set was to tick
 * a row first and then find a link inside the selection bar.
 */
test("the header checkbox selects everything matching, after confirming", async ({ page }) => {
  const captured: Captured = {};
  await installStubs(page, captured);
  await page.goto("/archiver");

  await expect(page.locator(".ag-row", { hasText: "Stale Board One" })).toBeVisible({ timeout: 15_000 });

  // No row ticked first — that was the old prerequisite.
  await page.getByTestId("select-all-header").click();
  await expect(page.getByTestId("confirm-select-all")).toBeVisible();
  await expect(page.getByTestId("confirm-select-all")).toContainText("2,000");

  // Backing out must arm nothing.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("select-all-badge")).toHaveCount(0);

  await page.getByTestId("select-all-header").click();
  await page.getByTestId("confirm-select-all-ok").click();
  await expect(page.getByTestId("select-all-badge")).toBeVisible();
  await expect(page.locator("body")).toContainText("2,000 selected");

  // Unticking it is the way back out.
  await page.getByTestId("select-all-header").click();
  await expect(page.getByTestId("select-all-badge")).toHaveCount(0);
});


/**
 * Regression guard for a semantic conflict between two individually-green
 * branches: "Export TML" (#41) and select-all (#43) both landed in this
 * selection bar. Export originally read `selectedGuids` directly, so in
 * select-all mode it would have backed up only the rows AG Grid happened to
 * have loaded while the bar claimed thousands — a silent under-export of the
 * exact objects an admin is about to trust a delete against.
 *
 * Every action in this bar must resolve through /archiver/resolve.
 */
test("select all → Export TML exports the whole filtered set, not the loaded rows", async ({ page }) => {
  const captured: Captured = {};
  await installStubs(page, captured);
  await page.goto("/archiver");

  await expect(page.locator(".ag-row", { hasText: "Stale Board One" })).toBeVisible({ timeout: 15_000 });

  await firstRowCheckbox(page).first().click();
  await selectAllMatching(page);

  await page.getByTestId("export-tml").click();
  await expect.poll(() => captured.executeBody?.action).toBe("export");

  expect(captured.resolveBody, "export must expand the filter server-side").toBeTruthy();
  expect(captured.executeBody.object_ids.length).toBe(TOTAL_MATCHING);
  expect(
    captured.executeBody.object_ids.length,
    "exporting only the loaded rows would silently under-back-up the selection"
  ).toBeGreaterThan(ROWS.length);
});
