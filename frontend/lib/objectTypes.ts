/**
 * One vocabulary for ThoughtSpot object types, used everywhere the app names one.
 *
 * These labels were previously copy-pasted into seven files and had already drifted
 * ("Agg Worksheet" / "Agg WS", "User Defined" / "User Def."). Import from here so a
 * rename is a one-line change.
 *
 * Keep in step with `_SUBTYPE_LABEL` in ts_admin/services/lineage_service.py.
 *
 * The keys are the `object_type` values the backend caches. DATASET is not a
 * ThoughtSpot API type — the backend derives it for Analyst Studio datasets, which
 * are otherwise indistinguishable from ordinary tables.
 */

export const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD:          "Liveboard",
  ANSWER:             "Answer",
  WORKSHEET:          "Model",
  LOGICAL_TABLE:      "Model",        // legacy cached records
  MODEL:              "Model",
  ONE_TO_ONE_LOGICAL: "Table",
  TABLE:              "Table",
  DATASET:            "Dataset",      // Analyst Studio
  AGGR_WORKSHEET:     "View",
  VIEW:               "View",
  SQL_VIEW:           "SQL View",
  USER_DEFINED:       "CSV Upload",
};

export const TYPE_LABELS_PLURAL: Record<string, string> = {
  LIVEBOARD:          "Liveboards",
  ANSWER:             "Answers",
  WORKSHEET:          "Models",
  LOGICAL_TABLE:      "Models",
  MODEL:              "Models",
  ONE_TO_ONE_LOGICAL: "Tables",
  TABLE:              "Tables",
  DATASET:            "Datasets",
  AGGR_WORKSHEET:     "Views",
  VIEW:               "Views",
  SQL_VIEW:           "SQL Views",
  USER_DEFINED:       "CSV Uploads",
};

/** Selectable object types, in the order they appear in filters and pickers. */
export const OBJECT_TYPES = [
  "LIVEBOARD",
  "ANSWER",
  "WORKSHEET",
  "ONE_TO_ONE_LOGICAL",
  "DATASET",
  "AGGR_WORKSHEET",
  "SQL_VIEW",
  "USER_DEFINED",
];

/** Label for an object type, falling back to the raw value for unknown types. */
export const typeLabel = (type: string | undefined | null): string =>
  (type && TYPE_LABELS[type]) || type || "";

export const typeLabelPlural = (type: string | undefined | null): string =>
  (type && TYPE_LABELS_PLURAL[type]) || typeLabel(type);
