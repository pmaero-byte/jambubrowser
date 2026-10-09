import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AbilitiesGrid } from "./AbilitiesGrid";

// localFetch is used only for liveness probes; stub it to a never-resolving
// promise so tests don't touch the network.
vi.mock("../../utils/api", () => ({
  localFetch: vi.fn(() => new Promise(() => {})),
}));

// The store is stable between tests; reset to a known tab.
import { useAppStore } from "../../store/appStore";

beforeEach(() => {
  useAppStore.setState({ activeTab: "chat" });
});

describe("AbilitiesGrid", () => {
  it("renders every pillar section by default", () => {
    render(<AbilitiesGrid />);
    for (const label of ["Browser", "Harness", "DeepNet", "Developer", "Decentralized", "Desktop"]) {
      expect(screen.getByRole("heading", { name: label })).toBeInTheDocument();
    }
  });

  it("shows ability cards and the search filters them", async () => {
    render(<AbilitiesGrid />);
    expect(screen.getByText("Live Chromium pane")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("abilities-search"), { target: { value: "torrent" } });
    expect(screen.getAllByTestId(/^ability-/)).toHaveLength(2); // downloads + torrents
  });

  it("filters by pillar chip", async () => {
    render(<AbilitiesGrid />);
    fireEvent.click(screen.getByTestId("abilities-filter-harness"));
    await waitFor(() => {
      expect(screen.getByText("Research chat")).toBeInTheDocument();
      expect(screen.queryByText("Live Chromium pane")).not.toBeInTheDocument();
    });
  });

  it("expands a card to reveal the demo hint, and Open navigates", async () => {
    render(<AbilitiesGrid />);
    fireEvent.click(screen.getByTestId("ability-browser-downloads"));
    await waitFor(() => {
      expect(screen.getByText(/Add a big file URL or magnet/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("ability-open-browser-downloads"));
    expect(useAppStore.getState().activeTab).toBe("browser");
  });

  it("renders a no-results state when the filter matches nothing", async () => {
    render(<AbilitiesGrid />);
    fireEvent.change(screen.getByTestId("abilities-search"), { target: { value: "zzz-no-match" } });
    await waitFor(() => {
      expect(screen.getByText("No abilities match that filter.")).toBeInTheDocument();
    });
  });
});
