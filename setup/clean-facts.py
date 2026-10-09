#!/usr/bin/env python3
"""
clean-facts.py — prune stored facts down to things about the user.

Run:  python setup/clean-facts.py              # classify, write the review file
      python setup/clean-facts.py --apply      # act on the (edited) review file

The fact extractor used to store anything concrete in an exchange, including
the assistant's own recaps of the calendar. Memory filled up with schedule
lines ("PT + yoga: 10:00–10:30", "meeting with Seth at 2:00 tomorrow"), to-dos,
and instructions to Savvy, many labelled `stable` so they never fade. The
calendar, tasks, reminders, meals and goals already hold the first two; the
third belongs in notes/savvy_rules.md.

The dry run sorts every fact into one of:
  keep             a fact about the user, type unchanged
  keep:<type>      keep, but relabel stable / situational / ephemeral
  drop             schedule detail, to-do, assistant recap, fragment, stale
  rule             an instruction to Savvy — moved to savvy_rules.md
  dup              near-identical to a newer kept fact (by local embedding)

and writes memory/fact_cleanup.txt, grouped by verdict. Edit any verdict (or a
rule's wording) by hand, then run --apply. Apply backs up the database first
and archives every removed row, so nothing is lost for good.
"""

import argparse
import json
import math
import shutil
import sys
import time
from operator import mul
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import get_client, load_app_config  # noqa: E402
from memory import Memory, DB_PATH, FACT_TYPES, normalize_fact_type, age_label  # noqa: E402
from paths import MEMORY_DIR, add_savvy_rule  # noqa: E402

REVIEW_PATH = MEMORY_DIR / "fact_cleanup.txt"
BATCH_SIZE = 40
# Cosine similarity above which two kept facts count as the same fact.
DUP_THRESHOLD = 0.95
VERDICT_ORDER = ["drop", "rule", "dup", "keep"]

PROMPT = """You are cleaning up a personal assistant's long-term memory of its \
user. Memory should hold who the user is — not their schedule, which lives in \
the calendar, tasks, reminders, meal planner and goal tracker.

For each stored fact choose a verdict:
  "keep" — about the user: background, work or study, people and relationships,
           preferences and dislikes, health (conditions, symptoms, treatments),
           habits and routines in general terms, how they tend to behave.
  "drop" — schedule details (events, appointments, times, specific days),
           to-dos, errands, deadlines, meal plans, goals restated, things the
           user asked for, assistant recaps, sentence fragments that don't stand
           alone, facts about other things than the user (a course policy, how
           long some task takes), or anything clearly stale for its age.
  "rule" — really an instruction to the assistant about how to behave
           ("check every calendar event", "only schedule travel when timing is
           tight"). Give it as a short imperative in "rule".

For "keep", also give the right type:
  "stable"      — still true in six months (health conditions, relationships,
                  preferences, skills, background)
  "situational" — true for now but will change (current projects, arrangements,
                  a diet being tried)
  "ephemeral"   — a passing state (mood, energy, how a day went)

Give a reason of a few words for every verdict.

Return ONLY a JSON array, one object per fact, same order:
[{"i": 0, "verdict": "keep", "type": "stable", "reason": "..."},
 {"i": 1, "verdict": "drop", "reason": "..."},
 {"i": 2, "verdict": "rule", "rule": "...", "reason": "..."}]

Facts (current type and age in brackets):
%s"""


def classify(client, model, batch: list[dict]) -> dict[int, dict]:
    listing = "\n".join(f"{n}. [{f['type']}, {f['age']}] {f['fact']}" for n, f in enumerate(batch))
    resp = client.messages.create(
        model=model, max_tokens=16000,
        messages=[{"role": "user", "content": PROMPT % listing}],
    )
    # Thinking models can lead with a thinking block; take the text one.
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    out = {}
    for item in json.loads(text):
        idx = int(item["i"])
        if not 0 <= idx < len(batch):
            continue
        verdict = str(item.get("verdict", "keep")).lower()
        if verdict not in ("keep", "drop", "rule"):
            verdict = "keep"
        out[batch[idx]["id"]] = {
            "verdict": verdict,
            "type": normalize_fact_type(item.get("type")) if verdict == "keep" else None,
            "rule": (item.get("rule") or "").strip(),
            "reason": " ".join(str(item.get("reason", "")).split()),
        }
    return out


