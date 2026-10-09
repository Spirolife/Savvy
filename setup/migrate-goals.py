#!/usr/bin/env python3
"""
migrate-goals.py — move goals into the goal tracker.

Run:  python setup/migrate-goals.py                    # seed resolutions, propose the rest
      python setup/migrate-goals.py --apply-proposals  # also add the proposals

1. Seeds the New Year's resolutions that used to be hard-coded in the system
   prompt as structured goals (idempotent: anything already tracked is skipped).
2. Reads goal-shaped facts from memory ("User wants to...", "...goal...") and
   asks the model to consolidate them into proposed goals, listing any that
   contradict each other (e.g. two different bedtime targets) separately —
   those need a human decision and are never added automatically.
   Proposals are saved to memory/goal_proposals.json for review.

The source facts are left in memory untouched.
"""

import argparse
import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import goals  # noqa: E402
from core import get_client, load_app_config  # noqa: E402
from memory import Memory  # noqa: E402
from paths import MEMORY_DIR  # noqa: E402

PROPOSALS_PATH = MEMORY_DIR / "goal_proposals.json"
YEAR_END = "2026-12-31"

# The resolutions block from prompt.py, as it was written.
RESOLUTIONS = [
    {"title": "Become conversational in Egyptian Arabic", "category": "language",
     "target": "Hold an everyday conversation in Egyptian Arabic", "due_date": YEAR_END},
    {"title": "Fully rehab my ankle", "category": "health",
     "target": "No more stiffness or pain by year end; keep up the PT regimen from my therapist",
     "why": "Lost 3 ligaments in a severe injury", "due_date": YEAR_END},
    {"title": "Work out 3x/week", "category": "fitness",
     "target": "3 workouts a week, balancing climbing, cardio and calisthenics", "due_date": YEAR_END,
     "children": [
         {"title": "5 pull-ups", "category": "fitness", "target": "5 strict pull-ups"},
         {"title": "20 push-ups", "category": "fitness", "target": "20 push-ups"},
         {"title": "30s L-sit", "category": "fitness", "target": "Hold an L-sit for 30 seconds"},
         {"title": "Pistol squat both sides", "category": "fitness", "target": "One pistol squat on each leg"},
         {"title": "Controlled nordic negative", "category": "fitness", "target": "A controlled nordic hamstring negative"},
     ]},
    {"title": "Understand my health", "category": "health",
     "target": "A diagnosis for the chronic sore throat/post-nasal drip, constant fatigue, bloating, extra soreness, "
               "thinning hair, hormone/mood swings with periods, and suspected lipedema",
     "why": "Doctors don't know what's wrong yet. Persist with appointments, diet changes, and controlling "
            "environmental allergens.", "due_date": YEAR_END},
    {"title": "Reach my body measurement goals", "category": "fitness",
     "target": "36/26/36 bust/waist/hip; upper arms 11→9 in, thighs 22→20 in; lose ~15 lb of fat while building "
               "strength — measurements matter more than the scale",
     "due_date": YEAR_END,
     "progress": "Starting point: 36.5/29/40, 155 lb at 5'2\""},
]

GOAL_FACT = re.compile(r"\b(goal|resolution|wants? to|aim|target|hoping to|trying to|plan(s|ning)? to|"
                       r"working (on|toward))\b", re.I)

PROMPT = """These are facts a personal assistant stored about one person over several months. Some describe \
goals they want to pursue. Their goals are already tracked as:

{tracked}

From the facts below, propose ADDITIONAL goals worth tracking — only real aspirations the person stated \
(not preferences, not one-off plans, not things already tracked above). Merge restatements into one goal. \
If facts state CONFLICTING versions of the same goal (two different bedtime targets, say), do not pick one: \
put them in "conflicts" for the person to decide.

Facts:
{facts}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "category": {"type": "string"},
                "target": {"type": "string"}, "from_facts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "category", "target", "from_facts"], "additionalProperties": False}},
        "conflicts": {"type": "array", "items": {
            "type": "object",
            "properties": {"topic": {"type": "string"},
                           "versions": {"type": "array", "items": {"type": "string"}}},
            "required": ["topic", "versions"], "additionalProperties": False}},
    },
    "required": ["proposals", "conflicts"], "additionalProperties": False,
}


def seed_resolutions() -> int:
    added = 0
    for g in RESOLUTIONS:
        parent = goals.add_goal(g["title"], g["category"], g.get("target", ""), g.get("why", ""), g.get("due_date"))
        if not parent.get("duplicate"):
            added += 1
            if g.get("progress"):
                goals.log_progress(parent["id"], g["progress"])
        for c in g.get("children", []):
            child = goals.add_goal(c["title"], c["category"], c.get("target", ""), parent_id=parent["id"])
            added += 0 if child.get("duplicate") else 1
    return added


def propose_from_facts() -> dict:
    mem = Memory(session_id="migrate_goals")
    facts = [r[0] for r in mem.db.execute("SELECT fact FROM facts ORDER BY timestamp")]
    mem.close()
    goal_facts = [f for f in facts if GOAL_FACT.search(f)]
    if not goal_facts:
        return {"proposals": [], "conflicts": []}
    config = load_app_config()
    resp = get_client(config).messages.create(
        model=config.get("anthropic_model", "claude-sonnet-4-6"), max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT.format(
            tracked=goals.format_goals_for_context(), facts="\n".join(f"- {f}" for f in goal_facts))}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply-proposals", action="store_true", help="add the proposed goals (never the conflicts)")
    args = ap.parse_args()

    n = seed_resolutions()
    print(f"Resolutions: {n} goal(s) added" + (" (already tracked)" if n == 0 else ""))

    if args.apply_proposals and PROPOSALS_PATH.exists():
        result = json.loads(PROPOSALS_PATH.read_text())
    else:
        result = propose_from_facts()
        PROPOSALS_PATH.write_text(json.dumps(result, indent=2) + "\n")

    print(f"\nProposed from memory ({len(result['proposals'])}):")
    for p in result["proposals"]:
        print(f"  + {p['title']} — {p['target']}  [{p['category']}]")
    if result["conflicts"]:
        print("\nConflicts — decide these yourself, then tell Savvy (they are never added automatically):")
        for c in result["conflicts"]:
            print(f"  ? {c['topic']}: " + " | ".join(c["versions"]))

    if args.apply_proposals:
        added = sum(0 if goals.add_goal(p["title"], p["category"], p["target"]).get("duplicate") else 1
                    for p in result["proposals"])
        print(f"\nAdded {added} proposed goal(s).")
    elif result["proposals"]:
        print(f"\nSaved to {PROPOSALS_PATH}. Review, edit if needed, then run with --apply-proposals.")

    print("\nNow tracked:\n" + goals.format_goals_for_context())


if __name__ == "__main__":
    main()
