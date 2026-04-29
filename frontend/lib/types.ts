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

export type JobStatus = "QUEUED" | "PENDING" | "RUNNING" | "COMPLETE" | "PARTIAL" | "FAILED";

export interface Job {
  id: string;
  job_type: string;
  status: JobStatus;
  progress: number;
  total: number;
  progress_pct: number;
  result: Record<string, unknown> | null;
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
  last_accessed_at: string | null;
  view_count: number;
}

// ── Permissions ───────────────────────────────────────────────────────────────

export type ShareMode = "READ_ONLY" | "MODIFY";
export type PrincipalType = "USER" | "USER_GROUP";

export interface Permission {
  principal_id: string;
  principal_name: string;
  principal_type: PrincipalType;
  share_mode: ShareMode;
}

export interface PermissionsResponse {
  ts_guid: string;
  object_name: string;
  permissions: Permission[];
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

// ── Archiver ──────────────────────────────────────────────────────────────────

export interface ArchiverItem {
  ts_guid: string;
  name: string;
  object_type: "LIVEBOARD" | "ANSWER";
  owner_guid: string;
  owner_name: string;
  org_id: number;
  last_accessed_at: string | null;
  modified_at: string | null;
  created_at: string | null;
  view_count: number;
  days_unused: number;
  tags: string[];
}

export interface ArchiverPreview {
  total: number;
  by_type: Record<string, number>;
  criteria_summary: string;
}

export interface AffectedPrincipal {
  name: string;
  type: "USER" | "USER_GROUP";
  object_count: number;
}

export interface DependencyWarning {
  ts_guid: string;
  name: string;
  object_type: string;
  dependents: { name: string; type: string }[];
}

export interface DryRunSummary {
  total: number;
  by_type: Record<string, number>;
  shared_count: number;
  affected_principals: AffectedPrincipal[];
  dependency_warnings: DependencyWarning[];
  errors: { ts_guid: string; error: string }[];
}

export interface ArchiveRecord {
  id: string;
  ts_guid: string;
  name: string;
  object_type: string;
  owner_name: string;
  last_accessed_at: string | null;
  days_unused: number;
  tags: string[];
  tml_export_status: "PENDING" | "SUCCESS" | "FAILED";
  archived_at: string;
  restored_at: string | null;
  restored_as_guid: string | null;
  is_restorable: boolean;
}

export interface ArchiveRecordFlatItem {
  id: string;
  ts_guid: string;
  name: string;
  object_type: string;
  owner_name: string;
  archived_at: string;
  tml_export_status: "PENDING" | "SUCCESS" | "FAILED";
  job_id: string;
}

export interface ArchiveSessionSummary {
  job_id: string;
  archived_at: string;
  total: number;
  succeeded: number;
  failed_tml_export: number;
  failed_delete: number;
}

// ── Bulk Deleter ──────────────────────────────────────────────────────────────

export type DeleterMode = "downstream" | "tag" | "list";

// DeleterItem mirrors ArchiverItem but with a wider object_type union — Bulk
// Deleter operates on Worksheets, Tables, Models, etc., not just Liveboard/Answer.
export interface DeleterItem {
  ts_guid: string;
  name: string;
  object_type: string;
  owner_guid: string;
  owner_name: string;
  org_id: number;
  last_accessed_at: string | null;
  modified_at: string | null;
  created_at: string | null;
  view_count: number;
  days_unused: number;
  tags: string[];
}

export interface DeleterResolveResponse {
  items: DeleterItem[];
  total: number;
  by_type: Record<string, number>;
  root_guid?: string | null;
  tag_name?: string | null;
  unrecognized?: string[];
}

export interface RootSearchItem {
  ts_guid: string;
  name: string;
  object_type: string;
  owner_name: string;
}
