import { OBJECT_TYPES, TYPE_LABELS } from "@/lib/objectTypes";

// ── AG Grid filter model → backend params ─────────────────────────────────
// Maps AG Grid's getFilterModel() output to query-string params shared by
// /archiver/results, /archiver/records, and /metadata. Returned object uses
// snake_case keys matching the FastAPI Query() params.

export type SerializedFilters = {
  search?: string;
  types?: string[];
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
  archived_before?: string;
  archived_after?: string;
};

/**
 * A `types` value that matches nothing, for a Type filter whose term names no
 * known type. The alternative — omitting the key — reads downstream as "no type
 * filter", which showed the FULL list under a filter the user had just applied.
 */
export const NO_TYPE_MATCH = "__no_matching_type__";

/**
 * Resolve a Type-column search term to the `object_type` values it names.
 *
 * The Type column filters on the *label* (`filterValueGetter` → `TYPE_LABELS`),
 * so the term the user types is "Model" or "SQL View", never `WORKSHEET`. Both
 * vocabularies are matched — a label because that is what is on screen, a raw
 * type because it is what the API and the CSV export use — and labels are
 * many-to-one (`WORKSHEET`, `LOGICAL_TABLE` and `MODEL` are all "Model"), so a
 * term resolves to a LIST of types, not one.
 */
function resolveTypeTerm(term: string): string[] {
  const v = term.toLowerCase().trim();
  if (!v) return [];
  const matched = OBJECT_TYPES.filter(
    (t) => (TYPE_LABELS[t] ?? t).toLowerCase().includes(v) || t.toLowerCase().includes(v),
  );
  // Aliases for the same label that are cached but not separately selectable —
  // searching "Model" must also reach rows stored as LOGICAL_TABLE.
  const labels = new Set(matched.map((t) => TYPE_LABELS[t] ?? t));
  return Object.keys(TYPE_LABELS).filter(
    (t) => matched.includes(t) || labels.has(TYPE_LABELS[t]),
  );
}

/**
 * Combine the Type column filter with the toolbar's type pills.
 *
 * They intersect. Before, callers wrote `f.types ?? toolbarTypes`, so a column
 * filter silently REPLACED an active pill — answers listed under a lit
 * "Liveboard" pill.
 */
export function intersectTypes(
  filterTypes: string[] | undefined,
  toolbarTypes: string[] | undefined,
): string[] | undefined {
  if (!filterTypes) return toolbarTypes;
  if (!toolbarTypes || toolbarTypes.length === 0) return filterTypes;
  const both = filterTypes.filter((t) => toolbarTypes.includes(t));
  return both.length > 0 ? both : [NO_TYPE_MATCH];
}

function dateOnly(iso: string | undefined): string | undefined {
  if (!iso) return undefined;
  return iso.slice(0, 10); // "YYYY-MM-DD HH:mm:ss" → "YYYY-MM-DD"
}

export function serializeFilterModel(model: Record<string, any>): SerializedFilters {
  const out: SerializedFilters = {};
  for (const [field, entry] of Object.entries(model ?? {})) {
    if (!entry) continue;

    if (field === "name") {
      const v = (entry.filter ?? "").toString().trim();
      if (v) out.search = v;
      continue;
    }

    if (field === "object_type") {
      const v = (entry.filter ?? "").toString().trim();
      if (!v) continue;
      const matches = resolveTypeTerm(v);
      out.types = matches.length > 0 ? matches : [NO_TYPE_MATCH];
      continue;
    }

    if (field === "owner_name") {
      const v = (entry.filter ?? "").toString().trim();
      if (v) out.owner_name_search = v;
      continue;
    }

    if (field === "tags") {
      const v = (entry.filter ?? "").toString().trim();
      if (v) out.tag_search = v;
      continue;
    }

    if (field === "days_unused") {
      const op = entry.type;
      const n = Number(entry.filter);
      if (Number.isFinite(n)) {
        if (op === "greaterThan") out.days_unused_min = n;
        else if (op === "lessThan") out.days_unused_max = n;
        else if (op === "equals") { out.days_unused_min = n; out.days_unused_max = n; }
      }
      continue;
    }

    if (field === "view_count") {
      const op = entry.type;
      const n = Number(entry.filter);
      if (Number.isFinite(n)) {
        if (op === "greaterThan") out.views_min = n;
        else if (op === "lessThan") out.views_max = n;
        else if (op === "equals") { out.views_min = n; out.views_max = n; }
      }
      continue;
    }

    if (field === "last_accessed_at" || field === "modified_at" || field === "created_at" || field === "archived_at") {
      const op = entry.type;
      const from = dateOnly(entry.dateFrom);
      const to = dateOnly(entry.dateTo);
      const beforeKey = (
        field === "last_accessed_at" ? "last_accessed_before" :
        field === "modified_at"      ? "modified_before"      :
        field === "created_at"       ? "created_before"       :
                                       "archived_before"
      ) as keyof SerializedFilters;
      const afterKey = (
        field === "last_accessed_at" ? "last_accessed_after" :
        field === "modified_at"      ? "modified_after"      :
        field === "created_at"       ? "created_after"       :
                                       "archived_after"
      ) as keyof SerializedFilters;
      if (op === "equals" && from)        { (out as any)[afterKey] = from; (out as any)[beforeKey] = from; }
      else if (op === "lessThan" && from) { (out as any)[beforeKey] = from; }
      else if (op === "greaterThan" && from) { (out as any)[afterKey] = from; }
      else if (op === "inRange") {
        if (from) (out as any)[afterKey] = from;
        if (to)   (out as any)[beforeKey] = to;
      }
      continue;
    }
  }
  return out;
}
