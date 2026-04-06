// ── Cluster & Org ─────────────────────────────────────────────────────────────

export interface Cluster {
  id: string;
  name: string;
  url: string;
  username: string;
  auth_type: "basic" | "trusted" | "bearer";
  is_active: boolean;
  created_at: string;
}

export interface Org {
  org_id: number;
  name: string;
  description?: string;
  status: string;
}

// ── Sync ──────────────────────────────────────────────────────────────────────

export type EntityType = "users" | "groups" | "metadata" | "tags" | "orgs" | "dependencies";
export type SyncStatus = "SUCCESS" | "FAILED" | "IN_PROGRESS" | "NOT_SYNCED";

export interface SyncLog {
  entity_type: EntityType;
  synced_at: string | null;
  record_count: number | null;
  status: SyncStatus;
  error: string | null;
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

export type JobStatus = "PENDING" | "RUNNING" | "COMPLETE" | "PARTIAL" | "FAILED";

export interface Job {
  id: string;
  job_type: string;
  status: JobStatus;
  progress: number;
  total: number;
  progress_pct: number;
  created_at: string;
  completed_at: string | null;
  error: string | null;
}

// ── Users ─────────────────────────────────────────────────────────────────────

export interface User {
  ts_guid: string;
  username: string;
  display_name: string;
  email: string;
  status: "ACTIVE" | "INACTIVE";
  created_at: string | null;
  orgs: number[];
  groups: string[];
}

// ── Groups ────────────────────────────────────────────────────────────────────

export interface Group {
  ts_guid: string;
  name: string;
  display_name: string;
  description: string;
  org_id: number;
  visibility: string;
  member_count: number;
}

// ── Metadata ──────────────────────────────────────────────────────────────────

export type ObjectType = "LIVEBOARD" | "ANSWER" | "LOGICAL_TABLE" | "WORKSHEET" | "TABLE";

export interface MetadataObject {
  ts_guid: string;
  name: string;
  object_type: ObjectType;
  owner_guid: string;
  owner_name: string;
  org_id: number;
  tags: string[];
  created_at: string | null;
  modified_at: string | null;
}

// ── API response wrappers ─────────────────────────────────────────────────────

export interface ApiError {
  detail: string;
}

export interface MetadataStats {
  total: number;
  by_type: Record<string, number>;
  stale_90d: number;
  last_synced: string | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
