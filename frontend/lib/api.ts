/**
 * Typed API client — all calls to the FastAPI backend go through here.
 * Never call /api/* directly from page components.
 */

import type { Cluster, Org, SyncLog, EntityType, Job, MetadataObject, MetadataStats, MetadataListResponse, PaginatedResponse, OffsetPaginatedResponse, PermissionsResponse, ArchiverItem, ArchiverPreview, ArchiveRecord, ArchiveSessionSummary, ArchiveRecordFlatItem, DeleterItem, DeleterResolveResponse, RootSearchItem, UserListItem, UserDetail, UserAccessResponse, GroupListItem, GroupDetail, TransferPreviewResponse, TransferSharingPreviewResponse, DeletePreviewResponse, UserHistoryItem, PrincipalPickerItem, SharingPreviewResponse, SharingHistoryItem, SharePermissionMode, DashboardSummary, TopologyResponse, LineageGraphResponse, ConsumersResponse, RootKind, UpdateCheck } from "./types";

// In dev mode, Next.js static-export config disables rewrites so we
// hit FastAPI directly on :8000. In production the SPA is served by
// FastAPI itself so /api/v1 resolves to the same origin.
const BASE =
  typeof window !== "undefined" && window.location.port === "3000"
    ? "http://localhost:8000/api/v1"
    : "/api/v1";

// ── Generic fetch wrapper ─────────────────────────────────────────────────────

/**
 * A failed API call. Extends Error (so existing `e.message` reads keep working)
 * but also carries the HTTP status, the backend's `error_type`, and an
 * actionable `hint` — enough for the UI to react to specific failures
 * (e.g. flip the cluster badge on an auth/session-expired error).
 *
 * `status === 0` means the request never reached the server (network down,
 * server not running, CORS) — distinct from an HTTP error response.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly errorType?: string;
  readonly hint?: string;

  constructor(message: string, opts: { status: number; errorType?: string; hint?: string } = { status: 0 }) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status;
    this.errorType = opts.errorType;
    this.hint = opts.hint;
  }

  /** True when the failure is an expired/invalid ThoughtSpot session. */
  get isAuthExpired(): boolean {
    return this.status === 401 || this.errorType === "TSAuthenticationError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (e) {
    // fetch only rejects on network-level failures (server down, DNS, CORS).
    throw new ApiError(
      "Can't reach the toolkit server. Is it still running?",
      { status: 0, errorType: "NetworkError" },
    );
  }

  // Parse body safely — some endpoints return 204 No Content, and an error
  // page (e.g. a proxy 502) may not be JSON at all.
  const text = await res.text();
  let body: any = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!res.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((e: any) => `${e.loc?.slice(-1)[0] ?? "?"}: ${e.msg ?? JSON.stringify(e)}`).join(", ")
        : `HTTP ${res.status}: ${res.statusText}`;
    throw new ApiError(message, {
      status: res.status,
      errorType: body?.error_type,
      hint: body?.hint,
    });
  }

  return body as T;
}

// ── Health ────────────────────────────────────────────────────────────────────

export const healthApi = {
  check: () => request<{ status: string; version: string }>("/health"),
};

// ── Updates ───────────────────────────────────────────────────────────────────

export const updateApi = {
  check: () => request<UpdateCheck>("/update"),
};

// ── Clusters ──────────────────────────────────────────────────────────────────

export const clustersApi = {
  list: () => request<Cluster[]>("/clusters"),

  create: (data: {
    id: string;
    name: string;
    url: string;
    username: string;
    auth_type: string;
    credential: string;
  }) => request<Cluster>("/clusters", { method: "POST", body: JSON.stringify(data) }),

  update: (id: string, data: {
    name: string;
    url: string;
    username: string;
    auth_type: string;
    credential?: string;     // omit to keep existing keychain entry
  }) => request<Cluster>(`/clusters/${id}`, { method: "PUT", body: JSON.stringify(data) }),

  delete: (id: string) =>
    request<void>(`/clusters/${id}`, { method: "DELETE" }),

  testConnection: (id: string) =>
    request<{ success: boolean; ts_version?: string; error?: string }>(`/clusters/${id}/test`, { method: "POST" }),

  activate: (id: string) =>
    request<Cluster>(`/clusters/${id}/activate`, { method: "POST" }),

  listOrgs: (id: string) =>
    request<Org[]>(`/clusters/${id}/orgs`),

  listCachedOrgs: (id: string) =>
    request<Org[]>(`/clusters/${id}/orgs/cached`),
};

