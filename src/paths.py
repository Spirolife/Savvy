"""
Central path definitions for the secretary project.
All other modules import paths from here.

Project layout:
    ~/Projects/secretary/
    ├── credentials/      config.json, credentials.json, token_*.json
    ├── memory/           secretary_memory.db, scheduler_state.json
    ├── setup/            shell scripts
    ├── src/              all Python source files
    ├── requirements.txt
    └── .venv/
"""

import re
from difflib import SequenceMatcher
from pathlib import Path

# Project root is one level up from src/
PROJECT_ROOT = Path(__file__).parent.parent

# Credentials & config
CREDENTIALS_DIR = PROJECT_ROOT / "credentials"
CONFIG_PATH = CREDENTIALS_DIR / "config.json"
GOOGLE_CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"

# Memory & state
MEMORY_DIR = PROJECT_ROOT / "memory"
DB_PATH = MEMORY_DIR / "secretary_memory.db"
SCHEDULER_STATE_PATH = MEMORY_DIR / "scheduler_state.json"

# Notes & durable behavior rules
NOTES_DIR = PROJECT_ROOT / "notes"
SAVVY_RULES_PATH = NOTES_DIR / "savvy_rules.md"

# Ensure directories exist
CREDENTIALS_DIR.mkdir(exist_ok=True)
MEMORY_DIR.mkdir(exist_ok=True)
NOTES_DIR.mkdir(exist_ok=True)


def load_savvy_rules() -> str:
    """Read durable behavior rules from notes/savvy_rules.md, if present."""
    if SAVVY_RULES_PATH.exists():
        return SAVVY_RULES_PATH.read_text().strip()
    return ""


def _rule_key(text: str) -> str:
    """Normalized form used to compare two rules for sameness."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def existing_savvy_rules() -> list[str]:
    """Every rule currently in notes/savvy_rules.md, without the bullet."""
    try:
        lines = SAVVY_RULES_PATH.read_text().splitlines()
    except OSError:
        return []
    return [l.strip()[2:].strip() for l in lines if l.strip().startswith("- ")]


def _semantic_matches(rule: str, existing: list[str], threshold: float = 0.80) -> set[str]:
    """Existing rules that mean the same thing as `rule`, by local embedding.

    Lexical similarity alone misses restatements — "always search both accounts"
    and "email search covers both accounts by default" share few words but are the
    same instruction, and the user wrote four variants of exactly that. Ollama is
    already running locally for the memory index, so reuse it. Returns an empty
    set (i.e. fall back to lexical matching only) if Ollama is unreachable.

    The 0.80 default was measured against the real rules file, not guessed: the
    most similar pair of genuinely distinct rules scored 0.778, while restatements
    of an existing rule scored 0.826+. 0.80 sits in that gap.
    """
    if not existing:
        return set()
    try:
        from memory import _embed, _cosine_similarity      # lazy: memory imports paths
        target = _embed(rule)
        if not target:
            return set()
        matches = set()
        for old in existing:
            vec = _embed(old)
            if vec and _cosine_similarity(target, vec) >= threshold:
                matches.add(old)
        return matches
    except Exception:
        return set()


def add_savvy_rule(rule: str, similarity: float = 0.82) -> dict:
    """Append a rule to notes/savvy_rules.md unless one already says the same thing.

    This used to be a blind append. Because the calendar tools failed silently,
    the user kept restating the same instruction, and the file accumulated four
    near-identical "search both accounts simultaneously" rules — burning context
    on every single turn and making the real rules harder to follow.

    A near-duplicate now replaces the older wording rather than stacking on it.
    """
    rule = rule.strip()
    if not rule:
        return {"success": False, "error": "Empty rule."}

    key = _rule_key(rule)
    existing = existing_savvy_rules()
    semantic = _semantic_matches(rule, existing)
    for old in existing:
        old_key = _rule_key(old)
        if (old_key == key
                or SequenceMatcher(None, old_key, key).ratio() >= similarity
                or old in semantic):
            if len(rule) > len(old):        # keep the more specific phrasing
                text = SAVVY_RULES_PATH.read_text().replace(f"- {old}", f"- {rule}", 1)
                SAVVY_RULES_PATH.write_text(text)
                return {"success": True, "action": "replaced", "replaced": old,
                        "rule": rule}
            return {"success": True, "action": "already_known", "rule": old,
                    "note": "A rule saying this already exists; nothing was added."}

    prefix = "\n" if SAVVY_RULES_PATH.exists() and SAVVY_RULES_PATH.stat().st_size > 0 else ""
    with open(SAVVY_RULES_PATH, "a") as f:
        f.write(f"{prefix}- {rule}\n")
    return {"success": True, "action": "added", "rule": rule}


def google_token_path(label: str) -> Path:
    """Get the token file path for a Google account label."""
    return CREDENTIALS_DIR / f"token_{label}.json"


def get_google_account_labels() -> list[str]:
    """Find all authenticated Google accounts by looking for token_*.json files."""
    labels = []
    for f in CREDENTIALS_DIR.glob("token_*.json"):
        label = f.stem.replace("token_", "")
        labels.append(label)
    return sorted(labels)