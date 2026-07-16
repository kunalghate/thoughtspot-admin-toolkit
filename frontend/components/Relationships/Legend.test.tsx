import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Legend } from "./Legend";
import { NODE_STYLES, NODE_STYLE_ORDER } from "./nodeStyles";

describe("Legend", () => {
  it("renders a labelled entry for each node type", () => {
    render(<Legend />);

    // Drive from the source of truth so the assertion stays in sync with
    // every node type in NODE_STYLE_ORDER / NODE_STYLES.
    for (const key of NODE_STYLE_ORDER) {
      expect(screen.getByText(NODE_STYLES[key].label)).toBeInTheDocument();
    }
  });

  it("renders the inaccessible-node key", () => {
    render(<Legend />);

    // Rendered directly in Legend.tsx, not from NODE_STYLES.
    expect(screen.getByText("Inaccessible")).toBeInTheDocument();
  });
});
