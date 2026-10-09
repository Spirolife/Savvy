"""
Local memory system for the private secretary.
All data stays on your machine in a SQLite database.
Embeddings are generated via Ollama's local embedding model.
"""

import sqlite3
import json
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
from paths import DB_PATH, CONFIG_PATH

# How to restart Ollama when it is running but broken. Homebrew installs it as a
# systemd user unit, which the Savvy services can restart without brew on PATH.
DEFAULT_OLLAMA_RESTART = ["systemctl", "--user", "restart", "sh.brew.ollama.service"]
OLLAMA_RESTART_COOLDOWN = 600   # seconds; a real outage must not become a restart loop
_last_ollama_restart = 0.0


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
            timestamp REAL NOT NULL,
            fact_type TEXT NOT NULL DEFAULT 'situational'
        )
    """)
    # Databases created before typing existed get the column added in place.
    cols = {r[1] for r in db.execute("PRAGMA table_info(facts)")}
    if "fact_type" not in cols:
        db.execute("ALTER TABLE facts ADD COLUMN fact_type TEXT NOT NULL DEFAULT 'situational'")
    if "last_checked" not in cols:
        db.execute("ALTER TABLE facts ADD COLUMN last_checked REAL")
    db.commit()
    return db


# ---------------------------------------------------------------------------
# Fact typing and decay
# ---------------------------------------------------------------------------
# Not everything worth remembering stays true for the same length of time. A
# health condition or a year-long goal is still relevant in six months; "user
# was exhausted" is not, and letting it compete on relevance alone means stale
# mood data gets used to assess someone months later.
#
# Half-life is in days. Retrieval multiplies cosine similarity by the decay
# factor, so an old ephemeral fact has to be overwhelmingly relevant to beat
# something recent, while a stable fact never fades.
FACT_TYPES = ("stable", "situational", "ephemeral")
DEFAULT_FACT_TYPE = "situational"

_HALF_LIFE_DAYS = {
    "stable": None,        # never decays
    "situational": 75.0,
    "ephemeral": 10.0,
}
# How much age can cost a fact when ranking, in cosine-similarity units.
# nomic-embed-text similarities for one query span only ~0.3-0.8, so the old
# rule (similarity x decay) let a 20-day-old situational fact at x0.83 lose to
# barely-related stable facts that never decay — the low-FODMAP diet trial
# vanished from a restaurant question. Age is now a bounded penalty: it breaks
# ties and sinks long-stale facts, but can't override clear relevance.
MAX_AGE_PENALTY = 0.10

# "Current situation": recent short-lived facts injected every turn regardless
# of similarity. Prioritizing needs them ("laundry last done Sep 27", "trip on
# Saturday") and no wording of "what should I do Thursday" is similar to them.
SITUATION_WINDOW_DAYS = {"situational": 45.0, "ephemeral": 7.0}
SITUATION_LIMIT = 12

# Past this age an ephemeral fact is dropped outright rather than merely faded.
_HARD_EXPIRY_DAYS = {
    "stable": None,
    "situational": None,
    "ephemeral": 30.0,
}


# Situational facts this old get a weekly "still true?" question (scheduler
# fact_check). Ephemeral ones expire on their own and stable ones don't drift,
# so neither is asked about. A fact asked about is not asked again for this long.
FACT_CHECK_AFTER_DAYS = 30.0


def fact_lifetime(fact_type: str) -> str:
    """How long a fact of this type stays in play, from the settings above."""
    fact_type = normalize_fact_type(fact_type)
    expiry = _HARD_EXPIRY_DAYS.get(fact_type)
    half_life = _HALF_LIFE_DAYS.get(fact_type)
    if expiry:
        return f"deleted after {expiry:g} days"
    if half_life:
        return f"fades over time (half-life {half_life:g} days)"
    return "permanent"


def age_label(age_days: float) -> str:
    """'today', '3 days ago', '2 weeks ago', '7 months ago'."""
    d = int(age_days)
    if d < 1:
        return "today"
    if d < 14:
        return f"{d} day{'s' if d != 1 else ''} ago"
    if d < 60:
        return f"{d // 7} weeks ago"
    return f"{d // 30} months ago"


def _annotate(fact: str, fact_type: str, age_days: float) -> str:
    # Stable facts don't go stale, so their age is noise. For the rest, the age
    # is what lets the model see that "can't do a pull-up" (7 months ago) is
    # superseded by "can do 2" (9 days ago) when both are retrieved.
    return fact if fact_type == "stable" else f"{fact} (noted {age_label(age_days)})"


def normalize_fact_type(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in FACT_TYPES else DEFAULT_FACT_TYPE


def decay_factor(fact_type: str, age_days: float) -> float:
    """Relevance multiplier for a fact of this type at this age, in [0, 1]."""
    half_life = _HALF_LIFE_DAYS.get(fact_type)
    if half_life is None:
        return 1.0
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life)


def is_expired(fact_type: str, age_days: float) -> bool:
    limit = _HARD_EXPIRY_DAYS.get(fact_type)
    return limit is not None and age_days > limit


def _ollama_restart_command() -> list[str] | None:
    """Config `ollama_restart_command` (a list; null/[] disables self-healing)."""
    try:
        cmd = json.loads(CONFIG_PATH.read_text()).get("ollama_restart_command", DEFAULT_OLLAMA_RESTART)
    except (OSError, json.JSONDecodeError):
        cmd = DEFAULT_OLLAMA_RESTART
    return list(cmd) if cmd else None


def _restart_ollama() -> bool:
    """Restart a running-but-broken Ollama and wait until it answers again.

    The case this exists for: `brew upgrade` replaces Ollama's files while the
    old server keeps running. /api/version still answers, but every embed call
    500s ("llama-server binary not found"), and retrieval silently degrades to
    recency-only until someone restarts it by hand. Rate-limited per process.
    """
    global _last_ollama_restart
    cmd = _ollama_restart_command()
    if not cmd or time.time() - _last_ollama_restart < OLLAMA_RESTART_COOLDOWN:
        return False
    _last_ollama_restart = time.time()
    print(f"[memory] Ollama is up but embedding is failing — restarting it: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, timeout=60, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[memory] Ollama restart failed: {e}")
        return False
    for _ in range(30):
        try:
            if httpx.get(f"{OLLAMA_BASE}/api/version", timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    print("[memory] Ollama didn't come back within 30s after the restart")
    return False


def _embed(text: str, _retried: bool = False) -> list[float]:
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
    except httpx.HTTPStatusError as e:
        # A 5xx means the server is running but can't embed — the stale-process
        # state after an upgrade. Connection errors (Ollama simply off) are left
        # alone: recency-only memory is a supported mode.
        if e.response.status_code >= 500 and not _retried and _restart_ollama():
            return _embed(text, _retried=True)
        print(f"[memory] Embedding failed: {e.response.status_code} {e.response.text[:200]}")
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

    def store_fact(self, fact: str, fact_type: str = DEFAULT_FACT_TYPE,
                   source_id: Optional[int] = None) -> bool:
        """Store an extracted fact. Returns False if it was a duplicate.

        Near-identical facts used to stack up without limit — one deadline had
        accumulated 38 separate entries, which then dominated retrieval purely
        by weight of numbers. A repeat now refreshes the existing row's
        timestamp instead of adding another copy, so repetition keeps a fact
        alive rather than multiplying it.
        """
        fact = " ".join((fact or "").split())
        if not fact:
            return False
        fact_type = normalize_fact_type(fact_type)

        existing = self.db.execute(
            "SELECT id FROM facts WHERE lower(fact) = lower(?) LIMIT 1", (fact,)
        ).fetchone()
        if existing:
            self.db.execute(
                "UPDATE facts SET timestamp = ?, fact_type = ? WHERE id = ?",
                (time.time(), fact_type, existing[0]),
            )
            self.db.commit()
            return False

        embedding = _embed(fact)
        embedding_json = json.dumps(embedding) if embedding else None

        if embedding:
            # Catch rewordings of something already known, not just exact repeats.
            for row_id, emb_json, existing_type in self.db.execute(
                "SELECT id, embedding, fact_type FROM facts WHERE embedding IS NOT NULL"
            ).fetchall():
                try:
                    if _cosine_similarity(embedding, json.loads(emb_json)) >= 0.97:
                        self.db.execute(
                            "UPDATE facts SET timestamp = ?, fact_type = ? WHERE id = ?",
                            (time.time(), fact_type, row_id),
                        )
                        self.db.commit()
                        return False
                except (json.JSONDecodeError, TypeError):
                    continue

        self.db.execute(
            """INSERT INTO facts (fact, embedding, source_conversation_id, timestamp, fact_type)
               VALUES (?, ?, ?, ?, ?)""",
            (fact, embedding_json, source_id, time.time(), fact_type),
        )
        self.db.commit()
        return True

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

    def retrieve_since(self, seconds_ago: float, session_id: Optional[str] = None) -> list[dict]:
        """Get all messages from the last `seconds_ago` seconds, chronological order.
        Optionally scoped to a single session_id (e.g. "signal_bot")."""
        cutoff = time.time() - seconds_ago
        if session_id:
            rows = self.db.execute(
                """SELECT role, content, timestamp FROM conversations
                   WHERE timestamp >= ? AND session_id = ?
                   ORDER BY timestamp ASC""",
                (cutoff, session_id),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT role, content, timestamp FROM conversations
                   WHERE timestamp >= ?
                   ORDER BY timestamp ASC""",
                (cutoff,),
            ).fetchall()
        return [{"role": r, "content": c, "timestamp": t} for r, c, t in rows]

    def retrieve_recent(self, n: int = 10) -> list[dict]:
        """Get the N most recent messages."""
        rows = self.db.execute(
            """SELECT role, content, timestamp FROM conversations
               ORDER BY timestamp DESC LIMIT ?""",
            (n,),
        ).fetchall()
        # Reverse so they're in chronological order
        return [{"role": r, "content": c, "timestamp": t} for r, c, t in reversed(rows)]

    def retrieve_facts(self, query: str, top_k: int = 5, annotate: bool = False) -> list[str]:
        """Find relevant stored facts. `annotate` appends the age of non-stable ones."""
        query_emb = _embed(query)
        if not query_emb:
            rows = self.db.execute(
                "SELECT fact FROM facts ORDER BY timestamp DESC LIMIT ?", (top_k,)
            ).fetchall()
            return [r[0] for r in rows]

        rows = self.db.execute(
            "SELECT fact, embedding, fact_type, timestamp FROM facts WHERE embedding IS NOT NULL"
        ).fetchall()

        now = time.time()
        scored = []
        for fact, emb_json, fact_type, ts in rows:
            fact_type = normalize_fact_type(fact_type)
            age_days = max(0.0, (now - (ts or now)) / 86400.0)
            if is_expired(fact_type, age_days):
                continue
            try:
                emb = json.loads(emb_json)
            except (json.JSONDecodeError, TypeError):
                continue
            similarity = _cosine_similarity(query_emb, emb)
            if similarity <= 0:
                continue
            penalty = MAX_AGE_PENALTY * (1.0 - decay_factor(fact_type, age_days))
            scored.append((similarity - penalty, _annotate(fact, fact_type, age_days) if annotate else fact))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [fact for score, fact in scored[:top_k]]

    def current_situation(self, limit: int = SITUATION_LIMIT, annotate: bool = False) -> list[str]:
        """The newest short-lived facts — what's going on right now — newest first.

        Situational facts from the last 45 days and ephemeral ones from the last
        week. Stable facts are excluded: they're found by relevance instead.
        """
        now = time.time()
        rows = self.db.execute(
            "SELECT fact, fact_type, timestamp FROM facts "
            "WHERE fact_type IN ('situational', 'ephemeral') ORDER BY timestamp DESC"
        ).fetchall()
        out = []
        for fact, fact_type, ts in rows:
            fact_type = normalize_fact_type(fact_type)
            age_days = max(0.0, (now - (ts or now)) / 86400.0)
            window = SITUATION_WINDOW_DAYS.get(fact_type)
            if window is not None and age_days <= window:
                out.append(_annotate(fact, fact_type, age_days) if annotate else fact)
            if len(out) >= limit:
                break
        return out

    def facts_to_check(self, limit: int = 3) -> list[dict]:
        """Situational facts old enough to confirm, oldest first.

        Skips any asked about in the last FACT_CHECK_AFTER_DAYS, so an unanswered
        question isn't repeated every week. Confirming refreshes the timestamp
        (store_fact on the same text), which resets the clock.
        """
        now = time.time()
        cutoff = now - FACT_CHECK_AFTER_DAYS * 86400
        rows = self.db.execute(
            "SELECT id, fact, timestamp FROM facts WHERE fact_type = 'situational' "
            "AND timestamp <= ? AND (last_checked IS NULL OR last_checked <= ?) "
            "ORDER BY timestamp LIMIT ?", (cutoff, cutoff, limit),
        ).fetchall()
        return [{"id": r[0], "fact": r[1], "age": age_label((now - r[2]) / 86400.0)} for r in rows]

    def mark_checked(self, fact_ids: list[int]) -> None:
        self.db.executemany("UPDATE facts SET last_checked = ? WHERE id = ?",
                            [(time.time(), i) for i in fact_ids])
        self.db.commit()

    def find_facts(self, pattern: str) -> list[dict]:
        """Literal substring search over stored facts, case-insensitive.

        Deliberately not semantic: this exists so a specific wrong fact can be
        found and removed, and for that you want exact matches, not neighbours.
        """
        rows = self.db.execute(
            "SELECT id, fact, timestamp FROM facts "
            "WHERE fact LIKE ? COLLATE NOCASE ORDER BY timestamp",
            (f"%{pattern}%",),
        ).fetchall()
        return [{"id": r[0], "fact": r[1], "timestamp": r[2]} for r in rows]

    def forget_facts(self, pattern: str) -> list[str]:
        """Delete every fact containing `pattern`. Returns what was removed.

        Stored facts are re-injected into the prompt on every turn, so a stale
        one keeps resurfacing no matter how often the user corrects it in
        conversation. Before this existed the only remedy was /forget, which
        also destroys the entire conversation history — so in practice a wrong
        fact was permanent.
        """
        matches = self.find_facts(pattern)
        if not matches:
            return []
        self.db.execute(
            "DELETE FROM facts WHERE fact LIKE ? COLLATE NOCASE", (f"%{pattern}%",)
        )
        self.db.commit()
        return [m["fact"] for m in matches]

    def find_conversations(self, pattern: str) -> list[dict]:
        """Literal substring search over the conversation log."""
        rows = self.db.execute(
            "SELECT id, role, content, timestamp FROM conversations "
            "WHERE content LIKE ? COLLATE NOCASE ORDER BY timestamp",
            (f"%{pattern}%",),
        ).fetchall()
        return [{"id": r[0], "role": r[1], "content": r[2], "timestamp": r[3]} for r in rows]

    def forget_conversations(self, pattern: str) -> int:
        """Delete matching conversation entries. Rewrites history — callers
        should confirm with the user first."""
        n = len(self.find_conversations(pattern))
        if n:
            self.db.execute(
                "DELETE FROM conversations WHERE content LIKE ? COLLATE NOCASE",
                (f"%{pattern}%",),
            )
            self.db.commit()
        return n

    def purge_expired_facts(self) -> list[str]:
        """Delete facts past their hard expiry. Returns what was removed."""
        now = time.time()
        removed = []
        for row_id, fact, fact_type, ts in self.db.execute(
            "SELECT id, fact, fact_type, timestamp FROM facts"
        ).fetchall():
            age_days = max(0.0, (now - (ts or now)) / 86400.0)
            if is_expired(normalize_fact_type(fact_type), age_days):
                removed.append(fact)
                self.db.execute("DELETE FROM facts WHERE id = ?", (row_id,))
        if removed:
            self.db.commit()
        return removed

    def set_fact_type(self, fact_id: int, fact_type: str) -> None:
        self.db.execute("UPDATE facts SET fact_type = ? WHERE id = ?",
                        (normalize_fact_type(fact_type), fact_id))
        self.db.commit()

    def fact_type_counts(self) -> dict:
        rows = self.db.execute(
            "SELECT fact_type, COUNT(*) FROM facts GROUP BY fact_type"
        ).fetchall()
        return {normalize_fact_type(t): n for t, n in rows}

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