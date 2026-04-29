/**
 * From-Tag mode intake — load distinct tags from the cache, let the user
 * pick one, report the picked name up to the parent.
 */
import { useEffect, useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { deleterApi } from "@/lib/api";

interface Props {
  clusterId: string;
  orgId: number;
  pickedTag: string | null;
  onPick: (tagName: string | null) => void;
}

export function TagPicker({ clusterId, orgId, pickedTag, onPick }: Props) {
  const [tags, setTags] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    deleterApi
      .tags({ cluster_id: clusterId, org_id: orgId })
      .then((rows) => setTags(rows))
      .catch(() => setTags([]))
      .finally(() => setLoading(false));
  }, [clusterId, orgId]);

  if (pickedTag) {
    return (
      <div style={{
        display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
        background: "#F5F0FF", border: "1px solid #C4B5FD", borderRadius: 6,
      }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#5B21B6" }}>
          Tag: {pickedTag}
        </span>
        <button
          onClick={() => onPick(null)}
          style={{
            marginLeft: "auto", padding: "4px 10px", fontSize: 12,
            background: "transparent", border: "1px solid #C4B5FD", borderRadius: 4,
            color: "#6D28D9", cursor: "pointer", fontFamily: "Geist, sans-serif",
          }}
        >Change tag</button>
      </div>
    );
  }

  return (
    <div style={{ position: "relative", maxWidth: 400 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        style={{
          width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "9px 12px", fontSize: 13, background: "white",
          border: "1px solid #E8E1D5", borderRadius: 6, cursor: "pointer",
          fontFamily: "Geist, sans-serif", color: "#0F0E0D",
        }}
      >
        <span style={{ color: tags.length === 0 && !loading ? "#9CA3AF" : "#0F0E0D" }}>
          {loading ? "Loading tags…" : tags.length === 0 ? "No tags found in cache" : "Pick a tag…"}
        </span>
        <ChevronDown size={14} />
      </button>

      {open && tags.length > 0 && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0,
          background: "white", border: "1px solid #E8E1D5", borderRadius: 6,
          maxHeight: 280, overflowY: "auto", zIndex: 10,
          boxShadow: "0 6px 16px -4px rgba(0,0,0,0.08)",
        }}>
          {tags.map((t) => (
            <button
              key={t}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { onPick(t); setOpen(false); }}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "8px 12px", fontSize: 13, background: "transparent",
                border: "none", borderBottom: "1px solid #F4EFE6", cursor: "pointer",
                fontFamily: "Geist, sans-serif", color: "#0F0E0D",
              }}
            >{t}</button>
          ))}
        </div>
      )}
    </div>
  );
}
