import { describe, it, expect, beforeEach } from "vitest";
import { useAppStore } from "./appStore";

// Reset store between tests so order doesn't matter.
beforeEach(() => {
  useAppStore.setState({
    sidebarOpen: true,
    inspectorOpen: true,
    activeTab: "chat",
    activeModel: "auto",
    privacyMode: "enhanced",
    messages: [],
    input: "",
    isLoading: false,
    browserTabs: [{ id: "1", url: "about:blank", title: "New Tab" }],
    activeBrowserTabId: "1",
    commandOpen: false,
    onboardingOpen: false,
  });
});

describe("appStore - sidebar / inspector", () => {
  it("toggles sidebar open/closed", () => {
    const { toggleSidebar } = useAppStore.getState();
    expect(useAppStore.getState().sidebarOpen).toBe(true);
    toggleSidebar();
    expect(useAppStore.getState().sidebarOpen).toBe(false);
    toggleSidebar();
    expect(useAppStore.getState().sidebarOpen).toBe(true);
  });

  it("toggles inspector independently from sidebar", () => {
    const { toggleSidebar, toggleInspector } = useAppStore.getState();
    toggleSidebar();
    expect(useAppStore.getState().sidebarOpen).toBe(false);
    expect(useAppStore.getState().inspectorOpen).toBe(true); // unaffected
    toggleInspector();
    expect(useAppStore.getState().inspectorOpen).toBe(false);
  });
});

describe("appStore - tab + model + privacy", () => {
  it("sets active tab", () => {
    useAppStore.getState().setActiveTab("audit");
    expect(useAppStore.getState().activeTab).toBe("audit");
  });

  it("sets active model", () => {
    useAppStore.getState().setActiveModel("claude-opus-4-8");
    expect(useAppStore.getState().activeModel).toBe("claude-opus-4-8");
  });

  it("sets privacy mode", () => {
    useAppStore.getState().setPrivacyMode("local_only");
    expect(useAppStore.getState().privacyMode).toBe("local_only");
  });
});

describe("appStore - chat messages", () => {
  it("adds a chat message", () => {
    useAppStore.getState().addMessage({ id: "1", role: "user", content: "hi" });
    expect(useAppStore.getState().messages).toHaveLength(1);
    expect(useAppStore.getState().messages[0].content).toBe("hi");
  });

  it("appends messages in order, does not replace", () => {
    const { addMessage } = useAppStore.getState();
    addMessage({ id: "1", role: "user", content: "first" });
    addMessage({ id: "2", role: "assistant", content: "second" });
    addMessage({ id: "3", role: "user", content: "third" });
    const msgs = useAppStore.getState().messages;
    expect(msgs.map((m) => m.content)).toEqual(["first", "second", "third"]);
  });

  it("updates the last message via patch", () => {
    useAppStore.setState({
      messages: [{ id: "1", role: "assistant", content: "" }],
    });
    useAppStore.getState().updateLastMessage({ content: "hello" });
    expect(useAppStore.getState().messages[0].content).toBe("hello");
  });

  it("updateLastMessage is a no-op when messages is empty", () => {
    // Should not throw
    useAppStore.getState().updateLastMessage({ content: "x" });
    expect(useAppStore.getState().messages).toHaveLength(0);
  });

  it("updateLastMessage merges fields, doesn't replace the message object", () => {
    useAppStore.setState({
      messages: [{ id: "1", role: "assistant", content: "", sources: ["a"] }],
    });
    useAppStore.getState().updateLastMessage({ content: "done" });
    const m = useAppStore.getState().messages[0];
    expect(m.content).toBe("done");
    expect(m.sources).toEqual(["a"]); // preserved
    expect(m.id).toBe("1"); // preserved
  });
});

describe("appStore - input + loading", () => {
  it("sets and clears input", () => {
    useAppStore.getState().setInput("hello world");
    expect(useAppStore.getState().input).toBe("hello world");
    useAppStore.getState().setInput("");
    expect(useAppStore.getState().input).toBe("");
  });

  it("toggles isLoading", () => {
    useAppStore.getState().setIsLoading(true);
    expect(useAppStore.getState().isLoading).toBe(true);
    useAppStore.getState().setIsLoading(false);
    expect(useAppStore.getState().isLoading).toBe(false);
  });
});

describe("appStore - command palette + onboarding", () => {
  it("toggles command palette", () => {
    useAppStore.getState().setCommandOpen(true);
    expect(useAppStore.getState().commandOpen).toBe(true);
    useAppStore.getState().setCommandOpen(false);
    expect(useAppStore.getState().commandOpen).toBe(false);
  });

  it("toggles onboarding dialog", () => {
    useAppStore.getState().setOnboardingOpen(true);
    expect(useAppStore.getState().onboardingOpen).toBe(true);
  });
});

