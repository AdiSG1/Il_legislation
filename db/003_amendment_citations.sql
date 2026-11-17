-- Il_legislation — let the agent cite amendments (run after 002_agent.sql)
--
-- law_amendments now returns each amendment's id (so an answer can cite it) and can
-- be limited to a date range, for questions like "what changed between 2015 and 2026".
-- The original law row the Knesset includes in its list ("(החוק המקורי)") is excluded.

drop function if exists law_amendments(bigint, int);

create or replace function law_amendments(p_law_id bigint, p_limit int default 5,
                                          p_from date default null, p_to date default null)
returns table (id bigint, number int, name text, kind text, publication_date timestamptz,
               series text, magazine text, page text, pdf_url text, summary text, in_text boolean)
language sql stable
set search_path = public, extensions
as $$
select a.id, a.number, a.name, a.kind, a.publication_date, a.series, a.magazine, a.page,
       a.pdf_url, a.summary, a.in_text
from amendments a
join laws l on l.israel_law_id = a.israel_law_id
where a.israel_law_id = p_law_id
  and (l.publication_date is null or a.publication_date is null
       or a.publication_date::date > l.publication_date::date)
  and (p_from is null or a.publication_date::date >= p_from)
  and (p_to   is null or a.publication_date::date <= p_to)
order by a.publication_date desc nulls last, a.number desc nulls last
limit p_limit;
$$;

revoke execute on function law_amendments(bigint, int, date, date) from public, anon, authenticated;
grant  execute on function law_amendments(bigint, int, date, date) to service_role;
