/**
 * Typed API client — all calls to the FastAPI backend go through here.
 * Never call /api/* directly from page components.
 */

import type { Cluster, Org, SyncLog, EntityType, Job, MetadataObject, MetadataStats, PaginatedResponse } from "./types";

const BASE = "/api/v1";

// ── Generic fetch wrapper ─────────────────────────────────────────────────────

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }

  return res.json() as Promise<T>;
}

// ── Health ────────────────────────────────────────────────────────────────────

export const healthApi = {
  check: () => request<{ status: string }>("/health"),
};

// ── Clusters ──────────────────────────────────────────────────────────────────

export const clustersApi = {
  list: () => request<Cluster[]>("/clusters"),

  create: (data: {
    name: string;
    url: string;
    username: string;
    auth_type: string;
    password?: string;
    secret_key?: string;
    token?: string;
  }) => request<Cluster>("/clusters", { method: "POST", body: JSON.stringify(data) }),

  update: (id: string, data: Partial<Cluster>) =>
    request<Cluster>(`/clusters/${id}`, { method: "PATCH", body: JSON.stringify(data) }),

  delete: (id: string) =>
    request<void>(`/clusters/${id}`, { method: "DELETE" }),

  testConnection: (id: string) =>
    request<{ ok: boolean; username: string; ts_version: string }>(`/clusters/${id}/test`),

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
