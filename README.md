# Il_legislation

A public Hebrew Q&A agent on Israeli legislation. **Its only source is the
official Knesset legislation database** (Knesset OData API + the official PDFs
on fs.knesset.gov.il). No news, no social media, no web search.

## Status

| Part | State |
|---|---|
| Database schema + hybrid search (`db/schema.sql`) | ready |
| Ingestion: Knesset list + amendments → Wikisource text (verified) → sections → DB (`ingest/`) | ready, first real run pending |
| Agent (system prompt, tools, quote check) | next |
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

