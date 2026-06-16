import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
} from "../ui/dialog";
import { ChevronRight, ChevronLeft, Shield, Bot, Brain, Lock, X } from "lucide-react";

interface OnboardingWizardProps {
  forceOpen?: boolean;
  onClose?: () => void;
}

const steps = [
  {
    title: "Welcome to Jambubrowser",
    icon: Bot,
    body: "Your sovereign, local-first AI research agent. Ask questions, browse sources, and build a personal knowledge vault — all on your machine.",
  },
  {
    title: "Privacy First",
    icon: Shield,
    body: "Choose from four privacy modes. In Local Only mode, no data leaves your device. PII is automatically redacted and tracking domains are blocked.",
  },
  {
    title: "Agentic Research",
    icon: Brain,
    body: "The agent plans, executes tools, verifies results, and replans when needed. Watch the live timeline as it searches, reads, and writes.",
  },
  {
    title: "Vault & Memory",
    icon: Lock,
    body: "Store credentials in an AES-256 vault and build long-term memory. The more you use Jambu, the better it understands your context.",
  },
];

export function OnboardingWizard({ forceOpen, onClose }: OnboardingWizardProps) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (forceOpen) {
      setOpen(true);
      return;
    }
    const seen = localStorage.getItem("jambu-onboarding-seen");
    if (!seen) {
      setOpen(true);
    }
  }, [forceOpen]);

  const close = () => {
    localStorage.setItem("jambu-onboarding-seen", "true");
    setOpen(false);
    onClose?.();
  };

  const StepIcon = steps[step].icon;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) close(); }}>
      <DialogContent className="max-w-md p-0 overflow-hidden border-border">
        <div className="p-6">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <StepIcon size={20} />
            </div>
            <button onClick={close} className="rounded p-1 text-muted-foreground hover:bg-muted">
              <X size={16} />
            </button>
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, x: 16 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -16 }}
              transition={{ duration: 0.18 }}
            >
              <h2 className="text-xl font-semibold">{steps[step].title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {steps[step].body}
              </p>
            </motion.div>
          </AnimatePresence>

          <div className="mt-6 flex items-center justify-between">
            <div className="flex gap-1">
              {steps.map((_, i) => (
                <div
                  key={i}
                  className={`h-1.5 w-4 rounded-full ${i === step ? "bg-primary" : "bg-muted"}`}
                />
              ))}
            </div>
            <div className="flex gap-2">
              {step > 0 && (
                <Button variant="outline" size="sm" onClick={() => setStep((s) => s - 1)}>
                  <ChevronLeft size={14} className="mr-1" /> Back
                </Button>
              )}
              {step < steps.length - 1 ? (
                <Button size="sm" onClick={() => setStep((s) => s + 1)}>
                  Next <ChevronRight size={14} className="ml-1" />
                </Button>
              ) : (
                <Button size="sm" onClick={close}>
                  Get Started
                </Button>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
