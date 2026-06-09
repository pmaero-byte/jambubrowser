import { motion } from "framer-motion";
import { Mic, Paperclip, ArrowUpRight } from "lucide-react";

/**
 * Floating Command Bar (The Pill)
 * -------------------------------
 * This is where the user interacts with the agent.
 * It supports text input, voice, and file attachments.
 */

interface CommandBarProps {
  input: string;
  setInput: (val: string) => void;
  isLoading: boolean;
  handleSubmit: (e: any) => void;
  startListening: () => void;
  fileInputRef: any;
  handleImageSelect: (e: any) => void;
  domain: string;
  setDomain: (val: string) => void;
  llmProvider: string;
  setLlmProvider: (val: string) => void;
}

export const CommandBar = ({ 
  input, setInput, isLoading, handleSubmit, 
  startListening, fileInputRef, handleImageSelect, 
  domain, setDomain,
  llmProvider, setLlmProvider
}: CommandBarProps) => {
  return (
    <motion.div layout className="input-container">
      <div className="bar-row">
        <div className="domain-bar">
          {['general', 'academic', 'coding'].map(d => (
            <button 
              key={d} 
              className={domain === d ? 'active' : ''} 
              onClick={() => setDomain(d)}
            >
              {d.charAt(0).toUpperCase() + d.slice(1)}
            </button>
          ))}
        </div>
        <div className="provider-bar">
          {[
            { id: 'auto', label: 'Auto' },
            { id: 'ollama', label: 'Ollama' },
            { id: 'mlx', label: 'MLX' },
            { id: 'minimax', label: 'MiniMax' },
          ].map(p => (
            <button
              key={p.id}
              className={llmProvider === p.id ? 'active' : ''}
              onClick={() => setLlmProvider(p.id)}
              title={`Use ${p.label} LLM provider`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      
      <form className="input-area glass" onSubmit={handleSubmit}>
        <button type="button" onClick={startListening}>
          <Mic size={18}/>
        </button>
        <button type="button" onClick={() => fileInputRef.current?.click()}>
          <Paperclip size={18}/>
        </button>
        <input 
          ref={fileInputRef} 
          type="file" 
          style={{display:'none'}} 
          onChange={handleImageSelect} 
        />
        <input 
          value={input} 
          onChange={e => setInput(e.target.value)} 
          placeholder="Send a command or search query..." 
        />
        <button type="submit" disabled={isLoading} className="go-btn">
          <ArrowUpRight size={20}/>
        </button>
      </form>
    </motion.div>
  );
};
