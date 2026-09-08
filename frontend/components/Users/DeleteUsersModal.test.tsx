/**
 * S31 — an owned-object count read from an uncertified metadata cache must
 * never be presented as fact.
 *
 * The fail-open this file exists to catch: the danger banner used to be gated
 * on `ownedTotal > 0`, so a truncated cache (which reports 0 owned objects for
 * everyone) rendered a *reassuring* modal — "0 owned objects", no warning,
 * Confirm enabled on `DELETE` alone — for a delete that can orphan content.
 * Every assertion below is therefore paired with a
 * `metadata_cache_authoritative: true` twin over the SAME all-zero counts:
 * without that twin these tests would pass on a component that always warns.
 *
 * Three states, three different renders, all pinned here:
 *   result says uncertified (false) → banner + acknowledgement
 *   result says certified   (true)  → silent
 *   NO result at all        (null)  → silent, error box only. `null` is not
 *                                     `false`; the modal must not accuse a
 *                                     cache it never read.
 * Plus the fail-safe on arrival: a result with the field MISSING reads as
 * uncertified, not as certified.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

import { DeleteUsersModal } from "./DeleteUsersModal";
import { usersApi, jobsApi } from "@/lib/api";
import type { UserListItem, DeleteDryRunResult } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  usersApi: { deleteDryrun: vi.fn(), deleteExecute: vi.fn() },
  jobsApi: { get: vi.fn() },
}));

const USERS = [
  { ts_guid: "u-alice", username: "alice", display_name: "Alice", email: "alice@co.com" },
] as unknown as UserListItem[];

/** The S31 shape: users resolve fine, every owned-object count is a bare 0. */
function result(): Omit<DeleteDryRunResult, "metadata_cache_authoritative"> {
  return {
    total: 1,
    items: [
      {
        ts_guid: "u-alice",
        username: "alice",
        display_name: "Alice",
        email: "alice@co.com",
        owned_object_count: 0,
        is_admin: false,
        exists_live: true,
      } as DeleteDryRunResult["items"][number],
    ],
    unrecognized: [],
    missing_live: [],
    admin_count: 0,
    owned_total: 0,
  };
}

/** Drive the 2s dry-run poll to completion against whatever `jobsApi.get` does. */
async function drivePoll() {
  vi.mocked(usersApi.deleteDryrun).mockResolvedValue({ job_id: "j1", total: 1 });
  render(<DeleteUsersModal clusterId="c1" orgId={0} users={USERS} onClose={() => {}} />);
  // Two hops: the dry-run POST resolves on the microtask queue and only THEN
  // installs the 2s poll interval, so a single advance would fire nothing.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(2000);
  });
}

type Job = Awaited<ReturnType<typeof jobsApi.get>>;

/** Render with a COMPLETE job whose result body is exactly `body`. */
async function renderWithResult(body: Record<string, unknown>) {
  vi.mocked(jobsApi.get).mockResolvedValue({ status: "COMPLETE", result: body } as unknown as Job);
  await drivePoll();
}

async function renderWith(metadataCacheAuthoritative: boolean) {
  await renderWithResult({ ...result(), metadata_cache_authoritative: metadataCacheAuthoritative });
}

/** Advance to the typed-DELETE step and type it. */
function armConfirm() {
  fireEvent.click(screen.getByRole("button", { name: /Delete 1 user/ }));
  fireEvent.change(screen.getByPlaceholderText("DELETE"), { target: { value: "DELETE" } });
}

const WARNING = /Owned-object counts may be incomplete/;
/** The acknowledgement checkbox, addressed by its accessible name so it stays
 *  unambiguous once an admin (a second checkbox) is in the fixture. */
const ACK = { name: /uncertified owned-object counts/ };
const ackCheckbox = () => screen.queryByRole("checkbox", ACK);
const confirmButton = () => screen.getByRole("button", { name: "Confirm delete" });

