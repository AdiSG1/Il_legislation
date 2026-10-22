# Il_legislation

A public Hebrew Q&A agent on Israeli legislation. **Its only source is the
official Knesset legislation database** (Knesset OData API + the official PDFs
on fs.knesset.gov.il). No news, no social media, no web search.

## Status

| Part | State |
|---|---|
| Database schema + hybrid search (`db/schema.sql`) | ready |
| Ingestion: OData → PDF → text → sections → DB (`ingest/`) | ready, needs a first real run |
| Agent (system prompt, tools, quote check) | next |
| Backend API, website, scheduled refresh | later |

## Data source (official only)

`https://knesset.gov.il/Odata/ParliamentInfo.svc`

| Entity | Used for |
|---|---|
| `KNS_Law` | published law versions; we index consolidated texts (`SubTypeDesc` contains "משולב") |
| `KNS_DocumentLaw` | the official PDF of each version (`FilePath`, `LawID`) |
| `KNS_IsraelLaw` | registry: in force / repealed (`LawValidityDesc`), Basic Law flag |

There is no official ID link between `KNS_Law` and `KNS_IsraelLaw`; we match
them by exact name only and leave the status empty when the match is unclear.

**Location:** the Knesset site may block requests from outside Israel. Run the
ingestion from a computer in Israel (or a cloud region in Israel).

## First run (on your computer)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q

# 1. look at the text quality of a few laws - no accounts needed
python -m ingest.run --dry-run --name "שירות הביטחון הכללי"
python -m ingest.run --dry-run --name "חוק-יסוד: הכנסת"
#    -> open out/<LawID>.txt and out/<LawID>.json and check the Hebrew and the section split

# 2. create the database: paste db/schema.sql into Supabase -> SQL Editor -> Run

# 3. full ingestion
export DATABASE_URL="postgresql://..."     # Supabase -> Connect -> Session pooler
export VOYAGE_API_KEY="..."
python -m ingest.run --limit 20            # then without --limit for everything
```

Every PDF is kept in `data/pdfs/` with its SHA-256 in the database, so each
answer can be traced to the exact official file.
