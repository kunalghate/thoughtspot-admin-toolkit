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

const TYPE_KEYWORDS: Array<{ kw: string; type: string }> = [
  { kw: "live", type: "LIVEBOARD" },
  { kw: "answer", type: "ANSWER" },
];

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
      const v = (entry.filter ?? "").toString().toLowerCase().trim();
      if (!v) continue;
      const matches = TYPE_KEYWORDS.filter(({ kw }) => kw.startsWith(v) || v.startsWith(kw)).map((k) => k.type);
      if (matches.length) out.types = matches;
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
