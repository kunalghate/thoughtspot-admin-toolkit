/**
 * From-List mode intake — paste GUIDs (newline or comma) or upload a CSV.
 * Splits, dedupes, reports the GUID list up to the parent for resolution.
 */
import { useRef, useState } from "react";
import { Upload } from "lucide-react";

interface Props {
  onSubmit: (guids: string[]) => void;
  onClear: () => void;
  hasResolved: boolean;
}

function splitGuids(raw: string): string[] {
  // Newline OR comma separated. Strip whitespace, drop empties.
  return raw
    .split(/[\s,]+/)
    .map((g) => g.trim())
    .filter(Boolean);
}

export function ListPaste({ onSubmit, onClear, hasResolved }: Props) {
  const [raw, setRaw] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const previewCount = splitGuids(raw).length;

  function handleFile(file: File) {
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = String(e.target?.result ?? "");
      // Take first column of each non-empty line; skip a header row that contains "guid"/"id"
      const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
      const out: string[] = [];
      lines.forEach((line, i) => {
        const first = line.split(",")[0]?.trim() ?? "";
        if (i === 0 && /^(guid|id|identifier)$/i.test(first)) return; // skip header
        if (first) out.push(first);
      });
      setRaw(out.join("\n"));
    };
    reader.readAsText(file);
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        placeholder="Paste GUIDs here — one per line, or comma-separated."
        rows={8}
        style={{
          width: "100%", padding: "10px 12px", fontSize: 12,
          fontFamily: "ui-monospace, SF Mono, Menlo, monospace",
          border: "1px solid #E8E1D5", borderRadius: 6, outline: "none",
          background: "white", resize: "vertical",
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, fontFamily: "Geist, sans-serif" }}>
        <button
          onClick={() => fileRef.current?.click()}
          style={{
            display: "flex", alignItems: "center", gap: 6, padding: "7px 12px", fontSize: 12,
            background: "white", border: "1px solid #E8E1D5", borderRadius: 6,
            cursor: "pointer", color: "#0F0E0D",
          }}
        >
          <Upload size={13} /> Upload CSV
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.txt"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
            if (fileRef.current) fileRef.current.value = "";
          }}
        />

        <span style={{ fontSize: 12, color: "#7A7068" }}>
          {previewCount > 0 ? `${previewCount} GUIDs detected` : "No GUIDs yet"}
        </span>

        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {hasResolved && (
            <button
              onClick={() => { setRaw(""); onClear(); }}
              style={{
                padding: "7px 14px", fontSize: 12,
                background: "transparent", border: "1px solid #E8E1D5", borderRadius: 6,
                cursor: "pointer", color: "#0F0E0D",
              }}
            >Clear</button>
          )}
          <button
            disabled={previewCount === 0}
            onClick={() => onSubmit(splitGuids(raw))}
            style={{
              padding: "7px 14px", fontSize: 12, fontWeight: 600,
              background: previewCount === 0 ? "#E8E1D5" : "#6D28D9",
              color: previewCount === 0 ? "#9CA3AF" : "white",
              border: "none", borderRadius: 6,
              cursor: previewCount === 0 ? "not-allowed" : "pointer",
            }}
          >Resolve list</button>
        </div>
      </div>
    </div>
  );
}
