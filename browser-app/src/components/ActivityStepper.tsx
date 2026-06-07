import { motion, AnimatePresence } from "framer-motion";
import { Brain, Search, BookOpen, PenLine, Check } from "lucide-react";
import type { RobotState } from "./robot-svg";

const STEPS: { state: RobotState; label: string; icon: typeof Brain }[] = [
  { state: "thinking", label: "Thinking", icon: Brain },
  { state: "searching", label: "Searching", icon: Search },
  { state: "reading", label: "Reading", icon: BookOpen },
  { state: "writing", label: "Writing", icon: PenLine },
];

const ORDER: RobotState[] = ["thinking", "searching", "reading", "writing"];

interface ActivityStepperProps {
  state: RobotState;
  visible: boolean;
}

export const ActivityStepper = ({ state, visible }: ActivityStepperProps) => {
  const currentIdx = ORDER.indexOf(state as typeof ORDER[number]);
  const isError = state === "error";
  const isComplete = state === "idle" && !visible;

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          className="activity-stepper"
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -20 }}
          transition={{ duration: 0.3 }}
        >
          <div className="stepper-track">
            {STEPS.map((step, i) => {
              const Icon = step.icon;
              const isCurrent = step.state === state;
              const isDone = !isError && !isComplete && currentIdx > i;
              return (
                <div key={step.state} className="stepper-step">
                  <div className={`stepper-node ${isCurrent ? "current" : ""} ${isDone ? "done" : ""} ${isError ? "error" : ""}`}>
                    {isDone ? (
                      <Check size={12} />
                    ) : (
                      <motion.div
                        animate={isCurrent ? { scale: [1, 1.15, 1] } : { scale: 1 }}
                        transition={isCurrent ? { duration: 1.2, repeat: Infinity } : {}}
                      >
                        <Icon size={12} />
                      </motion.div>
                    )}
                    {isCurrent && (
                      <motion.div
                        className="stepper-ring"
                        animate={{ scale: [1, 1.8], opacity: [0.6, 0] }}
                        transition={{ duration: 1.2, repeat: Infinity }}
                      />
                    )}
                  </div>
                  <div className={`stepper-label ${isCurrent ? "current" : ""} ${isDone ? "done" : ""}`}>
                    {step.label}
                  </div>
                  {i < STEPS.length - 1 && (
                    <div className="stepper-line">
                      <motion.div
                        className="stepper-line-fill"
                        initial={{ scaleX: 0 }}
                        animate={{ scaleX: isDone ? 1 : isCurrent ? 0.5 : 0 }}
                        transition={{ duration: 0.4 }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
