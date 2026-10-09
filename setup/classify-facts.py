#!/usr/bin/env python3
"""
classify-facts.py — label existing untyped facts, keep the durable ones.

Run:  python setup/classify-facts.py [--apply]

Without --apply it only reports. With --apply it writes the types, deletes
everything that isn't `stable`, and archives the deleted rows to
memory/fact_archive_<timestamp>.jsonl so nothing is truly lost.

Why: facts written before typing existed all default to `situational`, and the
backlog here was entirely 4-6 months old — moods, passed deadlines, sentence
fragments, and statements that are now simply false. Left in place they compete
with current information on relevance alone.

Facts are classified in batches to keep this to a few dozen API calls.
"""

import argparse
import json
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import get_client, load_app_config  # noqa: E402
from memory import Memory, FACT_TYPES, normalize_fact_type  # noqa: E402
from paths import MEMORY_DIR  # noqa: E402

BATCH_SIZE = 50

PROMPT = """You are labelling stored facts about one person, by how long each stays true.

  "stable"      — true for a long time: goals, health conditions, relationships,
                  people, preferences, recurring commitments, skills. Still worth
                  knowing in six months.
  "situational" — true for a while but will change: projects, arrangements, what
                  they were focused on at the time.
  "ephemeral"   — a passing state or a single moment: moods, energy, how one day
                  went, anything tied to a specific past date, sentence fragments
                  that don't stand alone, or statements that only made sense in
                  the moment.

Return ONLY a JSON array of objects, one per input, in the same order:
[{"i": 0, "type": "stable"}, {"i": 1, "type": "ephemeral"}, ...]

Facts:
%s"""


def classify(client, model, batch: list[tuple[int, str]]) -> dict[int, str]:
    listing = "\n".join(f'{n}. {fact}' for n, (_, fact) in enumerate(batch))
    resp = client.messages.create(
        model=model, max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT % listing}],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    out = {}
    for item in json.loads(text):
        idx = int(item["i"])
        if 0 <= idx < len(batch):
            out[batch[idx][0]] = normalize_fact_type(item.get("type"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write types, delete non-stable facts, archive them")
    args = ap.parse_args()

    config = load_app_config()
    client = get_client(config)
    model = config.get("anthropic_model", "claude-sonnet-4-6")

    mem = Memory(session_id="fact_classify")
    rows = mem.db.execute("SELECT id, fact FROM facts ORDER BY id").fetchall()
    print(f"{len(rows)} fact(s) to classify, in batches of {BATCH_SIZE}\n")

    labels: dict[int, str] = {}
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        try:
            labels.update(classify(client, model, batch))
        except Exception as e:
            print(f"  batch {start // BATCH_SIZE + 1}: FAILED ({e}) — left as-is")
            continue
        done = min(start + BATCH_SIZE, len(rows))
        print(f"  {done}/{len(rows)}")

    counts = {t: sum(1 for v in labels.values() if v == t) for t in FACT_TYPES}
    print(f"\nclassified {len(labels)}/{len(rows)}")
    for t in FACT_TYPES:
        print(f"  {t:<12} {counts[t]:>5}")
    unlabelled = len(rows) - len(labels)
    if unlabelled:
        print(f"  {'unlabelled':<12} {unlabelled:>5}  (kept, treated as situational)")

    # Persist the labels either way — classifying 1,497 facts is ~30 API calls,
    # and a dry run that throws them away makes --apply pay for it twice.
    label_path = MEMORY_DIR / "fact_labels.json"
    label_path.write_text(json.dumps(
        {str(i): {"type": t, "fact": dict(rows)[i]} for i, t in labels.items()},
        indent=2) + "\n")
    print(f"labels written to {label_path}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write types and prune.")
        mem.close()
        return 0

    for fact_id, ftype in labels.items():
        mem.set_fact_type(fact_id, ftype)

    doomed = [(i, f) for i, f in rows if labels.get(i) in ("situational", "ephemeral")]
    archive = MEMORY_DIR / f"fact_archive_{int(time.time())}.jsonl"
    with open(archive, "w") as fh:
        for fact_id, fact in doomed:
            fh.write(json.dumps({"id": fact_id, "fact": fact,
                                 "type": labels[fact_id]}) + "\n")
    for fact_id, _ in doomed:
        mem.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
    mem.db.commit()

    print(f"\narchived {len(doomed)} fact(s) -> {archive}")
    print("remaining:", mem.get_stats()["total_facts"], mem.fact_type_counts())
    mem.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
