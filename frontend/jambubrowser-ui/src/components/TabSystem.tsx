import { motion, AnimatePresence } from "framer-motion";
import { Plus, X } from "lucide-react";

/**
 * Modern Tab Management System
 * ----------------------------
 * Handles multiple browsing contexts.
 */

interface Tab {
  id: string;
  url: string;
  title: string;
}

interface TabSystemProps {
  tabs: Tab[];
  activeTabId: string;
  onTabSelect: (id: string) => void;
  onTabClose: (id: string) => void;
  onAddTab: () => void;
}

export const TabSystem = ({ tabs, activeTabId, onTabSelect, onTabClose, onAddTab }: TabSystemProps) => {
  return (
    <div className="tab-system">
      <div className="tab-list">
        <AnimatePresence>
          {tabs.map(tab => (
            <motion.div 
              key={tab.id}
              initial={{ opacity: 0, w: 0 }}
              animate={{ opacity: 1, w: 'auto' }}
              exit={{ opacity: 0, w: 0 }}
              className={`tab-item ${tab.id === activeTabId ? 'active' : ''}`}
              onClick={() => onTabSelect(tab.id)}
            >
              <span className="tab-title">{tab.title || 'New Tab'}</span>
              <X 
                size={12} 
                className="close-icon" 
                onClick={(e) => { e.stopPropagation(); onTabClose(tab.id); }} 
              />
            </motion.div>
          ))}
        </AnimatePresence>
        <button className="add-tab-btn" onClick={onAddTab}>
          <Plus size={14} />
        </button>
      </div>
    </div>
  );
};
