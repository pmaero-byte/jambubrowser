import { motion } from "framer-motion";
import { Trees } from "lucide-react";

/**
 * Premium Welcome Screen
 * -----------------------
 * This is the first thing a user sees.
 * It reinforces the 'Jambubrowser' brand with the Trees icon.
 */

export const Welcome = () => {
  return (
    <motion.div 
      initial={{ opacity: 0, y: 20 }} 
      animate={{ opacity: 1, y: 0 }} 
      className="welcome"
    >
      <Trees 
        size={48} 
        color="#00ff64" 
        style={{ marginBottom: '16px' }}
      />
      <h2>Sovereign Intelligence Active.</h2>
      <p>Start a secure research mission with Jambubrowser.</p>
      
      <div className="welcome-hints">
        <div className="hint glass">Type /ground to visualize a site</div>
        <div className="hint glass">Toggle Full Power for deep swarms</div>
      </div>
    </motion.div>
  );
};
