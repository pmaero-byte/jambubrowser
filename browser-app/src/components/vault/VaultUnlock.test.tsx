import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VaultUnlock } from "./VaultUnlock";

// Mock localFetch so we control API responses.
const mockLocalFetch = vi.hoisted(() => vi.fn());
vi.mock("../../utils/api", () => ({
  localFetch: mockLocalFetch,
}));

// Mock motion to avoid animation overhead in tests.
vi.mock("motion/react", () => ({
  motion: {
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
      input: (props: any) => <input {...props} />,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

describe("VaultUnlock", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the vault unlock form", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    const { container } = render(<VaultUnlock />);
    expect(container.textContent).toContain("Credential Vault");
    expect(container.textContent).toContain("Master Password");
    expect(container.textContent).toContain("Unlock Vault");
  });

  it("disables submit button when password is empty", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    render(<VaultUnlock />);
    const btn = screen.getByRole("button", { name: /unlock vault/i });
    expect(btn).toBeDisabled();
  });

  it("enables submit button when password is entered", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    render(<VaultUnlock />);
    const input = screen.getByPlaceholderText("••••••••");
    const btn = screen.getByRole("button", { name: /unlock vault/i });
    expect(btn).toBeDisabled();
    await userEvent.type(input, "mysecret");
    expect(btn).toBeEnabled();
  });

  it("shows loading state during unlock", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    // Return a promise that never resolves so we can check loading state.
    const mockLocalFetch = vi.fn(() => new Promise(() => {}));
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockImplementation(mockLocalFetch);

    render(<VaultUnlock />);
    const input = screen.getByPlaceholderText("••••••••");
    const btn = screen.getByRole("button", { name: /unlock vault/i });
    await userEvent.type(input, "mysecret");
    await userEvent.click(btn);
    expect(screen.getByText("Unlocking…")).toBeDefined();
  });

  it("shows success message on successful unlock", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(JSON.stringify({ success: true }), { status: 200 })
    );

    render(<VaultUnlock />);
    const input = screen.getByPlaceholderText("••••••••");
    const btn = screen.getByRole("button", { name: /unlock vault/i });
    await userEvent.type(input, "mysecret");
    await userEvent.click(btn);
    expect(await screen.findByText("Unlocked")).toBeDefined();
    expect(screen.getByText("Vault unlocked.")).toBeDefined();
  });

  it("shows error message on failed unlock", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(JSON.stringify({ success: false, error: "Wrong password." }), { status: 200 })
    );

    render(<VaultUnlock />);
    const input = screen.getByPlaceholderText("••••••••");
    const btn = screen.getByRole("button", { name: /unlock vault/i });
    await userEvent.type(input, "wrongpass");
    await userEvent.click(btn);
    expect(await screen.findByText("Wrong password.")).toBeDefined();
  });

  it("shows network error when fetch throws", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockRejectedValue(new Error("Network error"));

    render(<VaultUnlock />);
    const input = screen.getByPlaceholderText("••••••••");
    const btn = screen.getByRole("button", { name: /unlock vault/i });
    await userEvent.type(input, "mysecret");
    await userEvent.click(btn);
    expect(await screen.findByText("Network error.")).toBeDefined();
  });

  it("clears error when user types again", async () => {
    const { VaultUnlock } = await import("./VaultUnlock");
    const apiModule = await import("../../utils/api");
    vi.spyOn(apiModule, "localFetch").mockResolvedValue(
      new Response(JSON.stringify({ success: false, error: "Wrong password." }), { status: 200 })
    );

    render(<VaultUnlock />);
    const input = screen.getByPlaceholderText("••••••••");
    const btn = screen.getByRole("button", { name: /unlock vault/i });
    await userEvent.type(input, "wrongpass");
    await userEvent.click(btn);
    expect(await screen.findByText("Wrong password.")).toBeDefined();

    // Typing again should clear the error
    await userEvent.type(input, "x");
    expect(screen.queryByText("Wrong password.")).toBeNull();
  });
});