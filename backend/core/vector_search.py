"""
Vector Search Module
====================
Provides vector search functionality with fallback when sqlite-vec is not available.
"""

import sqlite3
from typing import List, Tuple, Optional
from backend.core.database import get_db_cursor


# Global flag for sqlite-vec availability
_sqlite_vec_available: Optional[bool] = None


def is_sqlite_vec_available() -> bool:
    """Check if sqlite-vec is available."""
    global _sqlite_vec_available
    if _sqlite_vec_available is None:
        try:
            with get_db_cursor() as cursor:
                cursor.execute("SELECT 1 FROM vec_documents LIMIT 1")
                # If we can query, check if it's a vec0 table
                cursor.execute("SELECT sql FROM sqlite_master WHERE name='vec_documents'")
                result = cursor.fetchone()
                _sqlite_vec_available = result and 'vec0' in result[0]
        except Exception:
            _sqlite_vec_available = False
    return _sqlite_vec_available


def store_embedding(doc_id: int, embedding: bytes) -> bool:
    """Store an embedding vector for a document."""
    try:
        with get_db_cursor() as cursor:
            if is_sqlite_vec_available():
                cursor.execute(
                    "INSERT OR REPLACE INTO vec_documents (id, embedding) VALUES (?, ?)",
                    (doc_id, embedding)
                )
            else:
                # Fallback: store as blob
                cursor.execute(
                    "INSERT OR REPLACE INTO vec_documents (id, embedding) VALUES (?, ?)",
                    (doc_id, embedding)
                )
            return True
    except Exception as e:
        print(f"Error storing embedding: {e}")
        return False


def search_similar(embedding: bytes, k: int = 8) -> List[Tuple[str, str]]:
    """
    Search for similar documents using vector similarity.
    
    Returns list of (text, url) tuples.
    """
    try:
        with get_db_cursor() as cursor:
            if is_sqlite_vec_available():
                # Use sqlite-vec MATCH syntax
                cursor.execute(
                    """SELECT d.text, d.url FROM vec_documents v 
                       JOIN documents d ON v.id = d.id 
                       WHERE v.embedding MATCH ? AND k = ?""",
                    (embedding, k)
                )
            else:
                # Fallback: simple similarity search using Python
                # This is slower but works without sqlite-vec
                cursor.execute("SELECT id, embedding FROM vec_documents")
                rows = cursor.fetchall()
                
                if not rows:
                    return []
                
                # Calculate similarity (simple cosine similarity)
                import struct
                import numpy as np
                
                query_vec = np.frombuffer(embedding, dtype=np.float32)
                similarities = []
                
                for row_id, stored_embedding in rows:
                    if stored_embedding:
                        try:
                            stored_vec = np.frombuffer(stored_embedding, dtype=np.float32)
                            if len(stored_vec) == len(query_vec):
                                similarity = np.dot(query_vec, stored_vec) / (
                                    np.linalg.norm(query_vec) * np.linalg.norm(stored_vec)
                                )
                                similarities.append((row_id, similarity))
                        except Exception:
                            continue
                
                # Sort by similarity and get top k
                similarities.sort(key=lambda x: x[1], reverse=True)
                top_ids = [row_id for row_id, _ in similarities[:k]]
                
                if not top_ids:
                    return []
                
                # Fetch documents
                placeholders = ','.join(['?' for _ in top_ids])
                cursor.execute(
                    f"""SELECT d.text, d.url FROM documents d 
                        WHERE d.id IN ({placeholders})""",
                    top_ids
                )
            
            return cursor.fetchall()
    except Exception as e:
        print(f"Error in vector search: {e}")
        return []


def clear_embeddings() -> bool:
    """Clear all embeddings from the vector table."""
    try:
        with get_db_cursor() as cursor:
            cursor.execute("DELETE FROM vec_documents")
            return True
    except Exception as e:
        print(f"Error clearing embeddings: {e}")
        return False
