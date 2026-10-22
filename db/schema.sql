-- Il_legislation — database schema (Supabase / Postgres 15+)
-- Run once in the Supabase SQL editor.
--
-- Model:
--   laws        one row per published law version from Knesset OData KNS_Law
--               (we index the consolidated versions, SubTypeDesc contains "משולב")
--   documents   one row per official PDF (KNS_DocumentLaw), with hash + download time
--   sections    one row per section (סעיף) of the extracted text, searchable
--
-- Search: hybrid = Hebrew-aware keyword search (trigram + 'simple' full text,
-- since Postgres has no Hebrew dictionary) fused with vector similarity (RRF).

-- Supabase keeps extensions in their own schema
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create extension if not exists pg_trgm with schema extensions;

-- ---------------------------------------------------------------- laws
create table if not exists laws (
    law_id                  bigint primary key,      -- KNS_Law.LawID
    name                    text not null,           -- KNS_Law.Name
    type_desc               text,                    -- KNS_Law.TypeDesc
    sub_type_desc           text,                    -- KNS_Law.SubTypeDesc (נוסח משולב ...)
    knesset_num             int,
    publication_date        timestamptz,
    publication_series_desc text,
    magazine_number         text,
    -- registry info from KNS_IsraelLaw, matched by name (no official join exists)
    israel_law_id           bigint,
    is_basic_law            boolean,
    validity_desc           text,                    -- KNS_IsraelLaw.LawValidityDesc
    latest_publication_date timestamptz,
    odata_url               text not null,
    raw                     jsonb not null,          -- the untouched OData row, for audit
    fetched_at              timestamptz not null default now()
);

-- ---------------------------------------------------------------- documents
create table if not exists documents (
    document_law_id   bigint primary key,            -- KNS_DocumentLaw.DocumentLawID
    law_id            bigint not null references laws(law_id) on delete cascade,
    group_type_desc   text,
    application_desc  text,
    file_url          text not null,                 -- official fs.knesset.gov.il URL
    sha256            text,                          -- hash of the downloaded PDF
    byte_size         int,
    downloaded_at     timestamptz,
    extraction_status text,                          -- ok | reversed_fixed | empty | failed
    extraction_note   text,
    last_updated_date timestamptz,                   -- KNS_DocumentLaw.LastUpdatedDate
    raw               jsonb not null
);
create index if not exists documents_law_idx on documents(law_id);

-- ---------------------------------------------------------------- sections
create table if not exists sections (
    id              bigserial primary key,
    law_id          bigint not null references laws(law_id) on delete cascade,
    document_law_id bigint not null references documents(document_law_id) on delete cascade,
    seq             int not null,                    -- order inside the law
    section_num     text,                            -- "7א", "12" ... null for preamble
    heading         text,                            -- marginal heading, e.g. "הגדרות"
    chapter         text,                            -- "פרק ב': ..." if known
    content         text not null,                   -- verbatim extracted text
    content_hash    text not null,
    embedding       extensions.vector(1024),
    fts             tsvector generated always as (to_tsvector('simple', coalesce(heading,'') || ' ' || content)) stored,
    unique (document_law_id, seq)
);
create index if not exists sections_law_idx  on sections(law_id);
create index if not exists sections_fts_idx  on sections using gin(fts);
create index if not exists sections_trgm_idx on sections using gin(content extensions.gin_trgm_ops);
create index if not exists sections_vec_idx  on sections using hnsw (embedding extensions.vector_cosine_ops);

-- ---------------------------------------------------------------- ingestion log
create table if not exists ingest_runs (
    id          bigserial primary key,
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    laws_seen   int, laws_changed int, sections_written int,
    errors      jsonb default '[]'::jsonb
);

-- ---------------------------------------------------------------- hybrid search
-- Reciprocal-rank fusion of keyword and vector results. Returns verbatim
-- section text plus everything needed to cite it.
create or replace function search_sections(
    query_text      text,
    query_embedding extensions.vector(1024),
    match_count     int default 8,
    law_filter      bigint default null,
    rrf_k           int default 50
)
returns table (
    section_id bigint, law_id bigint, law_name text, section_num text, heading text,
    chapter text, content text, validity_desc text, publication_date timestamptz,
    file_url text, score float
)
language sql stable
set search_path = public, extensions
as $$
with kw as (
    select s.id,
           row_number() over (order by
               ts_rank(s.fts, websearch_to_tsquery('simple', query_text)) +
               similarity(s.content, query_text) desc) as rnk
    from sections s
    where (law_filter is null or s.law_id = law_filter)
      and (s.fts @@ websearch_to_tsquery('simple', query_text)
           or s.content % query_text)
    limit match_count * 4
),
vec as (
    select s.id,
           row_number() over (order by s.embedding <=> query_embedding) as rnk
    from sections s
    where (law_filter is null or s.law_id = law_filter)
      and s.embedding is not null
    order by s.embedding <=> query_embedding
    limit match_count * 4
),
fused as (
    select coalesce(kw.id, vec.id) as id,
           coalesce(1.0 / (rrf_k + kw.rnk), 0) + coalesce(1.0 / (rrf_k + vec.rnk), 0) as score
    from kw full outer join vec on kw.id = vec.id
)
select s.id, s.law_id, l.name, s.section_num, s.heading, s.chapter, s.content,
       l.validity_desc, l.publication_date, d.file_url, f.score
from fused f
join sections s  on s.id = f.id
join laws l      on l.law_id = s.law_id
join documents d on d.document_law_id = s.document_law_id
order by f.score desc
limit match_count;
$$;

-- Fetch one section with its neighbours (definitions / exceptions often sit next door).
create or replace function get_section(p_law_id bigint, p_section_num text, p_context int default 1)
returns table (seq int, section_num text, heading text, chapter text, content text, file_url text)
language sql stable
set search_path = public, extensions
as $$
with target as (
    select document_law_id, seq from sections
    where law_id = p_law_id and section_num = p_section_num
    order by seq limit 1
)
select s.seq, s.section_num, s.heading, s.chapter, s.content, d.file_url
from sections s
join target t on s.document_law_id = t.document_law_id
join documents d on d.document_law_id = s.document_law_id
where s.seq between t.seq - p_context and t.seq + p_context
order by s.seq;
$$;

-- Locked down: no public (anon) access at all. Only our backend, connecting
-- with the database credentials, reads this data.
revoke execute on function search_sections(text, extensions.vector, int, bigint, int) from public, anon, authenticated;
revoke execute on function get_section(bigint, text, int) from public, anon, authenticated;
alter table laws enable row level security;
alter table documents enable row level security;
alter table sections enable row level security;
alter table ingest_runs enable row level security;
