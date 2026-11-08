// Run with: node --experimental-strip-types supabase/functions/ask/verify.test.ts
import assert from "node:assert/strict";
import { checkCitations, normalize } from "./verify.ts";

const s57 = {
  id: 57,
  content:
    "57. הגשת רשימות מועמדים\n(א) רשימת מועמדים תכיל לא יותר מ־120 שמות של אנשים הזכאים להיבחר ושהסכימו להכללת שמם ברשימה; השמות יהיו מסומנים כל אחד במספר סידורי.",
};
const retrieved = new Map([[57, s57]]);

// verbatim, with different whitespace and a plain hyphen instead of maqaf
let r = checkCitations([{ section_id: 57, quote: "רשימת מועמדים תכיל לא יותר מ-120  שמות" }], retrieved);
assert.equal(r[0].ok, true);

// elision with ... between two verbatim pieces
r = checkCitations([{ section_id: 57, quote: "רשימת מועמדים תכיל ... במספר סידורי" }], retrieved);
assert.equal(r[0].ok, true);

// a changed word is rejected
r = checkCitations([{ section_id: 57, quote: "רשימת מועמדים תכיל לא יותר מ־100 שמות" }], retrieved);
assert.deepEqual([r[0].ok, r[0].reason], [false, "quote_not_in_section"]);

// pieces out of order are rejected
r = checkCitations([{ section_id: 57, quote: "במספר סידורי ... רשימת מועמדים תכיל" }], retrieved);
assert.equal(r[0].ok, false);

// citing a section that was never retrieved is rejected
r = checkCitations([{ section_id: 99, quote: "רשימת מועמדים תכיל" }], retrieved);
assert.deepEqual([r[0].ok, r[0].reason], [false, "section_not_retrieved"]);

// too short to mean anything
r = checkCitations([{ section_id: 57, quote: "(א)" }], retrieved);
assert.equal(r[0].reason, "quote_too_short");

// quote marks and gershayim variants
assert.equal(normalize("”בוחר“ – תשכ״ט"), normalize('"בוחר" - תשכ"ט'));

console.log("verify.test.ts: all passed");
