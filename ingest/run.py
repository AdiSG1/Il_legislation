"""Ingestion: Knesset OData -> official PDFs -> Hebrew text -> sections -> Supabase.

Examples
  # look at a few laws without touching the database (writes ./out/*.json for review)
  python -m ingest.run --dry-run --name "שירות הביטחון הכללי"
  python -m ingest.run --dry-run --limit 5

  # full run (needs DATABASE_URL and VOYAGE_API_KEY)
  python -m ingest.run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import traceback

from .extract import extract_pdf
from .odata import GeoBlockedError, KnessetOData
from .sections import split_sections

ROOT = pathlib.Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "pdfs"
OUT_DIR = ROOT / "out"


def pick_document(docs: list[dict]) -> dict | None:
    pdfs = [d for d in docs if (d.get("FilePath") or "").lower().endswith(".pdf")]
    pdfs.sort(key=lambda d: d.get("LastUpdatedDate") or "", reverse=True)
    return pdfs[0] if pdfs else None


def registry_match(api: KnessetOData, name: str) -> dict | None:
    """KNS_IsraelLaw has no official join to KNS_Law, so match on the exact name only."""
    try:
        rows = api.registry_search(name)
    except Exception:
        return None
    exact = [r for r in rows if (r.get("Name") or "").strip() == name.strip()]
    return exact[0] if len(exact) == 1 else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="only laws whose name contains this text")
    ap.add_argument("--limit", type=int, default=0, help="stop after N laws (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="no database, no embeddings; write ./out/*.json")
    ap.add_argument("--force", action="store_true", help="re-index even if the PDF did not change")
    args = ap.parse_args(argv)

    api = KnessetOData()
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = cur = None
    if not args.dry_run:
        from . import store
        from .embed import embed_documents
        conn = store.connect()
        cur = conn.cursor()
        cur.execute("insert into ingest_runs default values returning id")
        run_id = cur.fetchone()[0]
        conn.commit()

    seen = changed = written = 0
    errors: list[dict] = []
    try:
        for law in api.consolidated_laws(args.name):
            if args.limit and seen >= args.limit:
                break
            seen += 1
            law_id, name = law["LawID"], (law.get("Name") or "").strip()
            try:
                doc = pick_document(api.law_documents(law_id))
                if not doc:
                    errors.append({"law_id": law_id, "name": name, "error": "no PDF document"})
                    continue

                pdf = api.download(doc["FilePath"])
                sha = hashlib.sha256(pdf).hexdigest()
                (PDF_DIR / f"{doc['DocumentLawID']}_{sha[:12]}.pdf").write_bytes(pdf)

                if cur is not None and not args.force and store.document_hash(cur, doc["DocumentLawID"]) == sha:
                    print(f"= {law_id} {name} (unchanged)")
                    continue

                ext = extract_pdf(pdf)
                secs = split_sections(ext.text) if ext.text else []
                print(f"+ {law_id} {name}: {ext.status}, {len(secs)} sections")

                if args.dry_run:
                    (OUT_DIR / f"{law_id}.json").write_text(json.dumps({
                        "law": law, "document": doc, "sha256": sha,
                        "extraction": {"status": ext.status, "note": ext.note},
                        "sections": [s.__dict__ for s in secs],
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    (OUT_DIR / f"{law_id}.txt").write_text(ext.text, encoding="utf-8")
                    continue

                reg = registry_match(api, name)
                embeddings = embed_documents(
                    [f"{name}\n{s.heading or ''}\n{s.content}" for s in secs]) if secs else []
                store.upsert_law(cur, law, api.entity_url("KNS_Law", law_id), reg)
                store.upsert_document(cur, doc, sha, len(pdf), ext.status, ext.note)
                written += store.replace_sections(cur, law_id, doc["DocumentLawID"], secs, embeddings)
                conn.commit()
                changed += 1
            except GeoBlockedError:
                raise
            except Exception as exc:  # one bad law must not stop the run
                if conn is not None:
                    conn.rollback()
                errors.append({"law_id": law_id, "name": name, "error": f"{type(exc).__name__}: {exc}"})
                traceback.print_exc()
    except GeoBlockedError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        errors.append({"error": str(exc)})
    finally:
        if cur is not None:
            cur.execute(
                "update ingest_runs set finished_at=now(), laws_seen=%s, laws_changed=%s, "
                "sections_written=%s, errors=%s where id=%s",
                (seen, changed, written, json.dumps(errors, ensure_ascii=False), run_id),
            )
            conn.commit()
            conn.close()

    print(f"\nlaws seen {seen}, changed {changed}, sections written {written}, errors {len(errors)}")
    for e in errors[:20]:
        print("  !", e)
    return 1 if errors and not changed and not args.dry_run else 0


if __name__ == "__main__":
    sys.exit(main())
