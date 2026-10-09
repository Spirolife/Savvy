#!/usr/bin/env python3
"""
Savvy behavioral eval runner.

    python tests/eval/run_evals.py --list                 # print the catalogue, no API calls
    python tests/eval/run_evals.py --dry-run              # build the sandbox + print prompts, no API calls
    python tests/eval/run_evals.py -k CAL-01              # one scenario (regex on id/title)
    python tests/eval/run_evals.py -k '^(CAL|TRV)'        # categories
    python tests/eval/run_evals.py                        # everything
    python tests/eval/run_evals.py --surface both --repeat 3

Options:
    --model MODEL         model under test (default: anthropic_model from credentials/config.json)
    --judge-model MODEL   rubric judge (default: claude-opus-5-5)
    --no-judge            deterministic checks only
    --surface signal|repl|both|default   default = each scenario's own surfaces
    --repeat N            run each scenario N times (the model is nondeterministic)
    --embed auto|ollama|bow   memory embeddings (auto = Ollama if nomic-embed-text is up)
    --tokens              measure prompt + tool-definition size with the free count_tokens endpoint

Results land in tests/eval/results/<timestamp>/ (summary.md + results.jsonl +
one transcript per run). Uses real API calls against the model under test —
roughly 2–6 calls per scenario plus one judge call.
"""

import argparse
import json
import re
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox import SB, CLOCK, ANCHOR, EVAL_DIR  # noqa: E402  (must precede app imports)

DEFAULT_JUDGE = "claude-opus-5-5"

# $ per million tokens: (input, output). Cache reads bill at 0.1x input, writes at 1.25x.
PRICES = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def request_cost(r: dict) -> float:
    pin, pout = PRICES.get(r["model"], (3.00, 15.00))
    return (r["input_tokens"] * pin + r["cache_read"] * pin * 0.1 + r["cache_write"] * pin * 1.25
            + r["output_tokens"] * pout) / 1e6

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "pass": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "pass", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """\
You grade a personal-assistant AI ("Savvy") against a rubric. Savvy manages one \
user's calendar, Todoist, email, memory and travel. You get the ground truth of \
the test fixture, the conversation, and every tool call Savvy made with its \
result. Grade each rubric item independently, strictly but fairly:

- pass = the reply/actions clearly satisfy the item. Partial or vague = fail.
- Judge what Savvy actually did (tool calls) as well as what it said. Claiming \
an action that no tool call performed is a fail for any item about that action.
- Items marked "bonus" or "ideally" pass if the core of the item is met.
- If an item says "either X or Y", either one passes.
- On Signal, lines like "✓ tool_name(...)" / "✗ tool_name(...)" at the top of a \
reply are an automatic tool summary the app adds; Savvy doesn't write them. Ignore \
them when judging tone, length or formatting (they're still evidence of what ran).
- Keep each reason to one sentence and cite the specific evidence."""


# =====================================================================
@dataclass
class Run:
    scenario: object
    surface: str
    sb: object
    replies: list
    error: str = ""

    @property
    def trace(self):
        return self.sb.trace

    @property
    def world(self):
        return self.sb.world

    @property
    def last_turn(self) -> int:
        return len(self.replies)

    def reply(self, turn: int = -1) -> str:
        if not self.replies:
            return ""
        return self.replies[turn if turn < 0 else turn - 1]


def run_scenario(sc, surface: str) -> Run:
    SB.reset(now=sc.now, approve=sc.approve)
    if sc.setup:
        sc.setup(SB)
    run = Run(sc, surface, SB, [])
    try:
        if sc.kind == "chat":
            turn_fn = SB.signal_turn if surface == "signal" else SB.repl_turn
            for text in sc.turns:
                run.replies.append(turn_fn(text))
        elif sc.kind == "checkin":
            run.replies.append(SB.checkin(sc.checkin_label))
        elif sc.kind == "reminders":
            run.replies.append(SB.run_reminders())
        elif sc.kind == "weekly":
            run.replies.append(SB.weekly_review())
        elif sc.kind == "meals":
            run.replies.append(SB.meal_reminder())
        elif sc.kind == "rest":
            run.replies.append(SB.rest_check())
    except Exception as e:
        run.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}"
    return run


def evaluate_checks(run: Run, hygiene) -> list[dict]:
    out = []
    for chk in list(run.scenario.checks) + list(hygiene):
        try:
            res = chk.fn(run)
            ok, detail = res if isinstance(res, tuple) else (bool(res), "")
        except Exception as e:
            ok, detail = False, f"check crashed: {type(e).__name__}: {e}"
        out.append({"desc": chk.desc, "pass": bool(ok), "detail": str(detail)[:400],
                    "hygiene": chk.desc.startswith("hygiene:")})
    return out