describe("appStore - browser tabs", () => {
  it("adds a new tab and activates it", () => {
    const initialCount = useAppStore.getState().browserTabs.length;
    useAppStore.getState().addBrowserTab("https://example.com", "Example");
    const tabs = useAppStore.getState().browserTabs;
    expect(tabs).toHaveLength(initialCount + 1);
    expect(tabs[tabs.length - 1].url).toBe("https://example.com");
    expect(useAppStore.getState().activeBrowserTabId).toBe(tabs[tabs.length - 1].id);
  });

  it("closing the active tab falls back to the last remaining tab", () => {
    useAppStore.getState().addBrowserTab("https://b.com", "B");
    useAppStore.getState().addBrowserTab("https://c.com", "C");
    const activeId = useAppStore.getState().activeBrowserTabId;
    useAppStore.getState().closeBrowserTab(activeId);
    const tabs = useAppStore.getState().browserTabs;
    expect(tabs.find((t) => t.id === activeId)).toBeUndefined();
    expect(useAppStore.getState().activeBrowserTabId).toBe(tabs[tabs.length - 1].id);
  });

  it("closing the last tab re-creates a single default tab", () => {
    // The store starts with exactly 1 tab. Close it.
    const onlyId = useAppStore.getState().browserTabs[0].id;
    useAppStore.getState().closeBrowserTab(onlyId);
    const tabs = useAppStore.getState().browserTabs;
    expect(tabs).toHaveLength(1);
    expect(tabs[0].id).not.toBe(onlyId); // new id
    expect(tabs[0].url).toBe("about:blank");
    expect(useAppStore.getState().activeBrowserTabId).toBe(tabs[0].id);
  });

  it("updates a browser tab's fields", () => {
    useAppStore.getState().addBrowserTab("https://a.com", "A");
    const id = useAppStore.getState().activeBrowserTabId;
    useAppStore.getState().updateBrowserTab(id, { title: "Renamed", url: "https://renamed.com" });
    const t = useAppStore.getState().browserTabs.find((x) => x.id === id);
    expect(t?.title).toBe("Renamed");
    expect(t?.url).toBe("https://renamed.com");
  });

  it("setActiveBrowserTab switches focus without closing", () => {
    useAppStore.getState().addBrowserTab("https://b.com", "B");
    const firstId = useAppStore.getState().browserTabs[0].id;
    useAppStore.getState().setActiveBrowserTab(firstId);
    expect(useAppStore.getState().activeBrowserTabId).toBe(firstId);
  });

  it("reorderBrowserTabs accepts a new tab order", () => {
    useAppStore.getState().addBrowserTab("https://b.com", "B");
    useAppStore.getState().addBrowserTab("https://c.com", "C");
    const tabs = useAppStore.getState().browserTabs;
    // Move the first tab to the end.
    const reordered = [tabs[1], tabs[2], tabs[0]];
    useAppStore.getState().reorderBrowserTabs(reordered);
    const urls = useAppStore.getState().browserTabs.map((t) => t.url);
    expect(urls).toEqual(["https://b.com", "https://c.com", "about:blank"]);
  });

  it("reorderBrowserTabs with the same order is a no-op", () => {
    useAppStore.getState().addBrowserTab("https://b.com", "B");
    const before = useAppStore.getState().browserTabs;
    useAppStore.getState().reorderBrowserTabs([...before]);
    const after = useAppStore.getState().browserTabs;
    expect(after.map((t) => t.id)).toEqual(before.map((t) => t.id));
  });

  it("reorderBrowserTabs rejects arrays of different length", () => {
    useAppStore.getState().addBrowserTab("https://b.com", "B");
    const before = useAppStore.getState().browserTabs.map((t) => t.id);
    useAppStore.getState().reorderBrowserTabs([]);
    const after = useAppStore.getState().browserTabs.map((t) => t.id);
    expect(after).toEqual(before);
  });

  it("reorderBrowserTabs rejects arrays with different tab ids", () => {
    useAppStore.getState().addBrowserTab("https://b.com", "B");
    const before = useAppStore.getState().browserTabs;
    const foreign = [
      { id: "ghost-1", url: "https://x.com", title: "X" },
      { id: "ghost-2", url: "https://y.com", title: "Y" },
    ];
    useAppStore.getState().reorderBrowserTabs(foreign);
    const after = useAppStore.getState().browserTabs;
    expect(after).toEqual(before); // unchanged
  });
});
