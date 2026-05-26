/**
 * Archiver dry-run happy path — the most safety-critical UI flow.
 *
 * The DryRunModal is the only UI gate between an admin's click and a
 * destructive call to a customer's ThoughtSpot cluster. This spec covers
 * the state machine in frontend/components/Deleter/DryRunModal.tsx:
 *
 *   polling → ready → running → complete
 *
 * Backend is fully stubbed via page.route — no FastAPI required.
 */

import { test, expect, type Route, type Page } from "@playwright/test";

// ── In-memory job store so dryrun→ready→execute→complete transitions work ──────

type StubJob = {
  id: string;
  job_type: string;
  status: "QUEUED" | "RUNNING" | "COMPLETE" | "FAILED" | "PARTIAL";
  progress: number;
  total: number;
  result?: Record<string, unknown>;
  error?: string;
};

const installStubs = async (page: Page) => {
  const jobs = new Map<string, StubJob>();

  const json = (route: Route, status: number, body: unknown) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  // ── Catch-all for any unmocked /api/v1 call so the spec doesn't hit a real backend ──
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );

  // ── Cluster + org bootstrap (Shell calls these on mount) ─────────────────────
  await page.route("**/api/v1/clusters", (route) =>
    json(route, 200, [
      { id: "c1", name: "Prod", url: "https://prod.example", username: "admin", auth_type: "basic", is_active: true },
    ])
  );
  await page.route("**/api/v1/clusters/c1/test", (route) =>
    json(route, 200, { success: true, ts_version: "10.0.0" })
  );
  await page.route("**/api/v1/clusters/c1/orgs/cached*", (route) =>
    json(route, 200, [{ org_id: 0, name: "Default", is_primary: true }])
  );
  await page.route("**/api/v1/clusters/c1/orgs**", (route) =>
    json(route, 200, [{ org_id: 0, name: "Default", is_primary: true }])
  );

  // ── Archiver reads ───────────────────────────────────────────────────────────
  await page.route("**/api/v1/archiver/preview*", (route) =>
    json(route, 200, { total: 1, by_type: { LIVEBOARD: 1 }, criteria_summary: "stale 90d" })
  );

  await page.route("**/api/v1/archiver/results*", (route) =>
    json(route, 200, {
      items: [
        {
          ts_guid: "lb-stale-1",
          name: "Stale Sales Dashboard",
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
        },
      ],
      total: 1,
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

  // ── Dryrun: returns COMPLETE on first poll ───────────────────────────────────
  await page.route("**/api/v1/archiver/dryrun", (route) => {
    const id = "job-dryrun";
    jobs.set(id, {
      id,
      job_type: "archive_dryrun",
      status: "COMPLETE",
      progress: 1,
      total: 1,
      result: {
        total: 1,
        by_type: { LIVEBOARD: 1 },
        shared_count: 0,
        affected_principals: [],
        dependency_warnings: [],
        errors: [],
      },
    });
    return json(route, 202, { job_id: id, total: 1 });
  });

  // ── Dryrun objects (modal grid) ──────────────────────────────────────────────
  await page.route("**/api/v1/archiver/dryrun/*/objects*", (route) =>
    json(route, 200, {
      items: [
        {
          ts_guid: "lb-stale-1",
          name: "Stale Sales Dashboard",
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
        },
      ],
      total: 1,
      record_offset: 0,
      page_size: 100,
    })
  );

  // ── Execute: returns COMPLETE on first poll ──────────────────────────────────
  await page.route("**/api/v1/archiver/execute", (route) => {
    const id = "job-execute";
    jobs.set(id, {
      id,
      job_type: "archive",
      status: "COMPLETE",
      progress: 1,
      total: 1,
      result: { succeeded: 1, failed_tml_export: 0, failed_delete: 0 },
    });
    return json(route, 202, { job_id: id, action: "delete", total: 1 });
  });

  // ── Job poller: serves whatever's in the in-memory store ─────────────────────
  await page.route("**/api/v1/jobs/*", (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/").pop() ?? "";
    const job = jobs.get(id);
    if (!job) return json(route, 404, { detail: "not found" });
    return json(route, 200, job);
  });
};

// ── Specs ──────────────────────────────────────────────────────────────────────

test("archiver page loads with dry-run-capable data", async ({ page }) => {
  await installStubs(page);
  await page.goto("/archiver");
  await expect(page.locator("body")).toContainText(/archiver/i, { timeout: 15_000 });
});

test("dry-run modal: polling → ready → confirm → execute → complete", async ({ page }) => {
  await installStubs(page);
  await page.goto("/archiver");

  // Wait for the seeded row to render in AG Grid.
  await expect(page.locator(".ag-row", { hasText: "Stale Sales Dashboard" })).toBeVisible({
    timeout: 15_000,
  });

  // AG Grid renders the pinned-left checkbox in a separate DOM subtree from the
  // row's data cells, so target it by ARIA role + label instead.
  await page.getByRole("checkbox", { name: /toggle row selection/i }).first().click();

  // Selection bar appears with the Delete-selected button.
  const openBtn = page.getByTestId("open-dryrun-modal");
  await expect(openBtn).toBeVisible();
  await openBtn.click();

  // Modal opens; impact-check polling resolves to "ready".
  const modal = page.getByTestId("dryrun-modal");
  await expect(modal).toBeVisible();
  await expect(modal).toHaveAttribute("data-state", "ready", { timeout: 10_000 });

  // Execute is disabled until the user types DELETE.
  const executeBtn = page.getByTestId("dryrun-execute");
  await expect(executeBtn).toBeDisabled();

  await page.getByTestId("dryrun-confirm-input").fill("DELETE");
  await expect(executeBtn).toBeEnabled();

  await executeBtn.click();

  // Job runs and finishes — state should land on "complete".
  await expect(modal).toHaveAttribute("data-state", "complete", { timeout: 10_000 });
});
