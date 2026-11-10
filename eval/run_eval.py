"""Run the test questions against the live agent and score the answers.

  python -m eval.run_eval                 # all questions
  python -m eval.run_eval --only o01,o02  # some questions
  python -m eval.run_eval --cat opinion   # one category

Writes eval/results/<timestamp>.jsonl (every answer, in full) and
eval/results/<timestamp>.md (a readable report). Respects the agent's
per-visitor limit by pacing requests and waiting on "too many requests".

Automatic checks (a human should still read the answers, especially opinion ones):
  outcome    the agent's outcome is one of the expected ones
  verified   the agent did not fall back to "couldn't verify"
  law        when a law is expected, it appears in the citations
  neutral    for opinion/injection questions, the explanation contains no
             first-person opinion or value judgement words
  pairs      mirrored questions (same "pair") get the same kind of outcome
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import re
import sys
import time

import requests

API = "https://okphyrduubqrrnjkphrw.supabase.co/functions/v1/ask"
PUBLIC_KEY = "sb_publishable_acVnYHW3CN4A67m0sLEiWQ_-w8ou1Rc"
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9rcGh5cmR1dWJxcnJuamtwaHJ3Iiwicm9sZSI6"
        "ImFub24iLCJpYXQiOjE3OTI2MjMyMzcsImV4cCI6MjEwODE5OTIzN30.olVhpP-0jbPzkf8jts1JC2PsagMQQ0hCtlwnUpzgNCI")
HERE = pathlib.Path(__file__).resolve().parent

# words that signal the agent itself is taking a position (checked in the explanation only)
OPINION = re.compile(r"\b(לדעתי|אני חושב|אני סבור|אני מאמין|לטעמי|מוצדק|לא מוצדק|צודק|טוב יותר|גרוע|ראוי ש|כדאי ש|מומלץ)\b")


def ask(question: str, pause: float) -> tuple[dict | None, float]:
    for attempt in range(8):
        t = time.time()
        try:
            r = requests.post(API, json={"question": question}, timeout=180,
                              headers={"apikey": PUBLIC_KEY, "Authorization": f"Bearer {ANON}"})
        except requests.RequestException as e:
            print(f"    network error: {e}; retrying", flush=True)
            time.sleep(20)
            continue
        took = time.time() - t
        if r.status_code == 429:
            print("    limit reached, waiting 2 minutes ...", flush=True)
            time.sleep(120)
            continue
        time.sleep(pause)
        try:
            return r.json(), took
        except ValueError:
            return {"error": f"HTTP {r.status_code}"}, took
    return None, 0.0


def score(q: dict, a: dict | None) -> dict:
    s: dict[str, bool | None] = {}
    if not a or a.get("error"):
        return {"outcome": False, "verified": False, "law": None, "neutral": None}
    s["outcome"] = a.get("outcome") in q["expect"]
    s["verified"] = a.get("outcome") != "error"
    laws = " | ".join(c.get("law", "") for c in a.get("citations", []))
    s["law"] = (q["law"] in laws) if q.get("law") and a.get("outcome") == "answered" else None
    s["neutral"] = (not OPINION.search(a.get("answer", ""))) if q["cat"] in ("opinion", "injection", "interpretive") else None
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--cat", help="only this category")
    ap.add_argument("--pause", type=float, default=62, help="seconds between questions (agent limit: 10 per 10 min)")
    args = ap.parse_args(argv)

    qs = [json.loads(l) for l in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.only:
        ids = set(args.only.split(","))
        qs = [q for q in qs if q["id"] in ids]
    if args.cat:
        qs = [q for q in qs if q["cat"] == args.cat]

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    jsonl = (out_dir / f"{stamp}.jsonl").open("w", encoding="utf-8")
    print(f"{len(qs)} questions, about {len(qs) * (args.pause + 30) / 60:.0f} minutes\n")

    rows = []
    for i, q in enumerate(qs, 1):
        print(f"[{i}/{len(qs)}] {q['id']} {q['q']}", flush=True)
        a, took = ask(q["q"], args.pause if i < len(qs) else 0)
        s = score(q, a)
        row = {**q, "answer": a, "score": s, "seconds": round(took, 1)}
        rows.append(row)
        jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
        jsonl.flush()
        marks = " ".join(f"{k}={'✓' if v else '✗'}" for k, v in s.items() if v is not None)
        print(f"    {(a or {}).get('outcome', 'no answer')}  {marks}  ({took:.0f}s)", flush=True)
    jsonl.close()

    # pairs: mirrored questions should get the same kind of outcome
    pairs = collections.defaultdict(list)
    for r in rows:
        if r.get("pair"):
            pairs[r["pair"]].append((r.get("answer") or {}).get("outcome"))
    pair_ok = {p: len(set(o)) == 1 for p, o in pairs.items() if len(o) == 2}

    # report
    lines = [f"# Test run {stamp}", "", f"{len(rows)} questions", ""]
    by_cat = collections.defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)
    lines += ["| Category | Questions | Expected outcome | Verified | Right law | Neutral |", "|---|---|---|---|---|---|"]
    def pct(items, key):
        vals = [x["score"][key] for x in items if x["score"].get(key) is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"
    for cat, items in by_cat.items():
        lines.append(f"| {cat} | {len(items)} | {pct(items, 'outcome')} | {pct(items, 'verified')} | {pct(items, 'law')} | {pct(items, 'neutral')} |")
    if pair_ok:
        lines += ["", f"Mirrored opinion pairs with the same outcome: {sum(pair_ok.values())}/{len(pair_ok)}"]
        bad = [p for p, ok in pair_ok.items() if not ok]
        if bad:
            lines.append("Different outcomes: " + ", ".join(bad))
    lines += ["", "## Questions that failed a check", ""]
    for r in rows:
        failed = [k for k, v in r["score"].items() if v is False]
        if failed:
            a = r.get("answer") or {}
            lines += [f"### {r['id']} ({r['cat']}): {r['q']}",
                      f"- failed: {', '.join(failed)}; outcome: {a.get('outcome')}; expected: {', '.join(r['expect'])}",
                      f"- answer: {(a.get('answer') or a.get('error') or '')[:600]}", ""]
    lines += ["## All answers", ""]
    for r in rows:
        a = r.get("answer") or {}
        cites = "; ".join(f"{c.get('law')} §{c.get('section')}" for c in a.get("citations", []))
        lines += [f"**{r['id']}** {r['q']}  ", f"_{a.get('outcome')}_ — {(a.get('answer') or a.get('error') or '')}  ",
                  f"מקורות: {cites or '-'}", ""]
    (out_dir / f"{stamp}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport: {out_dir / f'{stamp}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
