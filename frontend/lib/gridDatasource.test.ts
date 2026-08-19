import { describe, expect, it, vi } from "vitest";
import type { IGetRowsParams } from "ag-grid-community";
import { createGuardedDatasource } from "./gridDatasource";

function params(startRow = 0): IGetRowsParams {
  return {
    startRow,
    endRow: startRow + 100,
    successCallback: vi.fn(),
    failCallback: vi.fn(),
    sortModel: [],
    filterModel: {},
    context: undefined,
  } as unknown as IGetRowsParams;
}

const page = (n: number) => ({ items: Array.from({ length: n }, (_, i) => ({ id: i })), total: n });

describe("createGuardedDatasource", () => {
  it("serves the newest generation and reports the total", async () => {
    const loadIdRef = { current: 0 };
    const onLoaded = vi.fn();
    const ds = createGuardedDatasource({ loadIdRef, fetchPage: async () => page(3), onLoaded });

    const p = params();
    await ds.getRows(p);

    expect(p.successCallback).toHaveBeenCalledWith([{ id: 0 }, { id: 1 }, { id: 2 }], 3);
    expect(p.failCallback).not.toHaveBeenCalled();
    expect(onLoaded).toHaveBeenCalledOnce();
  });

  it("passes the requested startRow through to the fetcher", async () => {
    const loadIdRef = { current: 0 };
    const fetchPage = vi.fn(async () => page(1));
    await createGuardedDatasource({ loadIdRef, fetchPage }).getRows(params(400));
    expect(fetchPage).toHaveBeenCalledWith(400);
  });

  it("drops a superseded response instead of overwriting the newer total", async () => {
    const loadIdRef = { current: 0 };
    const onLoaded = vi.fn();
    const stale = createGuardedDatasource({ loadIdRef, fetchPage: async () => page(9999), onLoaded });
    createGuardedDatasource({ loadIdRef, fetchPage: async () => page(1) }); // newer generation

    const p = params();
    await stale.getRows(p);

    expect(p.successCallback).not.toHaveBeenCalled();
    expect(onLoaded).not.toHaveBeenCalled();
  });

  it("drops a superseded error without surfacing it to the user", async () => {
    const loadIdRef = { current: 0 };
    const onError = vi.fn();
    const stale = createGuardedDatasource({
      loadIdRef, fetchPage: async () => { throw new Error("boom"); }, onError,
    });
    createGuardedDatasource({ loadIdRef, fetchPage: async () => page(1) });

    await stale.getRows(params());
    expect(onError).not.toHaveBeenCalled();
  });

  it("surfaces an error from the newest generation", async () => {
    const loadIdRef = { current: 0 };
    const onError = vi.fn();
    const ds = createGuardedDatasource({
      loadIdRef, fetchPage: async () => { throw new Error("boom"); }, onError,
    });

    const p = params();
    await ds.getRows(p);

    expect(onError).toHaveBeenCalledOnce();
    expect(p.failCallback).toHaveBeenCalledOnce();
  });

  // The regression that made every sort click after the second one render blank
  // rows: AG Grid's RowNodeBlockLoader only frees a concurrency slot when the
  // block completes. A discard path that returns without calling either callback
  // leaks the slot, and the grid stops fetching entirely once the cap is hit.
  it("always completes the block — a superseded load never leaks a loader slot", async () => {
    const loadIdRef = { current: 0 };
    const calls: IGetRowsParams[] = [];

    for (const fetchPage of [
      async () => page(1),
      async () => { throw new Error("network"); },
    ]) {
      const ds = createGuardedDatasource({ loadIdRef, fetchPage });
      createGuardedDatasource({ loadIdRef, fetchPage: async () => page(1) }); // supersede it
      const p = params();
      calls.push(p);
      await ds.getRows(p);
    }

    for (const p of calls) {
      const completed =
        (p.successCallback as ReturnType<typeof vi.fn>).mock.calls.length +
        (p.failCallback as ReturnType<typeof vi.fn>).mock.calls.length;
      expect(completed).toBe(1);
    }
  });
});
