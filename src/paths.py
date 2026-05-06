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

# Ensure directories exist
CREDENTIALS_DIR.mkdir(exist_ok=True)
MEMORY_DIR.mkdir(exist_ok=True)


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