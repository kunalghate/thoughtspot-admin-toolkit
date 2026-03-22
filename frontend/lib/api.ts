/**
 * Typed API client — all calls to the FastAPI backend go through here.
 * Never call /api/* directly from page components.
 */

import type { Cluster, Org, SyncLog, EntityType, Job, MetadataObject, MetadataStats, PaginatedResponse } from "./types";

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
};

// ── Sync ──────────────────────────────────────────────────────────────────────

export const syncApi = {
  status: (clusterId: string) =>
    request<SyncLog[]>(`/sync/${clusterId}/status`),

  trigger: (clusterId: string, orgId: number, entityType: EntityType) =>
    request<Job>(`/sync/${clusterId}/${orgId}/${entityType}`, { method: "POST" }),
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
    page?: number;
    page_size?: number;
  }) => {
    const q = new URLSearchParams();
    q.set("cluster_id", params.cluster_id);
    q.set("org_id", String(params.org_id));
    if (params.types)      params.types.forEach((t) => q.append("types", t));
    if (params.owner_guid) q.set("owner_guid", params.owner_guid);
    if (params.tag_names)  params.tag_names.forEach((t) => q.append("tag_names", t));
    if (params.search)     q.set("search", params.search);
    if (params.stale_days) q.set("stale_days", String(params.stale_days));
    if (params.page)       q.set("page", String(params.page));
    if (params.page_size)  q.set("page_size", String(params.page_size));
    return request<PaginatedResponse<MetadataObject>>(`/metadata?${q}`);
  },

  stats: (clusterId: string, orgId: number) =>
    request<MetadataStats>(`/metadata/stats?cluster_id=${clusterId}&org_id=${orgId}`),

  get: (guid: string, clusterId: string, orgId: number) =>
    request<MetadataObject>(`/metadata/${guid}?cluster_id=${clusterId}&org_id=${orgId}`),
};

// ── Jobs ──────────────────────────────────────────────────────────────────────

export const jobsApi = {
  list: (clusterId: string) =>
    request<Job[]>(`/jobs?cluster_id=${clusterId}`),

  get: (jobId: number) =>
    request<Job>(`/jobs/${jobId}`),
};