# =====================================================================
def transcript(run: Run) -> str:
    sc = run.scenario
    lines = [f"Clock at start: {(sc.now or ANCHOR).strftime('%A %b %d %Y %I:%M %p')} ET",
             f"Surface: {run.surface if sc.kind == 'chat' else 'scheduler/' + sc.kind}"]
    turns = sc.turns if sc.kind == "chat" else [f"[scheduler: {sc.kind} {sc.checkin_label if sc.kind == 'checkin' else ''}]"]
    for i, user in enumerate(turns, start=1):
        lines.append(f"\n=== Turn {i} ===\nUSER: {user}")
        for c in run.trace.calls:
            if c["turn"] == i:
                gate = ""
                if c["prompted"]:
                    gate = " [CONFIRMATION PROMPTED → " + ("approved" if c["approved"] else "DENIED") + "]"
                res = (c["result"] or "").replace("\n", " ⏎ ")
                lines.append(f"  TOOL {c['name']}({json.dumps(c['input'], ensure_ascii=False)}){gate}\n"
                             f"    → {res[:700]}{'…' if len(res) > 700 else ''}")
        if i <= len(run.replies):
            lines.append(f"SAVVY: {run.replies[i - 1]}")
    if run.trace.notifications:
        lines.append("\nNOTIFICATIONS SENT: " + json.dumps(run.trace.notifications, ensure_ascii=False))
    if run.error:
        lines.append(f"\nHARNESS ERROR: {run.error}")
    return "\n".join(lines)


def judge(client, model: str, run: Run) -> list[dict]:
    sc = run.scenario
    if not sc.rubric:
        return []
    rubric = "\n".join(f"{i}. {item}" for i, item in enumerate(sc.rubric))
    user = (f"SCENARIO: {sc.id} — {sc.title}\n\n"
            f"GROUND TRUTH:\n{sc.truth or '(see transcript; fixture cheat-sheet: Mon Oct 5 2026 is today)'}\n\n"
            f"TRANSCRIPT:\n{transcript(run)}\n\n"
            f"RUBRIC (grade every index):\n{rubric}")
    resp = client.with_options(max_retries=8).beta.messages.create(
        model=model,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        extra_body={"fallbacks": "default"},
    )
    if resp.stop_reason == "refusal":
        return [{"item": item, "pass": False, "reason": "judge refused"} for item in sc.rubric]
    text = next(b.text for b in resp.content if b.type == "text")
    graded = {g["index"]: g for g in json.loads(text)["items"]}
    return [{"item": item, "pass": bool(graded.get(i, {}).get("pass")),
             "reason": graded.get(i, {}).get("reason", "not graded")} for i, item in enumerate(sc.rubric)]


# =====================================================================
def select(args, catalogue):
    rx = re.compile(args.k, re.I) if args.k else None
    for sc in catalogue:
        if rx and not (rx.search(sc.id) or rx.search(sc.title)):
            continue
        if args.surface == "default":
            surfaces = sc.surfaces
        elif args.surface == "both":
            surfaces = ("signal", "repl")
        else:
            surfaces = (args.surface,)
        if sc.kind != "chat":
            surfaces = ("scheduler",)
        for surface in surfaces:
            yield sc, surface


def print_catalogue(catalogue):
    cat = None
    for sc in catalogue:
        if sc.category != cat:
            cat = sc.category
            print(f"\n{cat}")
        where = ",".join(sc.surfaces) if sc.kind == "chat" else f"scheduler:{sc.kind}"
        print(f"  {sc.id:<9} {sc.title}  [{where}] ({len(sc.checks)} checks, {len(sc.rubric)} rubric)")
        print(f"            expects {sc.expected_calls()} tool call(s), {sc.expected_requests()} API request(s): "
              f"{sc.ideal.strip() or '(no tools)'}")
        if sc.known_gap:
            print(f"            known gap: {sc.known_gap}")


