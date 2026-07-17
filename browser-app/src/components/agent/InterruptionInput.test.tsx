import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InterruptionInput } from "./InterruptionInput";

// Mock motion to avoid animation overhead in tests.
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

describe("InterruptionInput", () => {
  const onSubmit = vi.fn();
  const onCancel = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when not visible", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    const { container } = render(
      <InterruptionInput visible={false} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    expect(container.textContent).toBe("");
  });

  it("renders interruption bar when visible", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    expect(screen.getByPlaceholderText(/redirect the agent/i)).toBeDefined();
    expect(screen.getByText("Redirect")).toBeDefined();
  });

  it("displays taskId when provided", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-42" onSubmit={onSubmit} onCancel={onCancel} />
    );
    expect(screen.getByText("task-42")).toBeDefined();
  });

  it("shows generic label when taskId is absent", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} onSubmit={onSubmit} onCancel={onCancel} />
    );
    expect(screen.getByText(/interrupting task/i)).toBeDefined();
  });

  it("submit button is disabled when input is empty", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    const btn = screen.getByRole("button", { name: /redirect/i });
    expect(btn).toBeDisabled();
  });

  it("submit button is enabled when input has text", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    const input = screen.getByPlaceholderText(/redirect the agent/i);
    await userEvent.type(input, "skip summary");
    const btn = screen.getByRole("button", { name: /redirect/i });
    expect(btn).toBeEnabled();
  });

  it("calls onSubmit with trimmed text on submit", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    const input = screen.getByPlaceholderText(/redirect the agent/i);
    const btn = screen.getByRole("button", { name: /redirect/i });
    await userEvent.type(input, "focus on security ");
    await userEvent.click(btn);
    expect(onSubmit).toHaveBeenCalledWith("focus on security");
  });

  it("does not call onSubmit with empty or whitespace text", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    const btn = screen.getByRole("button", { name: /redirect/i });
    await userEvent.click(btn);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("calls onCancel when cancel button is clicked", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    const cancelBtn = screen.getByTitle("Cancel");
    await userEvent.click(cancelBtn);
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("clears text after successful submit", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    const input = screen.getByPlaceholderText(/redirect the agent/i) as HTMLInputElement;
    await userEvent.type(input, "new direction");
    await userEvent.click(screen.getByRole("button", { name: /redirect/i }));
    expect(input.value).toBe("");
  });

  it("submits on Enter key", async () => {
    const { InterruptionInput } = await import("./InterruptionInput");
    render(
      <InterruptionInput visible={true} taskId="task-1" onSubmit={onSubmit} onCancel={onCancel} />
    );
    const input = screen.getByPlaceholderText(/redirect the agent/i);
    await userEvent.type(input, "change approach{enter}");
    expect(onSubmit).toHaveBeenCalledWith("change approach");
  });
});