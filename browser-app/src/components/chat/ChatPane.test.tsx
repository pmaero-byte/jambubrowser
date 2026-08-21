import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock the app store
const defaultStore = {
  messages: [] as any[],
  input: "",
  setInput: vi.fn(),
  isLoading: false,
  activeBrowserTabId: "tab-1",
  setActiveTab: vi.fn(),
  addBrowserTab: vi.fn(),
  updateBrowserTab: vi.fn(),
};

let storeState = { ...defaultStore };

// Selector-aware zustand mock: components call useAppStore((s) => s.field)
// or useAppStore() with no args to get the whole snapshot.
const mockUseAppStore = vi.hoisted(() =>
  vi.fn((selector?: (s: any) => any) =>
    selector ? selector(storeState) : storeState
  )
);
// The component also reads live state off the store module:
// useAppStore.getState().activeBrowserTabId
(mockUseAppStore as any).getState = () => storeState;
vi.mock("../../store/appStore", () => ({
  useAppStore: mockUseAppStore,
}));

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// Mock child components
vi.mock("./MessageCard", () => ({
  MessageCard: ({ message }: any) => <div data-testid="message-card">{message.content}</div>,
}));

vi.mock("./AgentTimeline", () => ({
  AgentTimeline: () => <div data-testid="agent-timeline">Timeline</div>,
}));

vi.mock("./AgentWorking", () => ({
  AgentWorking: () => <div data-testid="agent-working">Working</div>,
}));

describe("ChatPane", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeState = { ...defaultStore };
  });

  // jsdom does not implement scrollIntoView; ChatPane scrolls to bottom on
  // every message render.
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("shows empty state when no messages", async () => {
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} />);
    expect(screen.getByText("What would you like to research?")).toBeDefined();
  });

  it("renders messages when present", async () => {
    storeState = {
      ...defaultStore,
      messages: [
        { id: "1", role: "user", content: "Hello" },
        { id: "2", role: "assistant", content: "Hi there" },
      ],
    };
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} />);
    expect(screen.getAllByTestId("message-card")).toHaveLength(2);
  });

  it("disables send button when input is empty", async () => {
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} />);
    const btn = screen.getByRole("button", { name: /submit/i });
    expect(btn).toBeDisabled();
  });

  it("enables send button when input has text", async () => {
    // The mocked store has no zustand reactivity, so seed the input value
    // directly and assert the disabled={!input.trim()} binding.
    storeState = { ...defaultStore, input: "test query" };
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} />);
    const btn = screen.getByRole("button", { name: /submit/i });
    expect(btn).toBeEnabled();
  });

  it("calls onSend with trimmed input when submitted", async () => {
    storeState = { ...defaultStore, input: "test query" };
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} />);
    // Fire a real submit event on the form (user-event has no .submit()).
    const form = document.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(onSend).toHaveBeenCalledWith("test query");
  });

  it("clears input after send", async () => {
    const setInput = vi.fn();
    storeState = { ...defaultStore, input: "test query", setInput };
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} />);
    const form = document.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(setInput).toHaveBeenCalledWith("");
  });

  it("shows stop button when loading", async () => {
    storeState = { ...defaultStore, isLoading: true };
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    const onStop = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} onStop={onStop} />);
    expect(screen.getByTitle("Stop generation")).toBeDefined();
  });

  it("does not show send button when loading", async () => {
    storeState = { ...defaultStore, isLoading: true };
    const { ChatPane } = await import("./ChatPane");
    const onSend = vi.fn();
    render(<ChatPane agentEvents={[]} onSend={onSend} />);
    expect(screen.queryByTitle("Submit")).toBeNull();
  });
});