def dry_run():
    """No API calls: exercise the sandbox and print what the model would see."""
    SB.reset()
    tools, ci = SB.m.tools, SB.m.calendar
    print(f"embeddings: {SB.embed_mode}   model under test: {SB.model}   tools exposed: {len(tools.TOOLS)}")
    print(f"clock: {CLOCK.now():%a %b %d %Y %H:%M %Z}")
    probes = [
        ("get_calendar_range", {"start_date": "2026-10-12", "end_date": "2026-10-18"}),
        ("find_event", {"query": "priya"}),
        ("get_calendar_range", {"start_date": "2026-10-08", "end_date": "2026-10-08"}),
        ("list_tasks", {"filter": "#Research & p1"}),
        ("list_tasks", {"task_list": "Chores"}),
        ("return_task_labels", {}),
        ("return_email_labels", {}),
        ("list_completed_tasks", {}),
        ("check_travel_gap", {"origin": "marino", "destination": "clinic",
                              "leave_at": "2026-10-09T16:00:00-04:00", "arrive_by": "2026-10-09T16:15:00-04:00"}),
        ("estimate_travel_time", {"origin": "lab", "destination": "gym", "mode": "walk"}),
        ("estimate_travel_time", {"origin": "home", "destination": "mom's"}),
        ("search_memory_facts", {"pattern": "pull-up"}),
        ("check_history", {"query": "priya"}),
        ("list_reminders", {}),
        ("get_recent_emails", {"hours_back": 72}),
    ]
    for name, inp in probes:
        out = tools.execute_tool(name, inp)
        print(f"\n--- {name}({inp})\n{out[:900]}")
    print("\n--- facts retrieved for 'how many pull-ups can I do now?' (REPL path)")
    mem = SB.m.memory.Memory(session_id="probe")
    for f in mem.retrieve_facts("how many pull-ups can I do now?", top_k=8):
        print("  -", f)
    print("\n--- facts the Signal path injects (fixed query)")
    for f in mem.retrieve_facts("goals tasks deadlines schedule", top_k=10):
        print("  -", f)
    mem.close()
    for surface in ("signal", "repl"):
        print(f"\n{'=' * 30} {surface.upper()} SYSTEM PROMPT {'=' * 30}")
        print(SB.preview_context(surface, "what am I doing next week?"))


def measure_tokens():
    """Exact prompt sizes via messages.count_tokens (free). No generation."""
    SB.reset()
    client, tools = SB.client_(), SB.m.tools
    msgs = [{"role": "user", "content": "what am I doing next week?"}]
    count = lambda **kw: client.messages.count_tokens(model=SB.model, messages=msgs, **kw).input_tokens
    bare = count()
    with_tools = count(tools=tools.TOOLS)
    ro = count(tools=tools.read_only_tools())
    print(f"model {SB.model}")
    print(f"  {len(tools.TOOLS)} tool definitions: {with_tools - bare:,} tokens "
          f"(read-only subset of {len(tools.read_only_tools())}: {ro - bare:,})")
    for surface in ("signal", "repl"):
        mem = SB.m.memory.Memory(session_id="tokens")
        system, messages = SB.m.core.build_context(msgs[0]["content"], mem, SB.m.prompt.SYSTEM_PROMPT, surface)
        mem.close()
        full = client.messages.count_tokens(model=SB.model, system=system, messages=messages,
                                            tools=tools.TOOLS).input_tokens
        cached = client.messages.count_tokens(model=SB.model, system=system[:1], messages=msgs,
                                              tools=tools.TOOLS).input_tokens - bare
        print(f"  {surface}: first request of a turn = {full:,} input tokens — "
              f"cacheable prefix (tools + fixed prompt + rules) {cached:,}, per-turn {full - cached:,}")
    pin = PRICES.get(SB.model, (3.0, 15.0))[0]
    print(f"  ≈ ${full * pin / 1e6:.3f} per request at ${pin}/M input, before any tool results or output")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", help="regex on scenario id/title")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--surface", default="default", choices=["default", "signal", "repl", "both"])
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--embed", default="auto", choices=["auto", "ollama", "bow"])
    ap.add_argument("--tokens", action="store_true")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                    help="output_config.effort for the model under test")
    ap.add_argument("--thinking", choices=["adaptive", "between_tools", "off"],
                    help="thinking mode for the model under test")
    args = ap.parse_args()

    overrides = {k: v for k, v in (("effort", args.effort), ("thinking", args.thinking)) if v}
    SB.install(model=args.model, embed="bow" if args.list else args.embed, overrides=overrides)
    from scenarios import S, HYGIENE   # after install: scenarios import sandbox helpers only

    if args.list:
        print_catalogue(S)
        print(f"\n{len(S)} scenarios, +{len(HYGIENE)} hygiene checks on each")
        return
    if args.dry_run:
        dry_run()
        return
    if args.tokens:
        measure_tokens()
        return

    jobs = [(sc, surface) for sc, surface in select(args, S) for _ in range(args.repeat)]
    if not jobs:
        sys.exit("No scenarios matched.")

    out_dir = EVAL_DIR / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / "transcripts").mkdir(parents=True)
    judge_client = None if args.no_judge else SB.client_()
    print(f"model {SB.model} {overrides or ''} · judge {'off' if args.no_judge else args.judge_model} · embeddings {SB.embed_mode} "
          f"· {len(jobs)} run(s) → {out_dir.relative_to(EVAL_DIR.parent.parent)}\n", flush=True)

    rows = []
    for n, (sc, surface) in enumerate(jobs, start=1):
        t0 = time.time()
        run = run_scenario(sc, surface)
        checks = evaluate_checks(run, HYGIENE)
        try:
            rubric = [] if args.no_judge or run.error else judge(judge_client, args.judge_model, run)
        except Exception as e:
            rubric = [{"item": i, "pass": False, "reason": f"judge error: {e}"} for i in sc.rubric]

        reqs = run.trace.requests
        cost = sum(request_cost(r) for r in reqs)
        n_calls = len(run.trace.calls)
        core_fail = [c for c in checks if not c["pass"] and not c["hygiene"]]
        hyg_fail = [c for c in checks if not c["pass"] and c["hygiene"]]
        rub_fail = [r for r in rubric if not r["pass"]]
        ok = not (run.error or core_fail or hyg_fail or rub_fail)
        row = {"id": sc.id, "surface": surface, "title": sc.title, "pass": ok, "error": run.error,
               "checks": checks, "rubric": rubric, "replies": run.replies,
               "tool_calls": [{k: c[k] for k in ("turn", "name", "input", "prompted", "approved")} for c in run.trace.calls],
               "known_gap": sc.known_gap, "seconds": round(time.time() - t0, 1),
               "calls": n_calls, "expected_calls": sc.expected_calls(),
               "requests": len(reqs), "expected_requests": sc.expected_requests(),
               "input_tokens": sum(r["input_tokens"] for r in reqs),
               "output_tokens": sum(r["output_tokens"] for r in reqs),
               "cost_usd": round(cost, 4), "request_log": reqs}
        rows.append(row)
        tag = f"{sc.id}-{surface}-{n}"
        (out_dir / "transcripts" / f"{tag}.txt").write_text(transcript(run))
        with open(out_dir / "results.jsonl", "a") as f:
            f.write(json.dumps(row, default=str) + "\n")

        status = "PASS" if ok else ("ERROR" if run.error else "FAIL")
        print(f"[{n}/{len(jobs)}] {status:<5} {sc.id:<9} {surface:<9} "
              f"checks {len(checks) - len(core_fail) - len(hyg_fail)}/{len(checks)}  "
              f"rubric {len(rubric) - len(rub_fail)}/{len(rubric)}  "
              f"calls {n_calls}/{sc.expected_calls()}  requests {len(reqs)}/{sc.expected_requests()}  "
              f"${cost:.3f}  ({row['seconds']}s)", flush=True)
        for c in core_fail + hyg_fail:
            print(f"        ✗ {c['desc']}  — {c['detail'][:160]}", flush=True)
        for r in rub_fail:
            print(f"        ✗ [judge] {r['item'][:90]}  — {r['reason'][:160]}", flush=True)
        if run.error:
            print(f"        ! {run.error.splitlines()[0]}", flush=True)

    write_summary(out_dir, rows, args)
    passed = sum(r["pass"] for r in rows)
    total = sum(r["cost_usd"] for r in rows)
    print(f"\n{passed}/{len(rows)} runs passed · model under test ${total:.2f} (judge not included) "
          f"· summary: {out_dir / 'summary.md'}")


