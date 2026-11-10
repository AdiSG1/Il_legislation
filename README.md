# Il_legislation

A public Hebrew Q&A agent on Israeli legislation. **Its only source is the
official Knesset legislation database** (Knesset OData API + the official PDFs
on fs.knesset.gov.il). No news, no social media, no web search.

## Status

| Part | State |
|---|---|
| Database schema + hybrid search (`db/schema.sql`) | ready |
| Ingestion: Knesset list + amendments → Wikisource text (verified) → sections → DB (`ingest/`) | ready, first real run pending |
| Agent: Supabase Edge Function `ask` (`supabase/functions/ask/`) - Hebrew prompt, 4 read-only tools, verbatim-quote and number checks, limits, audit log | live |
| Website (`web/index.html`) - Hebrew RTL page | ready to host |
| Test set (`eval/`) - 110 questions, automatic scoring | ready |
| Weekly refresh (`refresh.bat` + Windows Task Scheduler) | ready |
| Backend API, website, scheduled refresh | later |

## Data sources

1. **Knesset OData** (`KNS_IsraelLaw`) - which laws exist and are in force.
2. **Knesset website legislation API** - for each law, the official amendment
   list (with the official PDFs and the Knesset's own summaries) and the
   "לחוק המלא" (full law) link shown on the law's Knesset page.
3. **Hebrew Wikisource - only at the page the Knesset links to.** The Knesset
   does not publish an up-to-date text of its laws; its law pages send visitors
   to Wikisource for the full text.

A text is used only if it cites **every** official amendment PDF from the
Knesset's list (`verified`). Otherwise it is marked `outdated` and not indexed.
Each indexed text is pinned to its Wikisource revision id.

**Location:** the Knesset site may block requests from outside Israel. Run the
ingestion from a computer in Israel.

## First run (on your computer)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt          # works on any recent Python, including 3.14+
python -m pytest -q

# 1. look at the text quality of a few laws - no accounts needed
python -m ingest.run --dry-run --name "הבחירות לכנסת"
python -m ingest.run --dry-run --limit 20
#    -> out/<IsraelLawID>.txt (sections) and .json (status, amendments, source revision)

# 2. create the database: paste db/schema.sql into Supabase -> SQL Editor -> Run

# 3. full ingestion
export DATABASE_URL="postgresql://..."     # Supabase -> Connect -> Session pooler
export VOYAGE_API_KEY="..."
python -m ingest.run --limit 20            # then without --limit for everything
```


## Website

`web/index.html` is a single static page. Host it anywhere static, e.g. Vercel:
Import the GitHub repo -> Root Directory `web` -> Framework preset "Other" -> Deploy.
After it has a public address, set the Supabase secret `ALLOWED_ORIGINS` to that address
(e.g. `https://your-site.vercel.app`) so only your site can call the agent from a browser.

## Test set

```bash
python -m eval.run_eval              # all 110 questions, about 2 hours (paced to the agent's limit)
python -m eval.run_eval --cat opinion
```
Results: `eval/results/<time>.md` (report) and `.jsonl` (every answer in full).

## Weekly refresh

`refresh.bat` runs a full ingestion and writes `logs/refresh-<time>.log`. A full run also
marks laws that left the Knesset's in-force list and removes their text. Schedule it with:

```
schtasks /Create /SC WEEKLY /D SAT /ST 03:00 /TN "Il_legislation weekly refresh" /TR "\"C:\path\to\Il_legislation\refresh.bat\"" /F
```
