import React, { useCallback, useEffect, useRef, useState } from "react";

interface ResizableSplitViewProps {
  /** Sidebar content (left) */
  sidebar: React.ReactNode;
  /** Main content (right) */
  children: React.ReactNode;
  /** Default sidebar width in pixels */
  defaultWidth?: number;
  /** Min width in pixels */
  minWidth?: number;
  /** Max width in pixels */
  maxWidth?: number;
  /** localStorage key for persisting width */
  storageKey?: string;
}

/**
 * Two-pane resizable split view with a draggable vertical handle.
 * Width is persisted in localStorage. Double-click the handle to reset.
 */
export const ResizableSplitView: React.FC<ResizableSplitViewProps> = ({
  sidebar,
  children,
  defaultWidth = 380,
  minWidth = 280,
  maxWidth = 720,
  storageKey = "jambu.splitview.width.v1",
}) => {
  const [width, setWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const v = parseInt(saved, 10);
        if (v >= minWidth && v <= maxWidth) return v;
      }
    } catch {}
    return defaultWidth;
  });
  const [dragging, setDragging] = useState(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  // Persist width
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, String(width));
    } catch {}
  }, [width, storageKey]);

  // Apply CSS variable
  useEffect(() => {
    document.documentElement.style.setProperty("--sidebar-width", `${width}px`);
  }, [width]);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setDragging(true);
    startX.current = e.clientX;
    startWidth.current = width;
  }, [width]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      const dx = e.clientX - startX.current;
      const newWidth = Math.max(minWidth, Math.min(maxWidth, startWidth.current + dx));
      setWidth(newWidth);
    };
    const onUp = () => setDragging(false);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [dragging, minWidth, maxWidth]);

  // Keyboard support: ←/→ to nudge
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 32 : 8;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      setWidth((w) => Math.max(minWidth, w - step));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      setWidth((w) => Math.min(maxWidth, w + step));
    } else if (e.key === "Home") {
      e.preventDefault();
      setWidth(defaultWidth);
    }
  }, [minWidth, maxWidth, defaultWidth]);

  const onDoubleClick = useCallback(() => {
    setWidth(defaultWidth);
  }, [defaultWidth]);

  return (
    <div className={`resizable-split ${dragging ? "dragging" : ""}`}>
      <div
        className="resizable-pane sidebar"
        style={{ width: `${width}px`, flex: `0 0 ${width}px` }}
      >
        {sidebar}
      </div>
      <div
        className="resize-handle"
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        tabIndex={0}
        onMouseDown={onMouseDown}
        onDoubleClick={onDoubleClick}
        onKeyDown={onKeyDown}
        title="Drag to resize · Double-click to reset · ←/→ to nudge"
      >
        <div className="resize-grip" />
      </div>
      <div className="resizable-pane main">{children}</div>
    </div>
  );
};
