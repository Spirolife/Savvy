"""
Local memory system for the private secretary.
All data stays on your machine in a SQLite database.
Embeddings are generated via Ollama's local embedding model.
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import Optional

import httpx

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
from paths import DB_PATH


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT,
            timestamp REAL NOT NULL,
            session_id TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            embedding TEXT,
            source_conversation_id INTEGER,
            timestamp REAL NOT NULL
        )
    """)
    db.commit()
    return db


def _embed(text: str) -> list[float]:
    """Get embedding from local Ollama embedding model."""
    if not text or not text.strip():
        return []
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": EMBED_MODEL, "input": text[:2000]},  # truncate long inputs
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if embeddings and len(embeddings) > 0:
            return embeddings[0]
        return []
    except Exception as e:
        print(f"[memory] Embedding failed: {e}")
        return []

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class Memory:
    def __init__(self, session_id: Optional[str] = None):
        self.db = _get_db()
        self.session_id = session_id or f"session_{int(time.time())}"

    def store(self, role: str, content: str) -> int:
        """Store a message and its embedding locally."""
        embedding = _embed(content)
        embedding_json = json.dumps(embedding) if embedding else None

        cursor = self.db.execute(
            """INSERT INTO conversations (role, content, embedding, timestamp, session_id)
               VALUES (?, ?, ?, ?, ?)""",
            (role, content, embedding_json, time.time(), self.session_id),
        )
        self.db.commit()
        return cursor.lastrowid

    def store_fact(self, fact: str, source_id: Optional[int] = None):
        """Store an extracted fact for structured memory."""
        embedding = _embed(fact)
        embedding_json = json.dumps(embedding) if embedding else None

        self.db.execute(
            """INSERT INTO facts (fact, embedding, source_conversation_id, timestamp)
               VALUES (?, ?, ?, ?)""",
            (fact, embedding_json, source_id, time.time()),
        )
        self.db.commit()

    def retrieve_relevant(self, query: str, top_k: int = 5) -> list[dict]:
        """Find the most relevant past messages using local embedding similarity."""
        query_emb = _embed(query)
        if not query_emb:
            # Fallback: return most recent messages
            rows = self.db.execute(
                """SELECT role, content, timestamp FROM conversations
                   ORDER BY timestamp DESC LIMIT ?""",
                (top_k,),
            ).fetchall()
            return [{"role": r, "content": c, "timestamp": t} for r, c, t in rows]

        rows = self.db.execute(
            "SELECT role, content, embedding, timestamp FROM conversations WHERE embedding IS NOT NULL"
        ).fetchall()

        scored = []
        for role, content, emb_json, ts in rows:
            emb = json.loads(emb_json)
            score = _cosine_similarity(query_emb, emb)
            scored.append({"role": role, "content": content, "timestamp": ts, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def retrieve_recent(self, n: int = 10) -> list[dict]:
        """Get the N most recent messages."""
        rows = self.db.execute(
            """SELECT role, content, timestamp FROM conversations
               ORDER BY timestamp DESC LIMIT ?""",
            (n,),
        ).fetchall()
        # Reverse so they're in chronological order
        return [{"role": r, "content": c, "timestamp": t} for r, c, t in reversed(rows)]

    def retrieve_facts(self, query: str, top_k: int = 5) -> list[str]:
        """Find relevant stored facts."""
        query_emb = _embed(query)
        if not query_emb:
            rows = self.db.execute(
                "SELECT fact FROM facts ORDER BY timestamp DESC LIMIT ?", (top_k,)
            ).fetchall()
            return [r[0] for r in rows]

        rows = self.db.execute(
            "SELECT fact, embedding FROM facts WHERE embedding IS NOT NULL"
        ).fetchall()

        scored = []
        for fact, emb_json in rows:
            emb = json.loads(emb_json)
            score = _cosine_similarity(query_emb, emb)
            scored.append((score, fact))

        scored.sort(reverse=True)
        return [fact for _, fact in scored[:top_k]]

    def get_stats(self) -> dict:
        """Get memory statistics."""
        msg_count = self.db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        fact_count = self.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        session_count = self.db.execute(
            "SELECT COUNT(DISTINCT session_id) FROM conversations"
        ).fetchone()[0]
        return {
            "total_messages": msg_count,
            "total_facts": fact_count,
            "total_sessions": session_count,
        }

    def close(self):
        self.db.close()