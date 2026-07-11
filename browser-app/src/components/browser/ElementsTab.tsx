import { useEffect, useState, useCallback, useRef } from "react";
import { ChevronRight, ChevronDown, FileCode, RefreshCw, AlertTriangle } from "lucide-react";
import { useDevtoolsStore } from "../../store/devtoolsStore";

interface DomNode {
  tag: string | null;
  id: string | null;
  class: string | null;
  text: string | null;
  children: DomNode[];
}

interface ElementsState {
  root: DomNode | null;
  loading: boolean;
  error: string | null;
  selected: string | null;
  expanded: Set<string>;
  rev: number;
}

const EXTRACT_SCRIPT = `(function(){
  function serialize(node, depth) {
    if (!node) return null;
    if (depth > 8) return null;
    const isElement = node.nodeType === 1;
    const isText = node.nodeType === 3;
    if (!isElement && !isText) return null;
    if (isText) {
      const t = (node.nodeValue || '').replace(/\\s+/g, ' ').trim();
      if (!t) return null;
      return { tag: null, id: null, class: null, text: t.slice(0, 200), children: [] };
    }
    const out = {
      tag: node.tagName ? node.tagName.toLowerCase() : null,
      id: node.id || null,
      class: (typeof node.className === 'string' && node.className) || null,
      text: null,
      children: [],
    };
    // Cap the per-node child count to keep the payload small; long
    // lists of identical siblings (think 5k <li> in a table) would
    // otherwise dominate the JSON.
    const kids = Array.from(node.childNodes).slice(0, 80);
    for (const c of kids) {
      const s = serialize(c, depth + 1);
      if (s) out.children.push(s);
    }
    return out;
  }
  return JSON.stringify(serialize(document.documentElement, 0));
})()`;

const pathKey = (path: number[]): string => path.join(".");

export function ElementsTab() {
  const { activeTab } = useDevtoolsStore();
  const [state, setState] = useState<ElementsState>({
    root: null, loading: false, error: null, selected: null, expanded: new Set(["0"]), rev: 0,
  });
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!activeTab) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState((s) => ({ ...s, loading: true, error: null, rev: s.rev + 1 }));
    try {
      const tauri = (window as unknown as Record<string, unknown>).__TAURI__ as {
        core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
      };
      const raw = await tauri.core.invoke("browser_evaluate", {
        tabId: activeTab,
        expression: EXTRACT_SCRIPT,
      });
      if (ctrl.signal.aborted) return;
      const text = String(raw);
      if (!text || text === "null") {
        setState((s) => ({ ...s, loading: false, error: "Could not serialize DOM" }));
        return;
      }
      const root = JSON.parse(text) as DomNode;
      setState((s) => ({ ...s, root, loading: false, error: null }));
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setState((s) => ({ ...s, loading: false, error: String(e) }));
    }
  }, [activeTab]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const toggle = (key: string) => {
    setState((s) => {
      const next = new Set(s.expanded);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return { ...s, expanded: next };
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border/30 bg-surface/50 px-2 py-1.5">
        <FileCode size={11} className="text-accent/70" />
        <span className="text-[11px] text-muted-foreground/70 font-medium">Elements</span>
        <div className="flex-1" />
        <button
          onClick={refresh}
          disabled={state.loading}
          className="rounded-md p-1 text-muted-foreground/50 transition-all duration-150 hover:bg-muted/30 hover:text-foreground disabled:opacity-40"
          title="Refresh DOM"
        >
          <RefreshCw size={11} className={state.loading ? "animate-spin" : ""} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-1 font-mono text-[11px]">
        {state.error && (
          <div className="m-2 flex items-start gap-1.5 rounded-md bg-red-500/10 border border-red-500/20 p-2 text-[10px] text-red-400/80">
            <AlertTriangle size={10} className="mt-0.5 shrink-0" />
            <span>{state.error}</span>
          </div>
        )}
        {!state.error && state.root && (
          <Tree
            node={state.root}
            depth={0}
            path={[]}
            expanded={state.expanded}
            selected={state.selected}
            onToggle={toggle}
            onSelect={(key) => setState((s) => ({ ...s, selected: key }))}
          />
        )}
        {!state.error && !state.root && !state.loading && (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground/40">
            <FileCode size={24} className="mb-2" />
            <span className="text-[10px]">No DOM to display.</span>
          </div>
        )}
      </div>
    </div>
  );
}

function Tree({
  node, depth, path, expanded, selected, onToggle, onSelect,
}: {
  node: DomNode;
  depth: number;
  path: number[];
  expanded: Set<string>;
  selected: string | null;
  onToggle: (key: string) => void;
  onSelect: (key: string) => void;
}) {
  const key = pathKey(path);
  const isExpanded = expanded.has(key);
  const isSelected = selected === key;
  const hasChildren = node.children.length > 0;

  if (node.text !== null) {
    return (
      <div
        className="flex items-start gap-1 truncate py-0.5 text-muted-foreground/60"
        style={{ paddingLeft: 4 + depth * 12 }}
        title={node.text}
      >
        <span className="shrink-0 text-[10px] text-muted-foreground/40">"</span>
        <span className="truncate">{node.text.length > 80 ? node.text.slice(0, 80) + "…" : node.text}</span>
        <span className="shrink-0 text-[10px] text-muted-foreground/40">"</span>
      </div>
    );
  }

  const tag = node.tag ?? "?";
  const idPart = node.id ? `#${node.id}` : "";
  const classPart = node.class ? `.${node.class.split(/\s+/).filter(Boolean).join(".")}` : "";

  return (
    <div>
      <div
        className={`flex cursor-pointer items-center gap-0.5 rounded py-0.5 text-foreground/85 transition-colors duration-100 hover:bg-muted/30 ${
          isSelected ? "bg-accent/15" : ""
        }`}
        style={{ paddingLeft: 4 + depth * 12 }}
        onClick={() => {
          onSelect(key);
          if (hasChildren) onToggle(key);
        }}
      >
        {hasChildren ? (
          isExpanded
            ? <ChevronDown size={10} className="shrink-0 text-muted-foreground/50" />
            : <ChevronRight size={10} className="shrink-0 text-muted-foreground/50" />
        ) : (
          <span className="inline-block w-[10px] shrink-0" />
        )}
        <span className="shrink-0 text-blue-400/80">&lt;{tag}</span>
        {idPart && <span className="shrink-0 text-amber-300/80">{idPart}</span>}
        {classPart && <span className="truncate text-emerald-300/80">{classPart}</span>}
        <span className="shrink-0 text-blue-400/80">&gt;</span>
      </div>
      {isExpanded && hasChildren && (
        <div>
          {node.children.map((child, i) => (
            <Tree
              key={i}
              node={child}
              depth={depth + 1}
              path={[...path, i]}
              expanded={expanded}
              selected={selected}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}
