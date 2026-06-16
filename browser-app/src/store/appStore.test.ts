import { describe, it, expect } from "vitest";
import { useAppStore } from "./appStore";

describe("appStore", () => {
  it("adds a chat message", () => {
    useAppStore.setState({ messages: [] });
    useAppStore.getState().addMessage({ id: "1", role: "user", content: "hi" });
    expect(useAppStore.getState().messages).toHaveLength(1);
    expect(useAppStore.getState().messages[0].content).toBe("hi");
  });

  it("updates the last message", () => {
    useAppStore.setState({
      messages: [{ id: "1", role: "assistant", content: "" }],
    });
    useAppStore.getState().updateLastMessage({ content: "hello" });
    expect(useAppStore.getState().messages[0].content).toBe("hello");
  });
});
