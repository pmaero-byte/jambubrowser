import { create } from "zustand";

export type CanvasTab =
  | "chat"
  | "browser"
  | "logs"
  | "missions"
  | "history"
  | "memory"
  | "privacy"
  | "audit"
  | "vault"
  | "settings"
  | "team"
  | "extensions"
  | "agent";

export interface BrowserTab {
  id: string;
  url: string;
  title: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  sources?: string[];
  agentRun?: {
    total_steps?: number;
    duration_ms?: number;
    total_cost_usd?: number;
  };
}

interface AppState {
  sidebarOpen: boolean;
  inspectorOpen: boolean;
  activeTab: CanvasTab;
  activeModel: string;
  privacyMode: "standard" | "enhanced" | "maximum" | "local_only";
  messages: ChatMessage[];
  input: string;
  isLoading: boolean;
  browserTabs: BrowserTab[];
  activeBrowserTabId: string;
  commandOpen: boolean;
  onboardingOpen: boolean;

  toggleSidebar: () => void;
  toggleInspector: () => void;
  setActiveTab: (tab: CanvasTab) => void;
  setActiveModel: (model: string) => void;
  setPrivacyMode: (mode: AppState["privacyMode"]) => void;
  setInput: (input: string) => void;
  addMessage: (message: ChatMessage) => void;
  updateLastMessage: (patch: Partial<ChatMessage>) => void;
  setIsLoading: (loading: boolean) => void;
  setCommandOpen: (open: boolean) => void;
  setOnboardingOpen: (open: boolean) => void;

  addBrowserTab: (url?: string, title?: string) => void;
  closeBrowserTab: (id: string) => void;
  setActiveBrowserTab: (id: string) => void;
  updateBrowserTab: (id: string, patch: Partial<BrowserTab>) => void;
  reorderBrowserTabs: (newOrder: BrowserTab[]) => void;
  restoreSession: (tabs: BrowserTab[], activeTabId: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  sidebarOpen: true,
  inspectorOpen: true,
  activeTab: "chat",
  activeModel: "auto",
  privacyMode: "enhanced",
  messages: [],
  input: "",
  isLoading: false,
  browserTabs: [{ id: "1", url: "https://astrogenesis.net", title: "Astrogenesis" }],
  activeBrowserTabId: "1",
  commandOpen: false,
  onboardingOpen: false,

  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),
  setActiveTab: (tab) => set({ activeTab: tab }),
  setActiveModel: (model) => set({ activeModel: model }),
  setPrivacyMode: (mode) => set({ privacyMode: mode }),
  setInput: (input) => set({ input }),
  addMessage: (message) => set((s) => ({ messages: [...s.messages, message] })),
  updateLastMessage: (patch) =>
    set((s) => {
      const messages = [...s.messages];
      const last = messages[messages.length - 1];
      if (last) {
        messages[messages.length - 1] = { ...last, ...patch };
      }
      return { messages };
    }),
  setIsLoading: (isLoading) => set({ isLoading }),
  setCommandOpen: (commandOpen) => set({ commandOpen }),
  setOnboardingOpen: (onboardingOpen) => set({ onboardingOpen }),

  addBrowserTab: (url = "https://astrogenesis.net", title = "Astrogenesis") =>
    set((s) => {
      const id = crypto.randomUUID();
      return {
        browserTabs: [...s.browserTabs, { id, url, title }],
        activeBrowserTabId: id,
      };
    }),
  closeBrowserTab: (id) => set((s) => {
    const tabs = s.browserTabs.filter((t) => t.id !== id);
    if (tabs.length === 0) {
      const newId = crypto.randomUUID();
      return {
        browserTabs: [{ id: newId, url: "about:blank", title: "New Tab" }],
        activeBrowserTabId: newId,
      };
    }
    return {
      browserTabs: tabs,
      activeBrowserTabId:
        s.activeBrowserTabId === id
          ? tabs[tabs.length - 1].id
          : s.activeBrowserTabId,
    };
  }),
  setActiveBrowserTab: (id) => set({ activeBrowserTabId: id }),
  updateBrowserTab: (id, patch) =>
    set((s) => ({
      browserTabs: s.browserTabs.map((t) =>
        t.id === id ? { ...t, ...patch } : t
      ),
    })),
  reorderBrowserTabs: (newOrder) =>
    set((s) => {
      // Reject malformed reorders: must be a a permutation of the current tabs.
      // Framer Motion's Reorder can briefly fire onReorder with stale arrays
      // (e.g. dropping past the last item), so guards here keep the array
      // shape stable.
      if (newOrder.length !== s.browserTabs.length) return {};
      const currentIds = s.browserTabs.map((t) => t.id).sort();
      const newIds = newOrder.map((t) => t.id).sort();
      if (currentIds.some((id, i) => id !== newIds[i])) return {};
      return { browserTabs: newOrder };
    }),
  restoreSession: (tabs, activeTabId) =>
    set(() => {
      // Validate the incoming session before applying it. We trust the
      // localStorage payload to be a permutation of valid BrowserTab shapes
      // (id/url/title) but reject anything that looks empty, malformed, or
      // references a tab id that doesn't exist.
      if (!Array.isArray(tabs) || tabs.length === 0) return {};
      for (const t of tabs) {
        if (!t || typeof t.id !== "string" || typeof t.url !== "string" || typeof t.title !== "string") {
          return {};
        }
      }
      const activeTabId_ = tabs.some((t) => t.id === activeTabId)
        ? activeTabId
        : tabs[0].id;
      return { browserTabs: tabs, activeBrowserTabId: activeTabId_ };
    }),
}));