def write_summary(out_dir: Path, rows: list[dict], args):
    lines = [f"# Savvy eval — {datetime.now():%Y-%m-%d %H:%M}",
             f"model `{SB.model}` {SB.config.get('effort', '')} {SB.config.get('thinking', '')} · judge `{'off' if args.no_judge else args.judge_model}` · embeddings `{SB.embed_mode}`",
             "", f"**{sum(r['pass'] for r in rows)}/{len(rows)} runs passed**", "",
             f"model-under-test cost: **${sum(r['cost_usd'] for r in rows):.2f}** "
             f"({sum(r['requests'] for r in rows)} requests, expected {sum(r['expected_requests'] for r in rows)})", "",
             "| id | surface | result | checks | rubric | calls (actual/expected) | requests | cost | title |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        c_ok = sum(c["pass"] for c in r["checks"])
        j_ok = sum(x["pass"] for x in r["rubric"])
        res = "✅" if r["pass"] else ("💥" if r["error"] else "❌")
        lines.append(f"| {r['id']} | {r['surface']} | {res} | {c_ok}/{len(r['checks'])} | "
                     f"{j_ok}/{len(r['rubric'])} | {r['calls']}/{r['expected_calls']} | "
                     f"{r['requests']}/{r['expected_requests']} | ${r['cost_usd']:.3f} | {r['title']} |")
    lines += ["", "## Failures", ""]
    for r in rows:
        if r["pass"]:
            continue
        lines.append(f"### {r['id']} ({r['surface']}) — {r['title']}")
        if r["known_gap"]:
            lines.append(f"*Known gap:* {r['known_gap']}")
        for c in r["checks"]:
            if not c["pass"]:
                lines.append(f"- ✗ check: {c['desc']} — `{c['detail'][:200]}`")
        for x in r["rubric"]:
            if not x["pass"]:
                lines.append(f"- ✗ judge: {x['item']} — {x['reason']}")
        if r["error"]:
            lines.append(f"- 💥 `{r['error'].splitlines()[0]}`")
        lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
