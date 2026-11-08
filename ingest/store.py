"""Write laws, official amendments, texts and sections to Postgres (Supabase).

Uses pg8000, a pure-Python driver, so it installs on any Python version and
on Windows without extra system libraries.
"""

from __future__ import annotations

import json
import os
import ssl
from urllib.parse import unquote, urlparse

import pg8000.dbapi


def connect():
    """Connect from DATABASE_URL (postgresql://user:password@host:port/dbname)."""
    u = urlparse(os.environ["DATABASE_URL"])
    local = u.hostname in ("localhost", "127.0.0.1")
    return pg8000.dbapi.connect(
        user=unquote(u.username or "postgres"),
        password=unquote(u.password) if u.password else None,
        host=u.hostname,
        port=u.port or 5432,
        database=(u.path or "/postgres").lstrip("/") or "postgres",
        ssl_context=None if local else ssl.create_default_context(),
    )


def _vec(v: list[float] | None) -> str | None:
    return None if v is None else "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def stored_text_hash(cur, israel_law_id: int) -> str | None:
    cur.execute("select sha256 from texts where israel_law_id=%s", (israel_law_id,))
    r = cur.fetchone()
    return r[0] if r else None


def upsert_law(cur, registry: dict, item, status: str, note: dict | None) -> None:
    raw = {"odata": registry, "site": item.raw_general if item else None}
    cur.execute(
        """
        insert into laws (israel_law_id, name, is_basic_law, validity_desc, publication_date,
                          latest_publication_date, knesset_url, open_book_url, text_status, text_note,
                          raw, fetched_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
        on conflict (israel_law_id) do update set
          name=excluded.name, is_basic_law=excluded.is_basic_law, validity_desc=excluded.validity_desc,
          publication_date=excluded.publication_date, latest_publication_date=excluded.latest_publication_date,
          knesset_url=excluded.knesset_url, open_book_url=excluded.open_book_url,
          text_status=excluded.text_status, text_note=excluded.text_note, raw=excluded.raw, fetched_at=now()
        """,
        (
            registry["IsraelLawID"], (registry.get("Name") or "").strip(), registry.get("IsBasicLaw"),
            registry.get("LawValidityDesc"), registry.get("PublicationDate"),
            (item.latest_publication_date if item else None) or registry.get("LatestPublicationDate"),
            f"https://main.knesset.gov.il/apps/legislation/main/laws/{registry['IsraelLawID']}",
            item.open_book_url if item else None, status,
            json.dumps(note, ensure_ascii=False) if note is not None else None,
            json.dumps(raw, ensure_ascii=False),
        ),
    )


def replace_amendments(cur, israel_law_id: int, amendments, cited_ids: set[str]) -> None:
    cur.execute("delete from amendments where israel_law_id=%s", (israel_law_id,))
    for a in amendments:
        cur.execute(
            """insert into amendments (israel_law_id, number, name, kind, publication_date, series,
                                       magazine, page, pdf_url, summary, in_text)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (israel_law_id, a.number, a.name, a.kind, a.publication_date, a.series, a.magazine,
             a.page, a.pdf_url, a.summary, (a.pdf_id in cited_ids) if a.pdf_id else None),
        )


def upsert_text(cur, israel_law_id: int, page, sha256: str) -> None:
    cur.execute(
        """
        insert into texts (israel_law_id, source, title, revision_id, revision_time, permalink, sha256, fetched_at)
        values (%s,'wikisource',%s,%s,%s,%s,%s, now())
        on conflict (israel_law_id) do update set
          title=excluded.title, revision_id=excluded.revision_id, revision_time=excluded.revision_time,
          permalink=excluded.permalink, sha256=excluded.sha256, fetched_at=now()
        """,
        (israel_law_id, page.title, page.revision_id, page.revision_time, page.permalink, sha256),
    )


def replace_sections(cur, israel_law_id: int, sections, embeddings) -> int:
    cur.execute("delete from sections where israel_law_id=%s", (israel_law_id,))
    for s, e in zip(sections, embeddings):
        cur.execute(
            """insert into sections (israel_law_id, seq, section_num, anchor, heading, chapter,
                                     amended_by, content, content_hash, embedding)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::extensions.vector)""",
            (israel_law_id, s.seq, s.section_num, s.anchor, s.heading, s.chapter,
             s.amended_by, s.content, s.content_hash, _vec(e)),
        )
    return len(sections)


def drop_text(cur, israel_law_id: int) -> None:
    """Remove indexed text for a law whose text can no longer be used."""
    cur.execute("delete from sections where israel_law_id=%s", (israel_law_id,))
    cur.execute("delete from texts where israel_law_id=%s", (israel_law_id,))