describe("DeleteUsersModal — uncertified cache", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("warns and asks for an acknowledgement even when every count is 0", async () => {
    await renderWith(false);

    expect(screen.getByText(WARNING)).toBeInTheDocument();
    expect(screen.getByText(/in cache — may be incomplete/)).toBeInTheDocument();

    armConfirm();
    expect(ackCheckbox()).toBeInTheDocument();
  });

  it("does not claim there has never been a completed sync", async () => {
    // FIX 2: `_write_sync_log` upserts one row per (cluster, org, entity) and
    // the write-ahead IN_PROGRESS marker overwrites SUCCESS, so this banner is
    // on screen for the whole duration of a NORMAL, HEALTHY re-sync. Copy that
    // asserts "no completed sync" contradicts the Topbar's last-sync timestamp
    // and trains operators to dismiss the warning.
    await renderWith(false);

    const banner = screen.getByText(WARNING);
    expect(banner).toHaveTextContent(/not certified complete/);
    expect(banner).toHaveTextContent(/never synced, or a sync is in progress or failed/);
    expect(banner.textContent).not.toMatch(/no completed sync/);
  });

  it("stays silent on a certified cache with the same all-zero counts", async () => {
    // Non-vacuity twin for the tests above: identical fixture, flag flipped.
    await renderWith(true);

    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();

    armConfirm();
    expect(ackCheckbox()).not.toBeInTheDocument();
    expect(confirmButton()).not.toBeDisabled();
  });

  it("keeps Confirm disabled until the acknowledgement is checked", async () => {
    await renderWith(false);
    armConfirm();

    expect(confirmButton()).toBeDisabled(); // typed DELETE is no longer sufficient

    fireEvent.click(ackCheckbox()!);
    expect(confirmButton()).not.toBeDisabled();
  });

  // ── FIX 7: the fail-safe on arrival ────────────────────────────────────────

  it("treats a result MISSING the field as uncertified, not as certified", async () => {
    // The field is omitted ENTIRELY — the shape of a job result produced by a
    // server that predates S31. Every other fixture in this file supplies an
    // explicit boolean, so this is the only test that can distinguish
    // `=== true` (correct) from `!== false` / `!!x` (fail-open: a missing
    // field would read as CERTIFIED). It must go red under either mutation.
    await renderWithResult(result() as unknown as Record<string, unknown>);

    expect(screen.getByText(WARNING)).toBeInTheDocument();

    armConfirm();
    expect(ackCheckbox()).toBeInTheDocument();
    expect(confirmButton()).toBeDisabled();
  });

  it("treats a NON-BOOLEAN field as uncertified, not as certified", async () => {
    // `!!x` survives the missing-field test above (`!!undefined === false`, the
    // correct reading), so it needs its own fixture. `job.result` is untyped
    // JSON off the wire — `"false"`, `1`, `"no"` are all shapes TypeScript
    // cannot forbid there, and every one of them is truthy. Under `!!x` this
    // renders as CERTIFIED. Only `=== true` refuses to guess.
    await renderWithResult({ ...result(), metadata_cache_authoritative: "false" });

    expect(screen.getByText(WARNING)).toBeInTheDocument();

    armConfirm();
    expect(ackCheckbox()).toBeInTheDocument();
    expect(confirmButton()).toBeDisabled();
  });

  // ── FIX 3: no result at all is NOT "uncertified" ───────────────────────────

  it("shows the error alone when the dry-run job FAILS — no cache banner", async () => {
    // `items` stays [] and the flag stays null. Gating the banner on
    // `!metadataCacheAuthoritative` would render a red stale-cache banner
    // beside the real error, accusing a cache the modal never read.
    vi.mocked(jobsApi.get).mockResolvedValue({
      status: "FAILED",
      error: "upstream exploded",
    } as unknown as Job);
    await drivePoll();

    expect(screen.getByText("upstream exploded")).toBeInTheDocument();
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });

  it("shows the error alone when the poll throws — no cache banner", async () => {
    vi.mocked(jobsApi.get).mockRejectedValue(new Error("boom"));
    await drivePoll();

    expect(screen.getByText("Lost connection while checking impact")).toBeInTheDocument();
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();
  });
});
