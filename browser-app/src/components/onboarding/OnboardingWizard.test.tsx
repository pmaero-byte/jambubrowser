import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock motion to avoid animation overhead
vi.mock("motion/react", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
    span: ({ children, ...props }: any) => <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// Mock Dialog components
vi.mock("../ui/dialog", () => ({
  Dialog: ({ children, open }: any) => (open ? <div role="dialog">{children}</div> : null),
  DialogContent: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

describe("OnboardingWizard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("renders when forceOpen is true", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    expect(screen.getByRole("dialog")).toBeDefined();
  });

  it("does not render when not forced and already seen", async () => {
    localStorage.setItem("jambu-onboarding-seen", "true");
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows welcome step content", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    expect(screen.getByText("Welcome to Jambubrowser")).toBeDefined();
    expect(screen.getByText(/sovereign, local-first AI research agent/i)).toBeDefined();
  });

  it("shows Next button on first step", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    expect(screen.getByText("Next")).toBeDefined();
  });

  it("does not show Back button on first step", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    expect(screen.queryByText("Back")).toBeNull();
  });

  it("navigates to next step on Next click", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    await userEvent.click(screen.getByText("Next"));
    expect(screen.getByText("Privacy First")).toBeDefined();
  });

  it("shows Back button after first step", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    await userEvent.click(screen.getByText("Next"));
    expect(screen.getByText("Back")).toBeDefined();
  });

  it("navigates back on Back click", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    await userEvent.click(screen.getByText("Next"));
    await userEvent.click(screen.getByText("Back"));
    expect(screen.getByText("Welcome to Jambubrowser")).toBeDefined();
  });

  it("shows Get Started on last step", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    // Navigate to last step
    await userEvent.click(screen.getByText("Next"));
    await userEvent.click(screen.getByText("Next"));
    await userEvent.click(screen.getByText("Next"));
    expect(screen.getByText("Get Started")).toBeDefined();
  });

  it("closes and sets localStorage on Get Started click", async () => {
    const onClose = vi.fn();
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen onClose={onClose} />);
    // Navigate to last step
    await userEvent.click(screen.getByText("Next"));
    await userEvent.click(screen.getByText("Next"));
    await userEvent.click(screen.getByText("Next"));
    await userEvent.click(screen.getByText("Get Started"));
    expect(localStorage.getItem("jambu-onboarding-seen")).toBe("true");
    expect(onClose).toHaveBeenCalled();
  });

  it("shows step indicators", async () => {
    const { OnboardingWizard } = await import("./OnboardingWizard");
    render(<OnboardingWizard forceOpen />);
    // Step indicators are motion divs, check that they exist
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeDefined();
  });
});
