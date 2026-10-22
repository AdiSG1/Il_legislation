"""Write laws, documents and sections to Postgres (Supabase)."""

from __future__ import annotations

import json
import os

import psycopg


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=False)


def _vec(v: list[float] | None) -> str | None:
    return None if v is None else "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def upsert_law(cur, row: dict, odata_url: str, registry: dict | None) -> None:
    reg = registry or {}
    cur.execute(
        """
        insert into laws (law_id, name, type_desc, sub_type_desc, knesset_num, publication_date,
                          publication_series_desc, magazine_number, israel_law_id, is_basic_law,
                          validity_desc, latest_publication_date, odata_url, raw, fetched_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
        on conflict (law_id) do update set
          name=excluded.name, type_desc=excluded.type_desc, sub_type_desc=excluded.sub_type_desc,
          knesset_num=excluded.knesset_num, publication_date=excluded.publication_date,
          publication_series_desc=excluded.publication_series_desc, magazine_number=excluded.magazine_number,
          israel_law_id=excluded.israel_law_id, is_basic_law=excluded.is_basic_law,
          validity_desc=excluded.validity_desc, latest_publication_date=excluded.latest_publication_date,
          odata_url=excluded.odata_url, raw=excluded.raw, fetched_at=now()
        """,
        (
            row["LawID"], row.get("Name") or "", row.get("TypeDesc"), row.get("SubTypeDesc"),
            row.get("KnessetNum"), row.get("PublicationDate"), row.get("PublicationSeriesDesc"),
            row.get("MagazineNumber"), reg.get("IsraelLawID") or reg.get("Id"), reg.get("IsBasicLaw"),
            reg.get("LawValidityDesc"), reg.get("LatestPublicationDate"), odata_url, json.dumps(row, ensure_ascii=False),
        ),
    )


def document_hash(cur, document_law_id: int) -> str | None:
    cur.execute("select sha256 from documents where document_law_id=%s", (document_law_id,))
    r = cur.fetchone()
    return r[0] if r else None


def upsert_document(cur, row: dict, sha256: str, size: int, status: str, note: str) -> None:
    cur.execute(
        """
        insert into documents (document_law_id, law_id, group_type_desc, application_desc, file_url,
                               sha256, byte_size, downloaded_at, extraction_status, extraction_note,
                               last_updated_date, raw)
        values (%s,%s,%s,%s,%s,%s,%s, now(), %s,%s,%s,%s)
        on conflict (document_law_id) do update set
          file_url=excluded.file_url, sha256=excluded.sha256, byte_size=excluded.byte_size,
          downloaded_at=now(), extraction_status=excluded.extraction_status,
          extraction_note=excluded.extraction_note, last_updated_date=excluded.last_updated_date,
          raw=excluded.raw
        """,
        (
            row["DocumentLawID"], row["LawID"], row.get("GroupTypeDesc"), row.get("ApplicationDesc"),
            row["FilePath"], sha256, size, status, note, row.get("LastUpdatedDate"),
            json.dumps(row, ensure_ascii=False),
        ),
    )


def replace_sections(cur, law_id: int, document_law_id: int, sections, embeddings) -> int:
    cur.execute("delete from sections where document_law_id=%s", (document_law_id,))
    for s, e in zip(sections, embeddings):
        cur.execute(
            """insert into sections (law_id, document_law_id, seq, section_num, heading, chapter,
                                     content, content_hash, embedding)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s::extensions.vector)""",
            (law_id, document_law_id, s.seq, s.section_num, s.heading, s.chapter,
             s.content, s.content_hash, _vec(e)),
        )
    return len(sections)
