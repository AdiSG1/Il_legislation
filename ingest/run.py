"""Ingestion.

For every law in force (Knesset OData, KNS_IsraelLaw):
  1. ask the Knesset website for the law's official amendment list and its
     "לחוק המלא" link,
  2. if that link goes to Hebrew Wikisource, fetch that page (pinned revision),
  3. check that every official amendment PDF is cited by the text
       verified  -> index the text
       outdated  -> do NOT index; record which amendments are missing
  4. split into sections and store (with embeddings).

Laws with no Knesset "full law" link are recorded as no_text.

Examples
  python -m ingest.run --dry-run --name "הבחירות לכנסת"      # no accounts needed, writes ./out
  python -m ingest.run --dry-run --limit 10
  python -m ingest.run                                        # needs DATABASE_URL, VOYAGE_API_KEY
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import traceback

from .knesset_site import KnessetSite, verify
from .odata import GeoBlockedError, KnessetOData
from .wikisource import Wikisource, title_from_url
from .wikitext import parse_law

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out"


KEY_FILES = (ROOT / "keys.txt", ROOT / ".env")   # local only, both are in .gitignore


def load_env_file() -> None:
    """Read NAME=value lines from keys.txt (or .env) into the environment."""
    import os
    lines = [l for p in KEY_FILES if p.exists() for l in p.read_text(encoding="utf-8-sig").splitlines()]
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def process(reg: dict, site: KnessetSite, wiki: Wikisource):
    """Returns (item, status, note, page, parsed)."""
    item = site.law_item(reg["IsraelLawID"])
    if not item.open_book_url:
        return item, "no_text", {"reason": "the Knesset page has no full-law link"}, None, None
    title = title_from_url(item.open_book_url)
    if not title:
        return item, "no_text", {"reason": "full-law link is not Hebrew Wikisource", "url": item.open_book_url}, None, None
    page = wiki.page(title)
    parsed = parse_law(page.wikitext)
    if parsed.israel_law_id and parsed.israel_law_id != reg["IsraelLawID"]:
        return item, "error", {"reason": "Wikisource page belongs to a different law",
                               "page_law_id": parsed.israel_law_id}, page, parsed
    status, note = verify(item, parsed.cited_pdf_ids)
    note.update({"wikisource_revision": page.revision_id, "revision_time": page.revision_time})
    return item, status, note, page, parsed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="only laws whose name contains this text")
    ap.add_argument("--limit", type=int, default=0, help="stop after N laws (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="no database or embeddings; write ./out")
    ap.add_argument("--force", action="store_true", help="re-index even if the text did not change")
    args = ap.parse_args(argv)

    odata, site, wiki = KnessetOData(), KnessetSite(), Wikisource()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = cur = run_id = None
    if not args.dry_run:
        import os
        load_env_file()
        missing = [k for k in ("DATABASE_URL", "VOYAGE_API_KEY") if not os.environ.get(k)]
        if missing:
            print(f"Missing {', '.join(missing)}. Put them in the file {KEY_FILES[0]} "
                  "(one per line: NAME=value).", file=sys.stderr)
            return 2
        from . import store
        from .embed import embed_documents
        conn = store.connect()
        cur = conn.cursor()
        cur.execute("insert into ingest_runs default values returning id")
        run_id = cur.fetchone()[0]
        conn.commit()

    seen = changed = written = 0
    counts: dict[str, int] = {}
    errors: list[dict] = []
    try:
        for reg in odata.in_force_laws(args.name):
            if args.limit and seen >= args.limit:
                break
            seen += 1
            law_id, name = reg["IsraelLawID"], (reg.get("Name") or "").strip()
            try:
                item, status, note, page, parsed = process(reg, site, wiki)
                counts[status] = counts.get(status, 0) + 1
                n_secs = len(parsed.sections) if parsed else 0
                print(f"{status:9} {law_id} {name} ({n_secs} sections)")
                if status == "outdated":
                    for m in note["missing"][:3]:
                        print(f"          missing: {m['name']} ({(m['date'] or '')[:10]})")

                if args.dry_run:
                    (OUT_DIR / f"{law_id}.json").write_text(json.dumps({
                        "registry": reg, "status": status, "note": note,
                        "knesset_page": item.page_url, "open_book_url": item.open_book_url,
                        "amendments": [a.__dict__ for a in item.amendments],
                        "wikisource": page and {"title": page.title, "revision": page.revision_id,
                                                "time": page.revision_time, "permalink": page.permalink},
                        "sections": [s.__dict__ for s in parsed.sections] if parsed else [],
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    if parsed:
                        (OUT_DIR / f"{law_id}.txt").write_text(
                            "\n\n".join(f"[{s.anchor or ''} | {s.chapter or ''}]\n{s.content}" for s in parsed.sections),
                            encoding="utf-8")
                    continue

                cited = parsed.cited_pdf_ids if parsed else set()
                store.upsert_law(cur, reg, item, status, note)
                store.replace_amendments(cur, law_id, item.amendments, cited)
                if status == "verified":
                    sha = hashlib.sha256(page.wikitext.encode("utf-8")).hexdigest()
                    if args.force or store.stored_text_hash(cur, law_id) != sha:
                        secs = parsed.sections
                        emb = embed_documents([f"{name}\n{s.chapter or ''}\n{s.content}" for s in secs]) if secs else []
                        store.upsert_text(cur, law_id, page, sha)
                        written += store.replace_sections(cur, law_id, secs, emb)
                        changed += 1
                else:
                    store.drop_text(cur, law_id)   # never answer from an unverified text
                conn.commit()
            except GeoBlockedError:
                raise
            except Exception as exc:  # one bad law must not stop the run
                if conn is not None:
                    conn.rollback()
                counts["error"] = counts.get("error", 0) + 1
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

    print(f"\nlaws seen {seen}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if not args.dry_run:
        print(f"texts (re)indexed {changed}, sections written {written}")
    for e in errors[:20]:
        print("  !", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
