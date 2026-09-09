import { describe, expect, it } from "vitest";
import { NO_TYPE_MATCH, intersectTypes, serializeFilterModel } from "./agGridFilters";
import { OBJECT_TYPES, TYPE_LABELS } from "./objectTypes";

const typeFilter = (term: string) =>
  serializeFilterModel({ object_type: { filterType: "text", type: "contains", filter: term } });

describe("Type column filter", () => {
  // The Type column filters on the LABEL, so every label the grid can render
  // must resolve. Adding a label without a mapping turns this red.
  it.each(OBJECT_TYPES)("resolves the label rendered for %s", (type) => {
    const resolved = typeFilter(TYPE_LABELS[type]).types;
    expect(resolved).toBeDefined();
    expect(resolved).toContain(type);
    expect(resolved).not.toContain(NO_TYPE_MATCH);
  });

  it("resolves the raw API type name too", () => {
    expect(typeFilter("WORKSHEET").types).toContain("WORKSHEET");
    expect(typeFilter("liveboard").types).toEqual(["LIVEBOARD"]);
  });

  it("resolves a label that several cached types share", () => {
    // WORKSHEET, LOGICAL_TABLE and MODEL are all rendered as "Model".
    const resolved = typeFilter("Model").types ?? [];
    expect(resolved).toEqual(expect.arrayContaining(["WORKSHEET", "LOGICAL_TABLE", "MODEL"]));
  });

  it("distinguishes View from SQL View", () => {
    expect(typeFilter("SQL View").types).toEqual(expect.arrayContaining(["SQL_VIEW"]));
    expect(typeFilter("SQL View").types).not.toContain("AGGR_WORKSHEET");
  });

  // The regression: an unrecognised term used to omit `types` entirely, and
  // every caller read a missing key as "no type filter" — so an applied filter
  // silently listed the FULL inventory.
  it("returns a no-match filter rather than dropping the filter", () => {
    expect(typeFilter("zzzz").types).toEqual([NO_TYPE_MATCH]);
  });

  it("ignores an empty term", () => {
    expect(typeFilter("   ").types).toBeUndefined();
  });
});

describe("intersectTypes", () => {
  it("keeps whichever side is present on its own", () => {
    expect(intersectTypes(undefined, ["ANSWER"])).toEqual(["ANSWER"]);
    expect(intersectTypes(["ANSWER"], undefined)).toEqual(["ANSWER"]);
    expect(intersectTypes(["ANSWER"], [])).toEqual(["ANSWER"]);
  });

  it("intersects rather than letting the column filter override the toolbar", () => {
    expect(intersectTypes(["ANSWER", "LIVEBOARD"], ["LIVEBOARD"])).toEqual(["LIVEBOARD"]);
  });

  it("yields no rows when the two disagree completely", () => {
    expect(intersectTypes(["ANSWER"], ["LIVEBOARD"])).toEqual([NO_TYPE_MATCH]);
  });
});

describe("Connection column filter", () => {
  // The Connection column carried `filter: false`, because the metadata row
  // stores only `connection_guid` and there was no server-side way to match on
  // the name. There is now (a join on ts_connections), so the term has to reach
  // the backend as `connection_name_search` — dropping it would render as a lit
  // filter icon over an unfiltered grid.
  it("maps the connection column term to connection_name_search", () => {
    const out = serializeFilterModel({
      connection_name: { filterType: "text", type: "contains", filter: "Snowflake" },
    });
    expect(out.connection_name_search).toBe("Snowflake");
  });

  it("ignores a blank term rather than sending an empty match-everything filter", () => {
    const out = serializeFilterModel({
      connection_name: { filterType: "text", type: "contains", filter: "   " },
    });
    expect(out.connection_name_search).toBeUndefined();
  });
});
