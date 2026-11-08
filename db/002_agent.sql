-- Il_legislation — agent support (run after schema.sql)
--
-- * find_laws / law_info: look laws up by name, with status and recent amendments
-- * rate limiting and a daily cap, so a public site can't run up the API bill
-- * an audit log of every question, the sections retrieved and the answer,
--   so answers can be reviewed for accuracy and neutrality

create index if not exists laws_name_trgm_idx on laws using gin (name extensions.gin_trgm_ops);

-- ---------------------------------------------------------------- law lookup
create or replace function find_laws(p_name text, p_limit int default 5)
returns table (israel_law_id bigint, name text, is_basic_law boolean, validity_desc text,
               text_status text, latest_publication_date timestamptz, knesset_url text, score real)
language sql stable
set search_path = public, extensions
as $$
select l.israel_law_id, l.name, l.is_basic_law, l.validity_desc, l.text_status,
       l.latest_publication_date, l.knesset_url,
       greatest(similarity(l.name, p_name), word_similarity(p_name, l.name)) as score
from laws l
where l.name % p_name or p_name <% l.name or l.name ilike '%' || p_name || '%'
order by score desc
limit p_limit;
$$;

create or replace function law_amendments(p_law_id bigint, p_limit int default 5)
returns table (number int, name text, kind text, publication_date timestamptz,
               series text, magazine text, page text, pdf_url text, summary text, in_text boolean)
language sql stable
set search_path = public, extensions
as $$
select a.number, a.name, a.kind, a.publication_date, a.series, a.magazine, a.page,
       a.pdf_url, a.summary, a.in_text
from amendments a
where a.israel_law_id = p_law_id
order by a.publication_date desc nulls last, a.number desc nulls last
limit p_limit;
$$;

-- ---------------------------------------------------------------- limits
create table if not exists rate_limits (
    client_hash  text not null,                  -- salted hash of the IP, never the IP itself
    window_start timestamptz not null,
    requests     int not null default 0,
    primary key (client_hash, window_start)
);

create table if not exists usage_daily (
    day           date primary key,
    requests      int not null default 0,
    input_tokens  bigint not null default 0,
    output_tokens bigint not null default 0
);

-- Atomically count one request; returns false when the caller is over a limit.
create or replace function take_request(p_client text, p_window_minutes int, p_per_window int, p_per_day int)
returns boolean
language plpgsql
set search_path = public, extensions
as $$
declare
    w timestamptz := to_timestamp(floor(extract(epoch from now()) / (p_window_minutes * 60)) * p_window_minutes * 60);
    n_client int;
    n_day int;
begin
    insert into usage_daily(day, requests) values (current_date, 1)
    on conflict (day) do update set requests = usage_daily.requests + 1
    returning requests into n_day;
    insert into rate_limits(client_hash, window_start, requests) values (p_client, w, 1)
    on conflict (client_hash, window_start) do update set requests = rate_limits.requests + 1
    returning requests into n_client;
    delete from rate_limits where window_start < now() - interval '1 day';
    return n_client <= p_per_window and n_day <= p_per_day;
end;
$$;

create or replace function add_usage(p_input bigint, p_output bigint)
returns void
language sql
set search_path = public, extensions
as $$
insert into usage_daily(day, input_tokens, output_tokens) values (current_date, p_input, p_output)
on conflict (day) do update set input_tokens = usage_daily.input_tokens + p_input,
                                output_tokens = usage_daily.output_tokens + p_output;
$$;

-- ---------------------------------------------------------------- audit log
create table if not exists qa_log (
    id            bigserial primary key,
    created_at    timestamptz not null default now(),
    question      text not null,
    answer        jsonb,                 -- the answer exactly as returned to the user
    section_ids   bigint[],              -- every section the agent retrieved
    cited_ids     bigint[],              -- sections quoted in the answer
    verified      boolean,               -- did every quote match its section verbatim?
    outcome       text,                  -- answered | not_found | refused | error | limited
    model         text,
    input_tokens  int,
    output_tokens int,
    duration_ms   int
);
create index if not exists qa_log_created_idx on qa_log(created_at desc);

-- ---------------------------------------------------------------- access
-- Nothing is public. The backend (service_role) is the only caller.
alter table rate_limits enable row level security;
alter table usage_daily enable row level security;
alter table qa_log      enable row level security;

revoke execute on function find_laws(text, int)                    from public, anon, authenticated;
revoke execute on function law_amendments(bigint, int)             from public, anon, authenticated;
revoke execute on function take_request(text, int, int, int)       from public, anon, authenticated;
revoke execute on function add_usage(bigint, bigint)               from public, anon, authenticated;

grant execute on function search_sections(text, extensions.vector, int, bigint, int) to service_role;
grant execute on function get_section(bigint, text, int)                             to service_role;
grant execute on function find_laws(text, int)                                       to service_role;
grant execute on function law_amendments(bigint, int)                                to service_role;
grant execute on function take_request(text, int, int, int)                          to service_role;
grant execute on function add_usage(bigint, bigint)                                  to service_role;
