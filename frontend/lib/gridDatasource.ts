import type { IDatasource, IGetRowsParams } from "ag-grid-community";

/** The shape every server-paginated list endpoint returns. */
export interface PagedResponse<T> {
  items: T[];
  total: number;
}

/**
 * Build an AG Grid infinite-row-model datasource that drops responses from a
 * superseded generation.
 *
 * Why the generation guard exists: rebuilding the datasource (cluster, org,
 * search, or sort change) does not cancel the request the *previous*
 * datasource already had in flight. If that older response lands last it
 * overwrites `total` with the count for the query the user has already moved
 * off — e.g. the org-less first fetch that fires before `activeOrg` resolves
 * replacing an org-scoped count with the cluster-wide one.
 *
 * Why the discard path still calls `failCallback()`: AG Grid's
 * `RowNodeBlockLoader` caps in-flight datasource requests
 * (`maxConcurrentDatasourceRequests`, default 2) and only releases a slot when
 * the block completes — via `successCallback` OR `failCallback`. Returning
 * without calling either leaks the slot permanently, so after two superseded
 * loads the loader is saturated and the grid never issues another request:
 * every later sort or filter leaves blank rows behind. The block being failed
 * here belongs to a cache that `setGridOption("datasource", …)` has already
 * destroyed, so nothing user-visible is failed.
 */
export function createGuardedDatasource<R extends PagedResponse<unknown>>({
  loadIdRef,
  fetchPage,
  onLoaded,
  onError,
}: {
  /** Shared generation counter; bumped here, read back when a response lands. */
  loadIdRef: { current: number };
  fetchPage: (startRow: number) => Promise<R>;
  /** Called only for the newest generation — safe place to set `total`. */
  onLoaded?: (res: R) => void;
  /** Called only for the newest generation. */
  onError?: (err: unknown) => void;
}): IDatasource {
  const loadId = ++loadIdRef.current;

  return {
    getRows: async (params: IGetRowsParams) => {
      try {
        const res = await fetchPage(params.startRow);
        if (loadId !== loadIdRef.current) {
          params.failCallback(); // superseded — release the loader slot
          return;
        }
        onLoaded?.(res);
        params.successCallback(res.items, res.total);
      } catch (err) {
        if (loadId !== loadIdRef.current) {
          params.failCallback(); // superseded — release the loader slot
          return;
        }
        onError?.(err);
        params.failCallback();
      }
    },
  };
}
