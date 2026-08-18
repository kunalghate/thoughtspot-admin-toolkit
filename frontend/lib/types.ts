// ── Cluster & Org ─────────────────────────────────────────────────────────────

export type ConnectionStatus = "unknown" | "connected" | "expired" | "unreachable";

export interface Cluster {
  id: string;
  name: string;
  url: string;
  username: string;
  auth_type: "basic" | "trusted" | "bearer";
  is_active: boolean;
  created_at: string;
  // Live session health from the backend (see connection_status registry).
  connection_status: ConnectionStatus;
  connection_detail?: string | null;
  connection_checked_at?: string | null;
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
  error_type: string | null;
  error_traceback: string | null;
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

export interface GroupListItem {
  ts_guid: string;
  name: string;
  display_name: string;
  description: string;
  org_id: number;
  privileges: string[];
  member_count: number;
  /** Creator's display name, or the raw GUID if that user isn't cached. */
  created_by: string | null;
  created_at: string | null;
  modified_at: string | null;
  synced_at: string | null;
}

export interface GroupMember {
  ts_guid: string;
  username: string;
  display_name: string;
  email: string;
  status: string;
}

export interface GroupDetail extends GroupListItem {
  members: GroupMember[];
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
  archivable_total: number;
  stale_90d: number;
  never_accessed: number;
  last_synced: string | null;
  /** False when the last metadata sync was interrupted (or never ran) — the
   *  cached rows are real but possibly a truncated slice of the org. */
  cache_authoritative: boolean;
}

export interface MetadataListResponse extends PaginatedResponse<MetadataObject> {
  /** See MetadataStats.cache_authoritative. */
  cache_authoritative: boolean;
  /** Cached objects in this cluster/org hidden by the System User filter.
   *  `total` is what the admin can act on; this is what was withheld — the grid
   *  needs it to explain an empty result instead of looking like a failed sync. */
  hidden_system_count: number;
}

/**
 * Page-numbered envelope. Only `/metadata` uses this — every other paginated
 * endpoint sends `record_offset` and wants `OffsetPaginatedResponse`. Declaring
 * `page` on those made the type promise a field that arrives `undefined`.
 */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** Offset-paginated envelope (AG Grid infinite model) — no `page` field. */
export interface OffsetPaginatedResponse<T> {
  items: T[];
  total: number;
  record_offset: number;
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

// ── User Management ───────────────────────────────────────────────────────────

export interface UserListItem {
  ts_guid: string;
  username: string;
  display_name: string;
  email: string;
  status: "ACTIVE" | "INACTIVE";
  created_at: string | null;
  modified_at: string | null;
  synced_at: string | null;
}

export interface UserGroupInfo {
  ts_guid: string;
  name: string;
  display_name: string;
  privileges: string[];
}

export interface UserDetail extends UserListItem {
  owned_object_count: number;
  org_ids: number[];
  groups: string[];
  group_details: UserGroupInfo[];
  privileges: string[]; // effective: union of all group privileges
  is_admin: boolean;
}

export interface UserAccessResponse {
  items: SharingPermissionItem[];
  total: number;
  by_type: Record<string, number>;
}

export interface TransferObjectItem {
  ts_guid: string;
  name: string;
  object_type: string;
  owner_guid: string;
  owner_name: string;
  modified_at: string | null;
  tags: string[];
}

export interface TransferPreviewResponse {
  items: TransferObjectItem[];
  total: number;
  by_type: Record<string, number>;
}

export interface SharingPermissionItem {
  metadata_id: string;
  metadata_name: string;
  metadata_type: string;
  share_mode: string;
}

export interface TransferSharingPreviewResponse {
  items: SharingPermissionItem[];
  total: number;
  by_type: Record<string, number>;
}

export interface DeletePreviewItem extends UserListItem {
  owned_object_count: number;
  is_admin: boolean;
}

export interface DeletePreviewResponse {
  items: DeletePreviewItem[];
  total: number;
  unrecognized: string[];
}

/** Item shape inside a user-delete dry-run job result (adds the live-existence flag). */
export interface DeleteDryRunItem extends DeletePreviewItem {
  exists_live: boolean;
}

/** Stored in Job.result for a `user_delete_dryrun` job. */
export interface DeleteDryRunResult {
  total: number;
  items: DeleteDryRunItem[];
  unrecognized: string[];
  missing_live: string[];   // usernames/GUIDs no longer present on the cluster
  admin_count: number;
  owned_total: number;
}

export interface UserHistoryItem {
  id: string;
  job_id: string;
  action_type: "transfer" | "transfer_sharing" | "delete";
  from_username: string;
  from_display_name: string;
  to_username: string;
  to_display_name: string;
  items_total: number;
  items_succeeded: number;
  items_failed: number;
  status: "PENDING" | "SUCCESS" | "PARTIAL" | "FAILED";
  error: string | null;
  executed_at: string;
}

// ── Bulk Sharing ──────────────────────────────────────────────────────────────

export type SharePermissionMode = "READ_ONLY" | "MODIFY" | "NO_ACCESS";

export interface PrincipalPickerItem {
  ts_guid: string;
  name: string;
  display_name: string;
  principal_type: "USER" | "USER_GROUP";
}

export interface SharingPreviewRow {
  object_guid: string;
  object_name: string;
  object_type: string;
  principal_guid: string;
  principal_name: string;
  principal_type: string;
  previous_mode: string;
  new_mode: string;
  will_change: boolean;
}

export interface SharingPreviewResponse {
  items: SharingPreviewRow[];
  total: number;
  will_change_count: number;
}

export interface SharingHistoryItem {
  job_id: string;
  executed_at: string;
  object_count: number;
  principal_count: number;
  succeeded: number;
  failed: number;
  status: "SUCCESS" | "PARTIAL" | "FAILED";
}

// ── Relationships / Data Lineage ──────────────────────────────────────────────

export type LineageNodeType =
  | "CONNECTION" | "DB_TABLE" | "LOGICAL_TABLE" | "MODEL" | "ANSWER" | "LIVEBOARD";

/** The three explorer tabs' root_kind path segment. */
export type RootKind = "model" | "answer" | "liveboard" | "connection";

export interface TopologyItem {
  ts_guid: string;
  name: string;
  object_type: string;
  node_type: LineageNodeType;
  subtype: string;      // left-list filter label: Model | Table | Dataset | View
  owner_name: string;
}

export interface TopologyResponse {
  logical_tables: TopologyItem[];
  answers: TopologyItem[];
  liveboards: TopologyItem[];
  connections: TopologyItem[];
}

export interface LineageNode {
  guid: string;
  name: string;
  node_type: LineageNodeType;
  layer: number;         // pipeline depth by type; the graph lays out by distance from the root instead
  owner_name: string;
  accessible: boolean;
}

export interface LineageEdge {
  source: string;        // consumer / downstream
  target: string;        // producer / upstream
  relation: "USES" | "CONNECTS" | string;
}

export interface ColumnUsedBy {
  guid: string;
  name: string;
  node_type: string;
}

export interface ColumnLineageRow {
  model_guid: string;
  model_column_name: string;
  table_guid: string;
  table_column_name: string;
  db_table: string;
  db_column_name: string;
  connection_name: string;
  /** Computed column (TML formula) — no physical chain by design. */
  is_formula: boolean;
  used_by: ColumnUsedBy[];
}

export interface LineageGraphResponse {
  root: LineageNode;
  root_kind: RootKind;
  nodes: LineageNode[];
  edges: LineageEdge[];
  consumer_totals: Record<string, number>;
  capped: boolean;
  impact: { downstream_count: number };
  columns: ColumnLineageRow[];
}

export interface ConsumerItem {
  guid: string;
  name: string;
  node_type: string;
  owner_name: string;
}

export interface ConsumersResponse {
  items: ConsumerItem[];
  total: number;
  offset: number;
  limit: number;
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export interface DashboardCounts {
  users: number;
  groups: number;
  tags: number;
  objects_total: number;
  objects_by_type: Record<string, number>;
  archivable_total: number;
  /** Archivable objects last accessed 90+ days ago (real date, aged out). */
  stale_90d: number;
  /** Archivable objects with no access date at all — unknown, not unused. */
  never_accessed: number;
}

export interface DashboardAttention {
  inactive_users: number;
  users_without_group: number;
  empty_groups: number;
  orphaned_content: number;
}

export interface DashboardJob {
  id: string;
  job_type: string;
  status: string;
  created_at: string | null;
  error: string | null;
  error_type: string | null;
}

export interface DashboardRunningJob {
  id: string;
  job_type: string;
  status: string;
  progress: number;
  total: number;
}

export interface DashboardActivity {
  kind: "delete" | "share" | "user_action";
  label: string;
  status: string;
  timestamp: string | null;
  /** Identical adjacent entries folded into one row. */
  count: number;
}

export interface DashboardSummary {
  counts: DashboardCounts;
  /** Per-entity "has this ever synced?" — tells a real zero from a missing number. */
  synced: Partial<Record<EntityType, boolean>>;
  /** When each entity last synced successfully (null = never). */
  synced_at: Partial<Record<EntityType, string | null>>;
  /** Per-entity "is a sync running right now?". `synced` is False for the whole
   *  duration of a healthy sync, so this is what tells "in flight" apart from
   *  "never synced". Optional: older backends omit it. */
  syncing?: Partial<Record<EntityType, boolean>>;
  /** Change in record count since the previous successful sync of each entity. */
  deltas: Partial<Record<EntityType, number>>;
  attention: DashboardAttention;
  recent_jobs: DashboardJob[];
  running_jobs: DashboardRunningJob[];
  recent_activity: DashboardActivity[];
  failed_jobs_7d: number;
}

// ── Updates ───────────────────────────────────────────────────────────────────

export interface UpdateCheck {
  /** The version currently running. */
  current: string;
  /** Latest published release, or null when the check could not be made. */
  latest: string | null;
  update_available: boolean;
  /** False when GitHub was unreachable — show nothing rather than guessing. */
  checked: boolean;
  /** The exact command the user types to upgrade. Rendered verbatim. */
  command: string;
  release_url: string;
  wheel_url: string | null;
  error: string | null;
}
