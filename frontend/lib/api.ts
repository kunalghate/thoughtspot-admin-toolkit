/**
 * Typed API client — all calls to the FastAPI backend go through here.
 * Never call /api/* directly from page components.
 */

import type { Cluster, Org, SyncLog, EntityType, Job, MetadataObject, MetadataStats, PaginatedResponse, PermissionsResponse, ArchiverItem, ArchiverPreview, ArchiveRecord, ArchiveSessionSummary, ArchiveRecordFlatItem, DeleterItem, DeleterResolveResponse, RootSearchItem } from "./types";

// In dev mode, Next.js static-export config disables rewrites so we
// hit FastAPI directly on :8000. In production the SPA is served by
// FastAPI itself so /api/v1 resolves to the same origin.
const BASE =
  typeof window !== "undefined" && window.location.port === "3000"
    ? "http://localhost:8000/api/v1"
    : "/api/v1";

// ── Generic fetch wrapper ─────────────────────────────────────────────────────

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  // Parse body safely — some endpoints return 204 No Content
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;

  if (!res.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((e: any) => `${e.loc?.slice(-1)[0] ?? "?"}: ${e.msg ?? JSON.stringify(e)}`).join(", ")
        : `HTTP ${res.status}: ${res.statusText}`;
    throw new Error(message);
  }

  return body as T;
}

// ── Health ────────────────────────────────────────────────────────────────────

export const healthApi = {
  check: () => request<{ status: string }>("/health"),
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

  listOrgs: (id: string) =>
    request<Org[]>(`/clusters/${id}/orgs`),

  listCachedOrgs: (id: string) =>
    request<Org[]>(`/clusters/${id}/orgs/cached`),
};

// ── Sync ──────────────────────────────────────────────────────────────────────

export const syncApi = {
  // clusterId unused — backend uses active cluster from config
  status: (_clusterId: string, orgId = 0) =>
    request<SyncLog[]>(`/sync?org_id=${orgId}`),

  trigger: (_clusterId: string, orgId: number, entityType: EntityType) =>
    request<{ entity_type: string; job_id: string }>(`/sync/${entityType}?org_id=${orgId}`, { method: "POST" }),
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
    return request<PaginatedResponse<MetadataObject>>(`/metadata?${q}`);
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
  list: () =>
    request<Job[]>(`/jobs`),

  get: (jobId: string) =>
    request<Job>(`/jobs/${jobId}`),

  cancel: (jobId: string) =>
    request<void>(`/jobs/${jobId}/cancel`, { method: "DELETE" }),
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