// ── Sync ──────────────────────────────────────────────────────────────────────

export const syncApi = {
  // cluster_id is sent explicitly: the shell can be displaying one cluster while
  // a different one is marked active in config, and a sync must run against the
  // cluster the user is looking at.
  status: (clusterId: string, orgId = 0) =>
    request<SyncLog[]>(`/sync?org_id=${orgId}&cluster_id=${encodeURIComponent(clusterId)}`),

  trigger: (clusterId: string, orgId: number, entityType: EntityType) =>
    request<{ entity_type: string; job_id: string }>(
      `/sync/${entityType}?org_id=${orgId}&cluster_id=${encodeURIComponent(clusterId)}`,
      { method: "POST" },
    ),
};

// ── Metadata ──────────────────────────────────────────────────────────────────

export const metadataApi = {
  list: (params: {
    cluster_id: string;
    org_id: number;
    types?: string[];
    owner_guid?: string;
    tag_names?: string[];
    search?: string;
    stale_days?: number;
    owner_name_search?: string;
    tag_search?: string;
    views_min?: number;
    views_max?: number;
    last_accessed_before?: string;
    last_accessed_after?: string;
    modified_before?: string;
    modified_after?: string;
    created_before?: string;
    created_after?: string;
    sort_field?: string;
    sort_order?: "asc" | "desc";
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    if (params.types)          params.types.forEach((t) => q.append("types", t));
    if (params.owner_guid)     q.set("owner_guid", params.owner_guid);
    if (params.tag_names)      params.tag_names.forEach((t) => q.append("tag_names", t));
    if (params.search)         q.set("search", params.search);
    if (params.stale_days)     q.set("stale_days", String(params.stale_days));
    if (params.owner_name_search) q.set("owner_name_search", params.owner_name_search);
    if (params.tag_search)     q.set("tag_search", params.tag_search);
    if (params.views_min != null) q.set("views_min", String(params.views_min));
    if (params.views_max != null) q.set("views_max", String(params.views_max));
    if (params.last_accessed_before) q.set("last_accessed_before", params.last_accessed_before);
    if (params.last_accessed_after)  q.set("last_accessed_after", params.last_accessed_after);
    if (params.modified_before)  q.set("modified_before", params.modified_before);
    if (params.modified_after)   q.set("modified_after", params.modified_after);
    if (params.created_before)   q.set("created_before", params.created_before);
    if (params.created_after)    q.set("created_after", params.created_after);
    if (params.sort_field)     q.set("sort_field", params.sort_field);
    if (params.sort_order)     q.set("sort_order", params.sort_order);
    if (params.record_offset)  q.set("record_offset", String(params.record_offset));
    if (params.page_size)      q.set("page_size", String(params.page_size));
    return request<MetadataListResponse>(`/metadata?${q}`);
  },

  stats: (clusterId: string, orgId: number) =>
    request<MetadataStats>(`/metadata/stats?cluster_id=${clusterId}&org_id=${orgId}`),

  get: (guid: string, clusterId: string, orgId: number) =>
    request<MetadataObject>(`/metadata/${guid}?cluster_id=${clusterId}&org_id=${orgId}`),

  permissions: (guid: string, clusterId: string, orgId: number) =>
    request<PermissionsResponse>(`/metadata/${guid}/permissions?cluster_id=${clusterId}&org_id=${orgId}`),
};

// ── Jobs ──────────────────────────────────────────────────────────────────────

export const jobsApi = {
  list: (params?: {
    cluster_id?: string;
    job_types?: string[];
    statuses?: string[];
    sort_field?: string;
    sort_order?: "asc" | "desc";
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    if (params?.cluster_id)         q.set("cluster_id", params.cluster_id);
    if (params?.job_types)          params.job_types.forEach((t) => q.append("job_types", t));
    if (params?.statuses)           params.statuses.forEach((s) => q.append("statuses", s));
    if (params?.sort_field)         q.set("sort_field", params.sort_field);
    if (params?.sort_order)         q.set("sort_order", params.sort_order);
    if (params?.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params?.page_size)          q.set("page_size", String(params.page_size));
    const qs = q.toString();
    return request<{ items: Job[]; total: number; record_offset: number; page_size: number }>(
      qs ? `/jobs?${qs}` : `/jobs`,
    );
  },

  get: (jobId: string) =>
    request<Job>(`/jobs/${jobId}`),

  cancel: (jobId: string) =>
    request<void>(`/jobs/${jobId}/cancel`, { method: "DELETE" }),
};

// ── Diagnostics ───────────────────────────────────────────────────────────────

export const dashboardApi = {
  summary: (clusterId: string, orgId = 0) =>
    request<DashboardSummary>(`/dashboard?cluster_id=${clusterId}&org_id=${orgId}`),
};

export const diagnosticsApi = {
  /** Return the last N lines of the application log as plain text. */
  tailLogs: async (lines: number = 500): Promise<string> => {
    const res = await fetch(`${BASE}/diagnostics/logs?lines=${lines}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    return res.text();
  },

  /**
   * Absolute URL to download the support bundle as a zip. Small by default
   * (log tail + last few failed jobs); `full` includes every rotated log.
   */
  bundleUrl: (jobId?: string, opts?: { full?: boolean }): string => {
    const params = new URLSearchParams();
    if (jobId) params.set("job_id", jobId);
    if (opts?.full) params.set("full", "true");
    const q = params.toString();
    return `${BASE}/diagnostics/bundle${q ? `?${q}` : ""}`;
  },
};

// ── Archiver ──────────────────────────────────────────────────────────────────

export const archiverApi = {
  preview: (params: {
    cluster_id: string;
    org_id: number;
    stale_activity_days: number;
    stale_modified_days: number;
    types?: string[];
    exclude_tags?: string[];
    stale_operator?: "AND" | "OR";
    owner_guid?: string;
    exclude_owner_guids?: string[];
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    q.set("stale_activity_days", String(params.stale_activity_days));
    q.set("stale_modified_days", String(params.stale_modified_days));
    if (params.types)               params.types.forEach((t) => q.append("types", t));
    if (params.exclude_tags)        params.exclude_tags.forEach((t) => q.append("exclude_tags", t));
    if (params.stale_operator)      q.set("stale_operator", params.stale_operator);
    if (params.owner_guid)          q.set("owner_guid", params.owner_guid);
    if (params.exclude_owner_guids) params.exclude_owner_guids.forEach((g) => q.append("exclude_owner_guids", g));
    return request<ArchiverPreview>(`/archiver/preview?${q}`);
  },

  results: (params: {
    cluster_id: string;
    org_id: number;
    stale_activity_days: number;
    stale_modified_days: number;
    types?: string[];
    exclude_tags?: string[];
    filter_tags?: string[];
    search?: string;
    stale_operator?: "AND" | "OR";
    owner_guid?: string;
    exclude_owner_guids?: string[];
    owner_name_search?: string;
    tag_search?: string;
    days_unused_min?: number;
    days_unused_max?: number;
    views_min?: number;
    views_max?: number;
    last_accessed_before?: string;
    last_accessed_after?: string;
    modified_before?: string;
    modified_after?: string;
    created_before?: string;
    created_after?: string;
    sort_field?: string;
    sort_order?: "asc" | "desc";
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    q.set("stale_activity_days", String(params.stale_activity_days));
    q.set("stale_modified_days", String(params.stale_modified_days));
    if (params.types)               params.types.forEach((t) => q.append("types", t));
    if (params.exclude_tags)        params.exclude_tags.forEach((t) => q.append("exclude_tags", t));
    if (params.filter_tags)         params.filter_tags.forEach((t) => q.append("filter_tags", t));
    if (params.search)              q.set("search", params.search);
    if (params.stale_operator)      q.set("stale_operator", params.stale_operator);
    if (params.owner_guid)          q.set("owner_guid", params.owner_guid);
    if (params.exclude_owner_guids) params.exclude_owner_guids.forEach((g) => q.append("exclude_owner_guids", g));
    if (params.owner_name_search)   q.set("owner_name_search", params.owner_name_search);
    if (params.tag_search)          q.set("tag_search", params.tag_search);
    if (params.days_unused_min != null) q.set("days_unused_min", String(params.days_unused_min));
    if (params.days_unused_max != null) q.set("days_unused_max", String(params.days_unused_max));
    if (params.views_min != null)   q.set("views_min", String(params.views_min));
    if (params.views_max != null)   q.set("views_max", String(params.views_max));
    if (params.last_accessed_before) q.set("last_accessed_before", params.last_accessed_before);
    if (params.last_accessed_after)  q.set("last_accessed_after", params.last_accessed_after);
    if (params.modified_before)     q.set("modified_before", params.modified_before);
    if (params.modified_after)      q.set("modified_after", params.modified_after);
    if (params.created_before)      q.set("created_before", params.created_before);
    if (params.created_after)       q.set("created_after", params.created_after);
    if (params.sort_field)          q.set("sort_field", params.sort_field);
    if (params.sort_order)          q.set("sort_order", params.sort_order);
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)           q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<ArchiverItem>>(`/archiver/results?${q}`);
  },

  tags: (params: {
    cluster_id: string;
    org_id: number;
    stale_activity_days?: number;
    stale_modified_days?: number;
    types?: string[];
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    if (params.stale_activity_days != null) q.set("stale_activity_days", String(params.stale_activity_days));
    if (params.stale_modified_days != null)  q.set("stale_modified_days", String(params.stale_modified_days));
    if (params.types) params.types.forEach((t) => q.append("types", t));
    return request<{ ts_guid: string; name: string }[]>(`/archiver/tags?${q}`);
  },

  dryrun: (body: { cluster_id: string; org_id: number; object_ids: string[] }) =>
    request<{ job_id: string; total: number }>("/archiver/dryrun", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  dryrunObjects: (job_id: string, params: {
    cluster_id: string;
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)             q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<ArchiverItem>>(`/archiver/dryrun/${job_id}/objects?${q}`);
  },

  execute: (body: {
    cluster_id: string;
    org_id: number;
    object_ids: string[];
    action: "tag" | "untag" | "delete";
    tag_name?: string;
    create_tag_if_missing?: boolean;
  }) =>
    request<{ job_id: string; action: string; total: number }>("/archiver/execute", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  history: (params: {
    cluster_id: string;
    org_id: number;
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)             q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<ArchiveSessionSummary>>(`/archiver/history?${q}`);
  },

  historySession: (job_id: string, params: {
    cluster_id: string;
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)             q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<ArchiveRecord>>(`/archiver/history/${job_id}?${q}`);
  },

  restore: (body: { cluster_id: string; org_id: number; archive_record_ids: string[] }) =>
    request<{ job_id: string; total: number }>("/archiver/restore", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  allRecords: (params: {
    cluster_id: string;
    org_id: number;
    sort_field?: string;
    sort_order?: "asc" | "desc";
    search?: string;
    types?: string[];
    owner_name_search?: string;
    archived_before?: string;
    archived_after?: string;
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    if (params.sort_field)            q.set("sort_field", params.sort_field);
    if (params.sort_order)            q.set("sort_order", params.sort_order);
    if (params.search)                q.set("search", params.search);
    if (params.types)                 params.types.forEach((t) => q.append("types", t));
    if (params.owner_name_search)     q.set("owner_name_search", params.owner_name_search);
    if (params.archived_before)       q.set("archived_before", params.archived_before);
    if (params.archived_after)        q.set("archived_after", params.archived_after);
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)             q.set("page_size", String(params.page_size));
    return request<{ items: ArchiveRecordFlatItem[]; total: number; record_offset: number; page_size: number }>(`/archiver/records?${q}`);
  },
};

// ── Bulk Deleter ──────────────────────────────────────────────────────────────

export const deleterApi = {
  resolveDownstream: (body: { cluster_id: string; org_id: number; root_guid: string; root_type: string }) =>
    request<DeleterResolveResponse>("/deleter/resolve/downstream", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  resolveTag: (body: { cluster_id: string; org_id: number; tag_name: string }) =>
    request<DeleterResolveResponse>("/deleter/resolve/tag", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteTagOnly: (body: { cluster_id: string; org_id: number; tag_name: string }) =>
    request<{ tag_id: string; tag_name: string; removed_from: number }>("/deleter/delete-tag-only", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  resolveList: (body: { cluster_id: string; org_id: number; guids: string[] }) =>
    request<DeleterResolveResponse>("/deleter/resolve/list", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  tags: (params: { cluster_id: string; org_id: number }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    return request<string[]>(`/deleter/tags?${q}`);
  },

  rootSearch: (params: {
    cluster_id: string;
    org_id: number;
    query: string;
    types?: string[];
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    q.set("query", params.query);
    if (params.types) params.types.forEach((t) => q.append("types", t));
    if (params.limit) q.set("limit", String(params.limit));
    return request<RootSearchItem[]>(`/deleter/roots/search?${q}`);
  },

  dryrun: (body: { cluster_id: string; org_id: number; object_ids: string[] }) =>
    request<{ job_id: string; total: number }>("/deleter/dryrun", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  dryrunObjects: (job_id: string, params: { cluster_id: string; record_offset?: number; page_size?: number }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)             q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<DeleterItem>>(`/deleter/dryrun/${job_id}/objects?${q}`);
  },

  execute: (body: { cluster_id: string; org_id: number; object_ids: string[] }) =>
    request<{ job_id: string; total: number }>("/deleter/execute", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// ── User Management ───────────────────────────────────────────────────────────

export const usersApi = {
  list: (params: {
    cluster_id: string;
    org_id?: number;
    status?: string;
    search?: string;
    sort_field?: string;
    sort_order?: "asc" | "desc";
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    if (params.org_id != null)         q.set("org_id", String(params.org_id));
    if (params.status)                  q.set("status", params.status);
    if (params.search)                  q.set("search", params.search);
    if (params.sort_field)              q.set("sort_field", params.sort_field);
    if (params.sort_order)              q.set("sort_order", params.sort_order);
    if (params.record_offset != null)  q.set("record_offset", String(params.record_offset));
    if (params.page_size)               q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<UserListItem>>(`/users?${q}`);
  },

  get: (ts_guid: string, cluster_id: string) =>
    request<UserDetail>(`/users/${ts_guid}?cluster_id=${cluster_id}`),

  access: (ts_guid: string, cluster_id: string, org_id: number) =>
    request<UserAccessResponse>(`/users/${ts_guid}/access?cluster_id=${cluster_id}&org_id=${org_id}`),

  transferPreview: (body: {
    cluster_id: string;
    org_id: number;
    from_user_guid: string;
    object_types?: string[];
    tag_names?: string[];
    explicit_guids?: string[];
  }) =>
    request<TransferPreviewResponse>("/users/transfer/preview", {
      method: "POST", body: JSON.stringify(body),
    }),

  transferExecute: (body: {
    cluster_id: string;
    org_id: number;
    from_user_guid: string;
    to_user_identifier: string;
    object_ids: string[];
  }) =>
    request<{ job_id: string; total: number }>("/users/transfer/execute", {
      method: "POST", body: JSON.stringify(body),
    }),

  transferSharingPreview: (body: {
    cluster_id: string;
    org_id: number;
    from_user_guid: string;
    to_user_identifier: string;
  }) =>
    request<TransferSharingPreviewResponse>("/users/transfer-sharing/preview", {
      method: "POST", body: JSON.stringify(body),
    }),

  transferSharingExecute: (body: {
    cluster_id: string;
    org_id: number;
    from_user_guid: string;
    to_user_identifier: string;
    notify?: boolean;
  }) =>
    request<{ job_id: string; total: number }>("/users/transfer-sharing/execute", {
      method: "POST", body: JSON.stringify(body),
    }),

  deletePreview: (body: { cluster_id: string; user_guids: string[] }) =>
    request<DeletePreviewResponse>("/users/delete/preview", {
      method: "POST", body: JSON.stringify(body),
    }),

  deleteDryrun: (body: {
    cluster_id: string;
    org_id: number;
    user_guids: string[];
    user_identifiers?: string[];
  }) =>
    request<{ job_id: string; total: number }>("/users/delete/dryrun", {
      method: "POST", body: JSON.stringify(body),
    }),

  deleteExecute: (body: {
    cluster_id: string;
    org_id: number;
    user_guids: string[];
    user_identifiers?: string[];
  }) =>
    request<{ job_id: string; total: number }>("/users/delete/execute", {
      method: "POST", body: JSON.stringify(body),
    }),

  history: (params: {
    cluster_id: string;
    org_id?: number;
    action_type?: string;
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    if (params.org_id != null)        q.set("org_id", String(params.org_id));
    if (params.action_type)            q.set("action_type", params.action_type);
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)              q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<UserHistoryItem>>(`/users/history?${q}`);
  },
};

// ── Group Management (read-only v1) ───────────────────────────────────────────

export const groupsApi = {
  list: (params: {
    cluster_id: string;
    org_id?: number;
    search?: string;
    sort_field?: string;
    sort_order?: "asc" | "desc";
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    if (params.org_id != null)         q.set("org_id", String(params.org_id));
    if (params.search)                  q.set("search", params.search);
    if (params.sort_field)              q.set("sort_field", params.sort_field);
    if (params.sort_order)              q.set("sort_order", params.sort_order);
    if (params.record_offset != null)  q.set("record_offset", String(params.record_offset));
    if (params.page_size)               q.set("page_size", String(params.page_size));
    return request<OffsetPaginatedResponse<GroupListItem>>(`/groups?${q}`);
  },

  get: (ts_guid: string, cluster_id: string, org_id?: number) =>
    request<GroupDetail>(
      `/groups/${ts_guid}?cluster_id=${cluster_id}` + (org_id != null ? `&org_id=${org_id}` : ""),
    ),
};

// ── Bulk Sharing ──────────────────────────────────────────────────────────────

export const sharingApi = {
  principals: (params: {
    cluster_id: string;
    org_id: number;
    search?: string;
    include_users?: boolean;
    include_groups?: boolean;
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    if (params.search)               q.set("search", params.search);
    if (params.include_users != null) q.set("include_users", String(params.include_users));
    if (params.include_groups != null) q.set("include_groups", String(params.include_groups));
    if (params.limit)                 q.set("limit", String(params.limit));
    return request<{ items: PrincipalPickerItem[]; total: number }>(`/sharing/principals?${q}`);
  },

  preview: (body: {
    cluster_id: string;
    org_id: number;
    object_guids?: string[];
    tag_name?: string;
    principal_guids: string[];
    mode: SharePermissionMode;
  }) =>
    request<SharingPreviewResponse>("/sharing/preview", {
      method: "POST", body: JSON.stringify(body),
    }),

  dryrun: (body: {
    cluster_id: string;
    org_id: number;
    object_guids?: string[];
    tag_name?: string;
    principal_guids: string[];
    mode: SharePermissionMode;
  }) =>
    request<{ job_id: string; total: number }>("/sharing/dryrun", {
      method: "POST", body: JSON.stringify(body),
    }),

  execute: (body: {
    cluster_id: string;
    org_id: number;
    object_guids?: string[];
    tag_name?: string;
    principal_guids: string[];
    mode: SharePermissionMode;
    notify?: boolean;
  }) =>
    request<{ job_id: string; total: number }>("/sharing/execute", {
      method: "POST", body: JSON.stringify(body),
    }),

  history: (params: {
    cluster_id: string;
    org_id?: number;
    record_offset?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    if (params.org_id != null)        q.set("org_id", String(params.org_id));
    if (params.record_offset != null) q.set("record_offset", String(params.record_offset));
    if (params.page_size)              q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<SharingHistoryItem>>(`/sharing/history?${q}`);
  },
};

// ── Relationships / Data Lineage ──────────────────────────────────────────────

export const relationshipsApi = {
  topology: (clusterId: string, orgId: number) =>
    request<TopologyResponse>(`/relationships/topology?cluster_id=${clusterId}&org_id=${orgId}`),

  graph: (rootKind: RootKind, guid: string, clusterId: string, orgId: number) =>
    request<LineageGraphResponse>(
      `/relationships/${rootKind}/${encodeURIComponent(guid)}?cluster_id=${clusterId}&org_id=${orgId}`,
    ),

  consumers: (rootKind: RootKind, guid: string, params: {
    cluster_id: string;
    org_id: number;
    type?: string;
    offset?: number;
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    if (params.type)            q.set("type", params.type);
    if (params.offset != null)  q.set("offset", String(params.offset));
    if (params.limit)           q.set("limit", String(params.limit));
    return request<ConsumersResponse>(`/relationships/${rootKind}/${encodeURIComponent(guid)}/consumers?${q}`);
  },

  // Lazily index one saved answer's column usage (1 TML export, memoized server-side).
  indexAnswer: (guid: string, clusterId: string, orgId: number) =>
    request<{ guid: string; rows_written: number }>(
      `/relationships/answer/${encodeURIComponent(guid)}/index?cluster_id=${clusterId}&org_id=${orgId}`,
      { method: "POST" },
    ),

  // Opt-in: crawl ALL saved answers' column usage as a background job.
  deepIndex: (clusterId: string, orgId: number) =>
    request<{ job_id: string }>(
      `/relationships/deep-index?cluster_id=${clusterId}&org_id=${orgId}`,
      { method: "POST" },
    ),
};