def _unit(vec: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else None


def mark_duplicates(facts: list[dict], labels: dict[int, dict]) -> int:
    """Among kept facts, mark older near-copies of a newer one as dup."""
    kept = [f for f in facts if labels.get(f["id"], {}).get("verdict") == "keep" and f["vec"]]
    kept.sort(key=lambda f: f["ts"], reverse=True)   # the newest wording wins
    reps: list[dict] = []
    n = 0
    for f in kept:
        match = next((r for r in reps if sum(map(mul, f["vec"], r["vec"])) >= DUP_THRESHOLD), None)
        if match:
            labels[f["id"]] = {"verdict": "dup", "type": None, "rule": "",
                               "reason": f"same as #{match['id']}"}
            n += 1
        else:
            reps.append(f)
    return n


def load_facts(mem: Memory) -> list[dict]:
    now = time.time()
    facts = []
    for fid, fact, emb, ts, ftype in mem.db.execute(
        "SELECT id, fact, embedding, timestamp, fact_type FROM facts ORDER BY id"
    ).fetchall():
        vec = None
        if emb:
            try:
                vec = _unit(json.loads(emb))
            except (ValueError, TypeError):
                pass
        facts.append({"id": fid, "fact": " ".join(fact.split()), "ts": ts or now,
                      "type": normalize_fact_type(ftype), "vec": vec,
                      "age": age_label(max(0.0, (now - (ts or now)) / 86400.0))})
    return facts


def write_review(facts: list[dict], labels: dict[int, dict]) -> dict[str, int]:
    by_id = {f["id"]: f for f in facts}
    groups: dict[str, list[str]] = {v: [] for v in VERDICT_ORDER}
    for fid, lab in labels.items():
        f = by_id[fid]
        verdict = lab["verdict"]
        tag = f"keep:{lab['type']}" if verdict == "keep" and lab["type"] != f["type"] else verdict
        note = f"rule: {lab['rule']}" if verdict == "rule" else f"why: {lab['reason']}"
        groups[verdict].append(f"{tag:<17} #{fid:<5} [{f['type']}, {f['age']}] {f['fact']}  |  {note}")

    header = f"""\
# Fact cleanup review — generated {time.strftime('%Y-%m-%d %H:%M')}
#
# One line per stored fact:  <verdict>  #<id>  [current type, age] fact  |  note
#
# Change the first word to change what happens:
#   keep  keep:stable  keep:situational  keep:ephemeral  drop  rule  dup
# For "rule", the text after "rule:" is what goes into notes/savvy_rules.md;
# edit it freely. Everything after the id is ignored except that rule text.
# Facts missing from this file are left alone.
#
# Then run:  python setup/clean-facts.py --apply
"""
    body = []
    for v in VERDICT_ORDER:
        body.append(f"\n# ===== {v.upper()} ({len(groups[v])}) =====")
        body.extend(sorted(groups[v], key=lambda line: line.split("#", 1)[1]))
    REVIEW_PATH.write_text(header + "\n".join(body) + "\n")
    return {v: len(groups[v]) for v in VERDICT_ORDER}


def parse_review() -> dict[int, dict]:
    plan = {}
    for line in REVIEW_PATH.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2 or not parts[1].startswith("#"):
            continue
        verdict, fid = parts[0].lower(), int(parts[1][1:])
        entry = {"verdict": verdict.split(":")[0], "type": None, "rule": ""}
        if verdict.startswith("keep:"):
            entry["type"] = normalize_fact_type(verdict.split(":", 1)[1])
        if entry["verdict"] == "rule" and "|  rule:" in line:
            entry["rule"] = line.rsplit("|  rule:", 1)[1].strip()
        if entry["verdict"] not in ("keep", "drop", "rule", "dup"):
            raise SystemExit(f"Unknown verdict {verdict!r} on fact #{fid}")
        plan[fid] = entry
    return plan


def apply(mem: Memory) -> int:
    if not REVIEW_PATH.exists():
        print(f"No review file at {REVIEW_PATH}. Run without --apply first.")
        return 1
    plan = parse_review()
    rows = {r[0]: r for r in mem.db.execute(
        "SELECT id, fact, fact_type, timestamp FROM facts").fetchall()}
    plan = {fid: p for fid, p in plan.items() if fid in rows}

    stamp = int(time.time())
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.before-cleanup-{stamp}.db")
    mem.db.execute("PRAGMA wal_checkpoint(FULL)")
    shutil.copy2(DB_PATH, backup)
    print(f"database backed up to {backup}")

    rules_added = rules_merged = 0
    for fid, p in plan.items():
        if p["verdict"] == "rule" and p["rule"]:
            r = add_savvy_rule(p["rule"])
            if r.get("action") == "added":
                rules_added += 1
            else:
                rules_merged += 1

    removed = [(fid, p) for fid, p in plan.items() if p["verdict"] in ("drop", "rule", "dup")]
    archive = MEMORY_DIR / f"fact_archive_{stamp}.jsonl"
    with open(archive, "w") as fh:
        for fid, p in removed:
            _, fact, ftype, ts = rows[fid]
            fh.write(json.dumps({"id": fid, "fact": fact, "type": ftype,
                                 "timestamp": ts, "verdict": p["verdict"]}) + "\n")
    for fid, _ in removed:
        mem.db.execute("DELETE FROM facts WHERE id = ?", (fid,))
    retyped = 0
    for fid, p in plan.items():
        if p["verdict"] == "keep" and p["type"] and p["type"] != normalize_fact_type(rows[fid][2]):
            mem.db.execute("UPDATE facts SET fact_type = ? WHERE id = ?", (p["type"], fid))
            retyped += 1
    mem.db.commit()

    print(f"removed {len(removed)} fact(s), archived to {archive}")
    print(f"retyped {retyped} kept fact(s)")
    print(f"rules: {rules_added} added, {rules_merged} merged into existing ones")
    print("remaining:", mem.get_stats()["total_facts"], mem.fact_type_counts())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help=f"act on {REVIEW_PATH.name} instead of classifying")
    args = ap.parse_args()

    mem = Memory(session_id="fact_cleanup")
    try:
        if args.apply:
            return apply(mem)

        config = load_app_config()
        client = get_client(config)
        model = config.get("anthropic_model", "claude-sonnet-5-5")
        facts = load_facts(mem)
        print(f"{len(facts)} fact(s) to review, in batches of {BATCH_SIZE}\n")

        labels: dict[int, dict] = {}
        for start in range(0, len(facts), BATCH_SIZE):
            batch = facts[start:start + BATCH_SIZE]
            try:
                labels.update(classify(client, model, batch))
            except Exception as e:
                print(f"  batch {start // BATCH_SIZE + 1}: FAILED ({e}) — those facts are left out")
                continue
            print(f"  {min(start + BATCH_SIZE, len(facts))}/{len(facts)}")

        dups = mark_duplicates(facts, labels)
        counts = write_review(facts, labels)
        print(f"\nreviewed {len(labels)}/{len(facts)}  ({dups} near-duplicates)")
        for v in VERDICT_ORDER:
            print(f"  {v:<6} {counts[v]:>5}")
        print(f"\nReview file: {REVIEW_PATH}")
        print("Edit any verdicts, then re-run with --apply.")
        return 0
    finally:
        mem.close()


if __name__ == "__main__":
    sys.exit(main())
