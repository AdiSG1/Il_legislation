// Quote verification: every quote in an answer must appear, word for word, in the
// section it cites, and that section must be one the agent actually retrieved.
// Pure functions, no Deno / Node APIs, so they can be unit-tested anywhere.

export interface Citation {
  section_id: number;
  quote: string;
}

export interface SectionText {
  id: number;
  content: string;
}

export interface QuoteCheck {
  section_id: number;
  quote: string;
  ok: boolean;
  reason?: "section_not_retrieved" | "quote_not_in_section" | "quote_too_short";
}

// Normalize only what doesn't change meaning: whitespace, quote-mark styles,
// dash and maqaf variants, and Hebrew geresh/gershayim lookalikes.
export function normalize(s: string): string {
  return s
    .normalize("NFC")
    .replace(/[֑-ׇ]/g, (c) => (c === "־" ? "-" : "")) // drop niqqud/cantillation, maqaf -> '-'
    .replace(/[“”„‟"״]/g, '"')
    .replace(/[‘’‚‛'׳`]/g, "'")
    .replace(/[‐‑‒–—―−]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/\)\s+\(/g, ")(")      // "(א) (1)" and "(א)(1)" are the same sub-clause label
    .replace(/\s+([,.;:])/g, "$1")   // no space before punctuation
    .trim();
}

// Numbers and dates in the agent's own explanation must come from what the tools
// returned (law text, amendment list): no number may be invented or misremembered.
// Returns the numbers that appear in the answer but nowhere in the sources.
export function unsupportedNumbers(answer: string, sourceText: string): string[] {
  const src = normalize(sourceText);
  const srcNums = new Set(src.match(/\d+/g) ?? []);
  // dates like 26.7.2018 / 26/07/2018: accept if day, month and year all appear as a date in the sources
  const srcDates = new Set(
    [...src.matchAll(/(\d{4})-(\d{2})-(\d{2})/g)].map((m) => `${+m[3]}.${+m[2]}.${m[1]}`),
  );
  const out: string[] = [];
  const text = normalize(answer);
  for (const m of text.matchAll(/\d{1,2}[./]\d{1,2}[./]\d{4}/g)) {
    const [d, mo, y] = m[0].split(/[./]/);
    if (!srcDates.has(`${+d}.${+mo}.${y}`) && !src.includes(m[0])) out.push(m[0]);
  }
  const withoutDates = text.replace(/\d{1,2}[./]\d{1,2}[./]\d{4}/g, " ");
  for (const n of withoutDates.match(/\d+/g) ?? []) {
    if (!srcNums.has(n) && !srcNums.has(String(Number(n)))) out.push(n);
  }
  return [...new Set(out)];
}

export function checkCitations(citations: Citation[], retrieved: Map<number, SectionText>): QuoteCheck[] {
  return citations.map(({ section_id, quote }) => {
    const sec = retrieved.get(section_id);
    if (!sec) return { section_id, quote, ok: false, reason: "section_not_retrieved" as const };
    const q = normalize(quote).replace(/^\.\.\.|\.\.\.$|^…|…$/g, "").trim();
    if (q.length < 8) return { section_id, quote, ok: false, reason: "quote_too_short" as const };
    // allow the agent to elide with "..." between verbatim pieces
    const pieces = q.split(/\s*(?:\.\.\.|…)\s*/).filter((p) => p.length > 0);
    const hay = normalize(sec.content);
    let from = 0;
    for (const p of pieces) {
      const at = hay.indexOf(p, from);
      if (at < 0) return { section_id, quote, ok: false, reason: "quote_not_in_section" as const };
      from = at + p.length;
    }
    return { section_id, quote, ok: true };
  });
}
