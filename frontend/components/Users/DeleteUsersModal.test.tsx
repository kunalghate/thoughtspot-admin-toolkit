/**
 * S31 — an owned-object count read from an uncertified metadata cache must
 * never be presented as fact.
 *
 * The fail-open this file exists to catch: the danger banner used to be gated
 * on `ownedTotal > 0`, so a truncated cache (which reports 0 owned objects for
 * everyone) rendered a *reassuring* modal — "0 owned objects", no warning,
 * Confirm enabled on `DELETE` alone — for a delete that can orphan content.
 * Every assertion below is therefore paired with a `cache_authoritative: true`
 * twin over the SAME all-zero counts: without that twin these tests would pass
 * on a component that simply always warns.
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
function result(cacheAuthoritative: boolean): DeleteDryRunResult {
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
    cache_authoritative: cacheAuthoritative,
  };
}

/** Render the modal and drive the 2s dry-run poll to completion. */
async function renderWith(cacheAuthoritative: boolean) {
  vi.mocked(usersApi.deleteDryrun).mockResolvedValue({ job_id: "j1", total: 1 });
  vi.mocked(jobsApi.get).mockResolvedValue({
    status: "COMPLETE",
    result: result(cacheAuthoritative),
  } as unknown as Awaited<ReturnType<typeof jobsApi.get>>);

  render(<DeleteUsersModal clusterId="c1" orgId={0} users={USERS} onClose={() => {}} />);
  // Two hops: the dry-run POST resolves on the microtask queue and only THEN
  // installs the 2s poll interval, so a single advance would fire nothing.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(2000);
  });
}

/** Advance to the typed-DELETE step and type it. */
function armConfirm() {
  fireEvent.click(screen.getByRole("button", { name: /Delete 1 user/ }));
  fireEvent.change(screen.getByPlaceholderText("DELETE"), { target: { value: "DELETE" } });
}

const WARNING = /Owned-object counts may be incomplete/;
const ACK = /not certified complete/;

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
    expect(screen.getByText(ACK)).toBeInTheDocument();
  });

  it("stays silent on a certified cache with the same all-zero counts", async () => {
    // Non-vacuity twin for the test above: identical fixture, flag flipped.
    await renderWith(true);

    expect(screen.queryByText(WARNING)).not.toBeInTheDocument();

    armConfirm();
    expect(screen.queryByText(ACK)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm delete" })).not.toBeDisabled();
  });

  it("keeps Confirm disabled until the acknowledgement is checked", async () => {
    await renderWith(false);
    armConfirm();

    const confirm = screen.getByRole("button", { name: "Confirm delete" });
    expect(confirm).toBeDisabled(); // typed DELETE is no longer sufficient

    fireEvent.click(screen.getByRole("checkbox"));
    expect(confirm).not.toBeDisabled();
  });
});
