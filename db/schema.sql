-- Il_legislation — database schema v2 (Supabase / Postgres 15+)
--
-- Sources, in order of authority:
--   1. Knesset OData (KNS_IsraelLaw)          which laws exist and are in force
--   2. Knesset website legislation API        the official amendment list for each law,
--                                             and the "לחוק המלא" link the Knesset shows
--   3. Hebrew Wikisource, ONLY at that link   the current full text, pinned to a revision
--
-- Each text is checked against the official amendment list before it is used.
--
-- Search: hybrid = keyword (trigram + 'simple' full text; Postgres has no Hebrew
-- dictionary) fused with vector similarity (reciprocal rank fusion).

create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create extension if not exists pg_trgm with schema extensions;

-- ---------------------------------------------------------------- laws
create table if not exists laws (
    israel_law_id           bigint primary key,       -- KNS_IsraelLaw.IsraelLawID
    name                    text not null,
    is_basic_law            boolean,
    validity_desc           text,                     -- תקף ...
    publication_date        timestamptz,
    latest_publication_date timestamptz,
    knesset_url             text not null,            -- the law's page on main.knesset.gov.il
    open_book_url           text,                     -- the "לחוק המלא" link from that page
    text_status             text not null,            -- verified | outdated | no_text | error
    text_note               jsonb,
    raw                     jsonb not null,           -- untouched OData + site data, for audit
    fetched_at              timestamptz not null default now()
);

-- ---------------------------------------------------------------- official amendments
create table if not exists amendments (
    id               bigserial primary key,
    israel_law_id    bigint not null references laws(israel_law_id) on delete cascade,
    number           int,
    name             text not null,
    kind             text,                            -- ישיר / עקיף
    publication_date timestamptz,
    series           text,
    magazine         text,
    page             text,
    pdf_url          text,
    summary          text,                            -- the Knesset's own summary
    in_text          boolean                          -- cited by the indexed full text?
);
create index if not exists amendments_law_idx on amendments(israel_law_id);

-- ---------------------------------------------------------------- full-text sources
create table if not exists texts (
    israel_law_id  bigint primary key references laws(israel_law_id) on delete cascade,
    source         text not null default 'wikisource',
    title          text not null,
    revision_id    bigint not null,
    revision_time  timestamptz not null,
    permalink      text not null,
    sha256         text not null,
    fetched_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------- sections
create table if not exists sections (
    id            bigserial primary key,
    israel_law_id bigint not null references laws(israel_law_id) on delete cascade,
    seq           int not null,
    section_num   text,                               -- "7א", "57", "(1)" in a schedule
    anchor        text,                               -- "סעיף 57", "תוספת פרט 1"
    heading       text,
    chapter       text,                               -- "פרק ה׳: ... / סימן ..."
    amended_by    text,                               -- amendment years noted on the section
    content       text not null,                      -- verbatim text
    content_hash  text not null,
    embedding     extensions.vector(1024),
    fts           tsvector generated always as (to_tsvector('simple', coalesce(heading,'') || ' ' || content)) stored,
    unique (israel_law_id, seq)
);
create index if not exists sections_law_idx  on sections(israel_law_id);
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

-- ---------------------------------------------------------------- search
create or replace function search_sections(
    query_text      text,
    query_embedding extensions.vector(1024),
    match_count     int default 8,
    law_filter      bigint default null,
    rrf_k           int default 50
)
returns table (
    section_id bigint, israel_law_id bigint, law_name text, section_num text, anchor text,
    heading text, chapter text, content text, text_status text,
    latest_publication_date timestamptz, knesset_url text, permalink text, score float
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
    where (law_filter is null or s.israel_law_id = law_filter)
      and (s.fts @@ websearch_to_tsquery('simple', query_text)
           or s.content % query_text)
    limit match_count * 4
),
vec as (
    select s.id,
           row_number() over (order by s.embedding <=> query_embedding) as rnk
    from sections s
    where (law_filter is null or s.israel_law_id = law_filter)
      and s.embedding is not null
    order by s.embedding <=> query_embedding
    limit match_count * 4
),
fused as (
    select coalesce(kw.id, vec.id) as id,
           coalesce(1.0 / (rrf_k + kw.rnk), 0) + coalesce(1.0 / (rrf_k + vec.rnk), 0) as score
    from kw full outer join vec on kw.id = vec.id
)
select s.id, s.israel_law_id, l.name, s.section_num, s.anchor, s.heading, s.chapter, s.content,
       l.text_status, l.latest_publication_date, l.knesset_url, t.permalink, f.score
from fused f
join sections s on s.id = f.id
join laws l     on l.israel_law_id = s.israel_law_id
join texts t    on t.israel_law_id = s.israel_law_id
order by f.score desc
limit match_count;
$$;

-- One section with its neighbours (definitions / exceptions often sit next door).
create or replace function get_section(p_law_id bigint, p_section_num text, p_context int default 1)
returns table (seq int, section_num text, anchor text, heading text, chapter text, content text, permalink text)
language sql stable
set search_path = public, extensions
as $$
with target as (
    select seq from sections
    where israel_law_id = p_law_id and section_num = p_section_num
    order by seq limit 1
)
select s.seq, s.section_num, s.anchor, s.heading, s.chapter, s.content, t.permalink
from sections s
join target x on true
join texts t on t.israel_law_id = s.israel_law_id
where s.israel_law_id = p_law_id and s.seq between x.seq - p_context and x.seq + p_context
order by s.seq;
$$;

-- Locked down: no public (anon) access. Only the backend, with database credentials.
revoke execute on function search_sections(text, extensions.vector, int, bigint, int) from public, anon, authenticated;
revoke execute on function get_section(bigint, text, int) from public, anon, authenticated;
alter table laws        enable row level security;
alter table amendments  enable row level security;
alter table texts       enable row level security;
alter table sections    enable row level security;
alter table ingest_runs enable row level security;
