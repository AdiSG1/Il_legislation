"""Local usage and cost dashboard for Il_legislation.

Runs only on this computer:  python -m dashboard.server   (or double-click dashboard.bat)
and opens http://127.0.0.1:8765 in the browser.

The browser page never sees a key. This program reads the keys from keys.txt,
asks each service for its numbers, and hands the page one JSON summary.

Keys it uses (in keys.txt, next to DATABASE_URL):
  DATABASE_URL           required  questions, tokens, database size, refresh runs
  SUPABASE_ACCESS_TOKEN  optional  project status and API request counts
  VERCEL_TOKEN           optional  latest website deployments
  ANTHROPIC_ADMIN_KEY    optional  Anthropic's own billed cost (needs a Console organization)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
TZ = "Asia/Jerusalem"

PROJECT_REF = "okphyrduubqrrnjkphrw"
SITE_URL = "https://illegislation-web.vercel.app"

# Claude Sonnet 5.5 list prices, USD per million tokens (platform.claude.com/docs/en/about-claude/pricing)
PRICE_IN = float(os.environ.get("CLAUDE_PRICE_IN", "2"))
PRICE_OUT = float(os.environ.get("CLAUDE_PRICE_OUT", "10"))

# Free-plan allowances and the agent's own limits
LIMITS = {
    "db_bytes": 500 * 1024 * 1024,          # Supabase Free: database size
    "edge_calls_month": 500_000,            # Supabase Free: Edge Function invocations
    "questions_day": int(os.environ.get("DAILY_REQUEST_LIMIT", "500")),  # set in the agent
}


def load_keys() -> None:
    for p in (ROOT / "keys.txt", ROOT / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def has(key: str) -> bool:
    return bool(os.environ.get(key, "").strip())


def iso(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


# ------------------------------------------------------------------ sources
def from_database() -> dict:
    from ingest.store import connect     # same connection code as the weekly refresh

    con = connect()
    cur = con.cursor()

    def rows(sql, args=()):
        cur.execute(sql, args)
        names = [d[0] for d in cur.description]
        return [{n: iso(v) for n, v in zip(names, r)} for r in cur.fetchall()]

    agg = f"""
        count(*) filter (where outcome <> 'limited')                  as questions,
        count(*) filter (where outcome = 'answered')                  as answered,
        count(*) filter (where outcome = 'interpretive')              as interpretive,
        count(*) filter (where outcome = 'not_found')                 as not_found,
        count(*) filter (where outcome = 'refused')                   as refused,
        count(*) filter (where outcome = 'error')                     as errors,
        count(*) filter (where outcome = 'limited')                   as limited,
        coalesce(sum(input_tokens), 0)::bigint                        as input_tokens,
        coalesce(sum(output_tokens), 0)::bigint                       as output_tokens,
        round(avg(duration_ms) filter (where outcome <> 'limited'))   as avg_ms
    """
    local_day = f"(created_at at time zone '{TZ}')::date"
    daily = rows(f"""
        select {local_day} as day, {agg} from qa_log
        where created_at > now() - interval '31 days' group by 1 order by 1""")
    month = rows(f"""
        select {agg}, count(*) as edge_calls from qa_log
        where created_at >= date_trunc('month', now() at time zone '{TZ}') at time zone '{TZ}'""")[0]
    today = rows(f"""
        select count(*) as requests from qa_log
        where {local_day} = (now() at time zone '{TZ}')::date""")[0]
    recent = rows("""
        select created_at, left(question, 140) as question, outcome, verified,
               input_tokens, output_tokens, duration_ms
        from qa_log order by id desc limit 12""")
    size = rows("select pg_database_size(current_database()) as bytes")[0]["bytes"]
    tables = rows("""
        select c.relname as name, pg_total_relation_size(c.oid) as bytes
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r'
        order by 2 desc limit 6""")
    runs = rows("""
        select started_at, finished_at, laws_seen, laws_changed, sections_written,
               coalesce(jsonb_array_length(case when jsonb_typeof(errors) = 'array' then errors end), 0) as error_count
        from ingest_runs order by id desc limit 5""")
    laws = rows("select text_status as status, count(*) as n from laws group by 1 order by 2 desc")
    sections = rows("select count(*) as n from sections")[0]["n"]
    con.close()
    return {"daily": daily, "month": month, "today": today, "recent": recent,
            "db_bytes": size, "tables": tables, "runs": runs, "laws": laws, "sections": sections}


def from_supabase() -> dict:
    h = {"Authorization": f"Bearer {os.environ['SUPABASE_ACCESS_TOKEN'].strip()}"}
    base = f"https://api.supabase.com/v1/projects/{PROJECT_REF}"
    r = requests.get(base, headers=h, timeout=20)
    r.raise_for_status()
    p = r.json()
    out = {"status": p.get("status"), "region": p.get("region"), "name": p.get("name")}
    try:
        c = requests.get(f"{base}/analytics/endpoints/usage.api-counts", params={"interval": "7day"},
                         headers=h, timeout=20)
        c.raise_for_status()
        res = c.json().get("result") or []
        out["api_7d"] = {k: sum(int(x.get(k) or 0) for x in res)
                         for k in ("total_rest_requests", "total_auth_requests",
                                   "total_storage_requests", "total_realtime_requests")}
    except Exception as e:              # the counts are a bonus; status is what matters
        out["api_7d_error"] = short(e)
    return out


def from_vercel() -> dict:
    params = {"projectId": os.environ.get("VERCEL_PROJECT", "illegislation-web"), "limit": 6}
    if has("VERCEL_TEAM_ID"):
        params["teamId"] = os.environ["VERCEL_TEAM_ID"]
    r = requests.get("https://api.vercel.com/v7/deployments", params=params,
                     headers={"Authorization": f"Bearer {os.environ['VERCEL_TOKEN'].strip()}"}, timeout=20)
    r.raise_for_status()
    deps = []
    for d in r.json().get("deployments", []):
        meta = d.get("meta") or {}
        deps.append({"created": d.get("created"), "state": d.get("readyState") or d.get("state"),
                     "target": d.get("target"), "url": d.get("url"),
                     "commit": (meta.get("githubCommitMessage") or "").splitlines()[0][:90] if meta else "",
                     "inspector": d.get("inspectorUrl")})
    return {"deployments": deps}


def from_anthropic() -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    r = requests.get("https://api.anthropic.com/v1/organizations/cost_report",
                     params={"starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 31},
                     headers={"x-api-key": os.environ["ANTHROPIC_ADMIN_KEY"].strip(),
                              "anthropic-version": "2023-06-01"}, timeout=30)
    r.raise_for_status()
    days = []
    for b in r.json().get("data", []):
        cents = sum(float(x.get("amount") or 0) for x in b.get("results", []))  # amounts are in cents
        days.append({"day": b.get("starting_at", "")[:10], "usd": round(cents / 100, 4)})
    return {"days": days, "month_usd": round(sum(d["usd"] for d in days), 2)}


def short(e: Exception) -> str:
    if isinstance(e, requests.HTTPError) and e.response is not None:
        code = e.response.status_code
        hint = {401: "the key was rejected – check it in keys.txt", 403: "the key has no access to this",
                404: "not found – check the project name or id"}.get(code, "")
        return f"HTTP {code}{' – ' + hint if hint else ''}"
    if isinstance(e, requests.ConnectionError):
        return "no internet connection to the service (or it is down)"
    if isinstance(e, requests.Timeout):
        return "the service took too long to answer"
    if isinstance(e, KeyError):
        return f"missing in keys.txt: {e.args[0]}"
    return str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__


SOURCES = {
    "database": ("DATABASE_URL", from_database),
    "supabase": ("SUPABASE_ACCESS_TOKEN", from_supabase),
    "vercel": ("VERCEL_TOKEN", from_vercel),
    "anthropic": ("ANTHROPIC_ADMIN_KEY", from_anthropic),
}


def collect() -> dict:
    def one(name):
        key, fn = SOURCES[name]
        if not has(key):
            return name, {"missing_key": key}
        try:
            return name, fn()
        except BaseException as e:      # connect() raises SystemExit with a readable message
            return name, {"error": short(e) if isinstance(e, Exception) else str(e)}
    with ThreadPoolExecutor(len(SOURCES)) as pool:
        data = dict(pool.map(one, SOURCES))
    data["config"] = {"price_in": PRICE_IN, "price_out": PRICE_OUT, "limits": LIMITS,
                      "project_ref": PROJECT_REF, "site_url": SITE_URL}
    data["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return data


# ------------------------------------------------------------------ web server
_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def cached(fresh: bool) -> dict:
    with _lock:
        if fresh or _cache["data"] is None or time.time() - _cache["at"] > 60:
            _cache["data"], _cache["at"] = collect(), time.time()
        return _cache["data"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # only this computer, by name: blocks other websites from reading the data through the browser
        if self.headers.get("Host") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return self.send(403, b"forbidden", "text/plain")
        path, _, query = self.path.partition("?")
        if path == "/":
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/data":
            body = json.dumps(cached("fresh=1" in query), ensure_ascii=False, default=str).encode("utf-8")
            return self.send(200, body, "application/json; charset=utf-8")
        self.send(404, b"not found", "text/plain")

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):       # keep the window quiet
        pass


def main() -> None:
    load_keys()
    url = f"http://127.0.0.1:{PORT}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"The dashboard is already running. Opening {url}")
        webbrowser.open(url)
        return
    print(f"Dashboard running at {url}\nLeave this window open while you use it. Close it to stop.")
    for name, (key, _) in SOURCES.items():
        print(f"  {name:<10} {'ready' if has(key) else 'not set up (' + key + ' missing in keys.txt)'}")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
