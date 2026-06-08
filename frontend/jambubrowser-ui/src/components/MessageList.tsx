import { motion } from "framer-motion";

/**
 * Premium Message List
 * --------------------
 * Renders the flow of conversation.
 * Handles 'Deep Trust' source highlighting.
 */

interface Message {
  role: string;
  content: string;
  sources?: string[];
}

interface MessageListProps {
  messages: Message[];
  onSourceClick: (url: string) => void;
}

export const MessageList = ({ messages, onSourceClick }: MessageListProps) => {
  return (
    <div className="message-list">
      {messages.map((msg, i) => (
        <motion.div 
          key={i} 
          initial={{ opacity: 0, y: 10 }} 
          animate={{ opacity: 1, y: 0 }}
          className={`message ${msg.role}`}
        >
          <div className="avatar">
            {msg.role === "user" ? "U" : "J"}
          </div>
          <div className="content">
            <div className="answer">{msg.content}</div>
            {msg.sources && msg.sources.length > 0 && (
              <div className="source-row">
                {msg.sources.map((src, si) => (
                  <button 
                    key={si} 
                    className="source-chip" 
                    onClick={() => onSourceClick(src)}
                  >
                    [{si + 1}] {new URL(src).hostname}
                  </button>
                ))}
              </div>
            )}
          </div>
        </motion.div>
      ))}
    </div>
  );
};
