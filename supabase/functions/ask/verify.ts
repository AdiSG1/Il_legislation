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
    .trim();
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
