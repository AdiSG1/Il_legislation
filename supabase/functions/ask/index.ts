// POST /functions/v1/ask   { "question": "...", "history": [{ "role": "user"|"assistant", "content": "..." }] }
//
// The agent: Claude with four read-only tools over the law database, a final
// submit_answer tool, and a check that every quote is verbatim before anything
// is returned. Secrets (set in Supabase, never in code):
//   ANTHROPIC_API_KEY, VOYAGE_API_KEY, IP_HASH_SALT
// Optional: CLAUDE_MODEL, ANTHROPIC_VERSION, DAILY_REQUEST_LIMIT, PER_IP_PER_10_MIN, ALLOWED_ORIGINS

import { createClient } from "npm:@supabase/supabase-js@2";
import { DISCLAIMER, SYSTEM_PROMPT } from "./prompt.ts";
import { checkCitations, type Citation, type SectionText } from "./verify.ts";

const env = (k: string, d = "") => Deno.env.get(k) ?? d;
const MODEL = env("CLAUDE_MODEL", "claude-sonnet-5-5");
const ANTHROPIC_VERSION = env("ANTHROPIC_VERSION", "2023-06-01");
const DAILY_LIMIT = Number(env("DAILY_REQUEST_LIMIT", "500"));
const PER_IP = Number(env("PER_IP_PER_10_MIN", "10"));
const MAX_STEPS = 8;
const MAX_ANSWER_RETRIES = 2;

const db = createClient(env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"), {
  auth: { persistSession: false },
});

// ------------------------------------------------------------------ tools
const TOOLS = [
  {
    name: "search_law_sections",
    description:
      "חיפוש סעיפים בחוקים התקפים לפי משמעות ומילות מפתח. מחזיר עד 8 סעיפים עם מזהה (section_id), שם החוק, מספר הסעיף והנוסח. אפשר לצמצם לחוק אחד עם law_id.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string", description: "מה לחפש, בעברית" },
        law_id: { type: "integer", description: "אופציונלי: מזהה החוק (israel_law_id) לחיפוש בתוכו בלבד" },
      },
      required: ["query"],
    },
  },
  {
    name: "get_section",
    description: "הנוסח המלא של סעיף בחוק, יחד עם הסעיפים הסמוכים לו (לפני ואחרי).",
    input_schema: {
      type: "object",
      properties: {
        law_id: { type: "integer" },
        section_num: { type: "string", description: 'מספר הסעיף כפי שמופיע בחוק, למשל "7א" או "57"' },
      },
      required: ["law_id", "section_num"],
    },
  },
  {
    name: "find_law",
    description:
      "איתור חוק לפי שמו. מחזיר מזהה, שם מלא, האם חוק-יסוד, סטטוס, תאריך התיקון האחרון, והאם הנוסח שלו זמין במאגר (text_status = verified).",
    input_schema: {
      type: "object",
      properties: { name: { type: "string" } },
      required: ["name"],
    },
  },
  {
    name: "law_info",
    description: "פרטי חוק ורשימת התיקונים האחרונים שלו מתוך מאגר הכנסת, כולל תקציר הכנסת לכל תיקון וקישור לפרסום הרשמי.",
    input_schema: {
      type: "object",
      properties: { law_id: { type: "integer" }, limit: { type: "integer", description: "מספר תיקונים, ברירת מחדל 5" } },
      required: ["law_id"],
    },
  },
  {
    name: "submit_answer",
    description: "התשובה הסופית למשתמש. חובה לקרוא לכלי הזה בסוף.",
    input_schema: {
      type: "object",
      properties: {
        outcome: { type: "string", enum: ["answered", "not_found", "interpretive", "refused"] },
        answer: { type: "string", description: "הסבר קצר בעברית, ללא הערכות וללא מידע שאינו בציטוטים" },
        citations: {
          type: "array",
          items: {
            type: "object",
            properties: {
              section_id: { type: "integer" },
              quote: { type: "string", description: "ציטוט מילה במילה מהסעיף; קיצור באמצע רק עם ..." },
            },
            required: ["section_id", "quote"],
          },
        },
      },
      required: ["outcome", "answer", "citations"],
    },
  },
];

interface SectionRow {
  section_id?: number; seq?: number; israel_law_id?: number; law_name?: string; section_num: string | null;
  anchor: string | null; heading: string | null; chapter: string | null; content: string;
  knesset_url?: string; permalink: string; text_status?: string;
}

// sections the agent has seen in this request: id -> text and citation info
type Seen = Map<number, SectionText & { law_id: number; law_name: string; section_num: string | null;
  anchor: string | null; knesset_url: string; permalink: string }>;

async function embedQuery(text: string): Promise<number[]> {
  const r = await fetch("https://api.voyageai.com/v1/embeddings", {
    method: "POST",
    headers: { Authorization: `Bearer ${env("VOYAGE_API_KEY").trim()}`, "content-type": "application/json" },
    body: JSON.stringify({ input: [text], model: "voyage-3.5", input_type: "query", output_dimension: 1024 }),
  });
  if (!r.ok) throw new Error(`voyage ${r.status}`);
  return (await r.json()).data[0].embedding;
}

const lawNames = new Map<number, { name: string; url: string }>();
async function lawName(id: number) {
  if (!lawNames.has(id)) {
    const { data } = await db.from("laws").select("name, knesset_url").eq("israel_law_id", id).single();
    lawNames.set(id, { name: data?.name ?? "", url: data?.knesset_url ?? "" });
  }
  return lawNames.get(id)!;
}

async function runTool(name: string, input: Record<string, unknown>, seen: Seen): Promise<unknown> {
  if (name === "search_law_sections") {
    const query = String(input.query ?? "").slice(0, 500);
    const emb = await embedQuery(query);
    const { data, error } = await db.rpc("search_sections", {
      query_text: query, query_embedding: `[${emb.join(",")}]`, match_count: 8,
      law_filter: input.law_id ?? null,
    });
    if (error) throw error;
    return (data as SectionRow[]).map((s) => {
      seen.set(s.section_id!, { id: s.section_id!, content: s.content, law_id: s.israel_law_id!,
        law_name: s.law_name!, section_num: s.section_num, anchor: s.anchor,
        knesset_url: s.knesset_url!, permalink: s.permalink });
      return { section_id: s.section_id, law_id: s.israel_law_id, law: s.law_name, section: s.section_num,
        heading: s.heading, chapter: s.chapter, text: s.content.slice(0, 2500) };
    });
  }
  if (name === "get_section") {
    const lawId = Number(input.law_id);
    const { data, error } = await db.rpc("get_section", {
      p_law_id: lawId, p_section_num: String(input.section_num ?? ""), p_context: 1,
    });
    if (error) throw error;
    if (!data?.length) return { error: "הסעיף לא נמצא בחוק הזה" };
    const law = await lawName(lawId);
    // get_section returns seq, not id: look the ids up so citations can point at them
    const { data: ids } = await db.from("sections").select("id, seq").eq("israel_law_id", lawId)
      .in("seq", (data as SectionRow[]).map((s) => s.seq));
    const idBySeq = new Map((ids ?? []).map((r: { id: number; seq: number }) => [r.seq, r.id]));
    return (data as SectionRow[]).map((s) => {
      const id = idBySeq.get(s.seq!)!;
      seen.set(id, { id, content: s.content, law_id: lawId, law_name: law.name, section_num: s.section_num,
        anchor: s.anchor, knesset_url: law.url, permalink: s.permalink });
      return { section_id: id, law: law.name, section: s.section_num, heading: s.heading,
        chapter: s.chapter, text: s.content };
    });
  }
  if (name === "find_law") {
    const { data, error } = await db.rpc("find_laws", { p_name: String(input.name ?? "").slice(0, 200), p_limit: 5 });
    if (error) throw error;
    return data;
  }
  if (name === "law_info") {
    const lawId = Number(input.law_id);
    const { data: law } = await db.from("laws")
      .select("israel_law_id, name, is_basic_law, validity_desc, text_status, latest_publication_date, knesset_url")
      .eq("israel_law_id", lawId).single();
    const { data: am, error } = await db.rpc("law_amendments", {
      p_law_id: lawId, p_limit: Math.min(Number(input.limit ?? 5), 15),
    });
    if (error) throw error;
    return { law, amendments: am };
  }
  return { error: `unknown tool ${name}` };
}

// ------------------------------------------------------------------ Claude
interface Usage { input: number; output: number }

async function claude(messages: unknown[], usage: Usage) {
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": env("ANTHROPIC_API_KEY").trim(),
      "anthropic-version": ANTHROPIC_VERSION,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: MODEL, max_tokens: 2000, system: SYSTEM_PROMPT, tools: TOOLS,
      tool_choice: { type: "auto" }, messages,
    }),
  });
  if (!r.ok) throw new Error(`anthropic ${r.status}: ${(await r.text()).slice(0, 300)}`);
  const res = await r.json();
  usage.input += res.usage?.input_tokens ?? 0;
  usage.output += res.usage?.output_tokens ?? 0;
  return res;
}

interface Answer { outcome: string; answer: string; citations: Citation[] }

async function answerQuestion(question: string, history: { role: string; content: string }[], usage: Usage) {
  const seen: Seen = new Map();
  const messages: unknown[] = [
    ...history.slice(-6).map((h) => ({ role: h.role === "assistant" ? "assistant" : "user", content: String(h.content).slice(0, 4000) })),
    { role: "user", content: question },
  ];
  let retries = 0;
  let nudged = false;
  for (let step = 0; step < MAX_STEPS + MAX_ANSWER_RETRIES; step++) {
    const res = await claude(messages, usage);
    messages.push({ role: "assistant", content: res.content });
    const calls = (res.content as { type: string; id: string; name: string; input: Record<string, unknown> }[])
      .filter((b) => b.type === "tool_use");
    if (!calls.length) {
      // the model answered in plain text: answers only count through submit_answer
      if (nudged) break;
      nudged = true;
      messages.push({ role: "user", content: "יש להגיש את התשובה באמצעות הכלי submit_answer בלבד." });
      continue;
    }
    const results = [];
    for (const call of calls) {
      if (call.name === "submit_answer") {
        const ans = call.input as unknown as Answer;
        const checks = checkCitations(ans.citations ?? [], seen);
        const bad = checks.filter((c) => !c.ok);
        const needsQuotes = ans.outcome === "answered" && !(ans.citations?.length);
        if ((bad.length || needsQuotes) && retries < MAX_ANSWER_RETRIES) {
          retries++;
          results.push({ type: "tool_result", tool_use_id: call.id, is_error: true,
            content: needsQuotes
              ? "תשובה מסוג answered חייבת לכלול לפחות ציטוט אחד מסעיף שקיבלת."
              : "הציטוטים הבאים אינם מופיעים מילה במילה בסעיף שצוין, או שהסעיף לא התקבל מהכלים. תקן והגש שוב:\n" +
                bad.map((b) => `- section_id ${b.section_id}: "${b.quote}" (${b.reason})`).join("\n") });
          continue;
        }
        return { ans, checks, seen, verified: bad.length === 0 && !needsQuotes };
      }
      try {
        results.push({ type: "tool_result", tool_use_id: call.id,
          content: JSON.stringify(await runTool(call.name, call.input, seen)) });
      } catch (e) {
        results.push({ type: "tool_result", tool_use_id: call.id, is_error: true, content: `שגיאה: ${String(e).slice(0, 200)}` });
      }
    }
    messages.push({ role: "user", content: results });
  }
  return { ans: null, checks: [], seen, verified: false };
}

// ------------------------------------------------------------------ HTTP
function cors(origin: string | null) {
  const allowed = env("ALLOWED_ORIGINS", "*").split(",").map((s) => s.trim());
  const ok = allowed.includes("*") || (origin && allowed.includes(origin));
  return {
    "Access-Control-Allow-Origin": ok ? (origin ?? "*") : allowed[0],
    "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}

async function clientHash(req: Request) {
  const ip = (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || "unknown";
  const bytes = new TextEncoder().encode(env("IP_HASH_SALT", "change-me") + ip);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

Deno.serve(async (req) => {
  const headers = { ...cors(req.headers.get("origin")), "content-type": "application/json; charset=utf-8" };
  if (req.method === "OPTIONS") return new Response("ok", { headers });
  if (req.method !== "POST") return new Response(JSON.stringify({ error: "POST only" }), { status: 405, headers });

  const started = Date.now();
  let body: { question?: string; history?: { role: string; content: string }[] };
  try { body = await req.json(); } catch { body = {}; }
  const question = String(body.question ?? "").trim().slice(0, 1000);
  if (question.length < 3) return new Response(JSON.stringify({ error: "שאלה ריקה" }), { status: 400, headers });

  const { data: allowed } = await db.rpc("take_request", {
    p_client: await clientHash(req), p_window_minutes: 10, p_per_window: PER_IP, p_per_day: DAILY_LIMIT,
  });
  if (allowed === false) {
    await db.from("qa_log").insert({ question, outcome: "limited", duration_ms: Date.now() - started });
    return new Response(JSON.stringify({ error: "הגעת למגבלת השאלות. נסה שוב מאוחר יותר." }), { status: 429, headers });
  }

  // configuration check: report missing secret NAMES only, never values
  const missing = ["ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "IP_HASH_SALT"].filter((k) => !env(k).trim());
  if (missing.length) {
    await db.from("qa_log").insert({ question, outcome: "error", duration_ms: Date.now() - started,
      answer: { error: `missing secrets: ${missing.join(", ")}`, secret_names_seen: Object.keys(Deno.env.toObject()).filter((k) => !k.startsWith("SUPABASE_")).sort() } });
    return new Response(JSON.stringify({ error: "השירות אינו מוגדר עדיין." }), { status: 503, headers });
  }

  const usage: Usage = { input: 0, output: 0 };
  try {
    const { ans, checks, seen, verified } = await answerQuestion(question, body.history ?? [], usage);
    const okChecks = checks.filter((c) => c.ok);
    const result = ans && verified
      ? {
        outcome: ans.outcome,
        answer: ans.answer,
        citations: okChecks.map((c) => {
          const s = seen.get(c.section_id)!;
          return { quote: c.quote, law: s.law_name, section: s.section_num, anchor: s.anchor,
            knesset_url: s.knesset_url, text_url: s.permalink };
        }),
        disclaimer: DISCLAIMER,
      }
      : {
        outcome: "error",
        answer: "לא הצלחתי לגבש תשובה שכל הציטוטים בה אומתו מול נוסח החוק, ולכן איני מציג אותה. נסה לנסח את השאלה אחרת.",
        citations: [], disclaimer: DISCLAIMER,
      };
    await db.rpc("add_usage", { p_input: usage.input, p_output: usage.output });
    await db.from("qa_log").insert({
      question, answer: result, section_ids: [...seen.keys()], cited_ids: okChecks.map((c) => c.section_id),
      verified, outcome: result.outcome, model: MODEL, input_tokens: usage.input, output_tokens: usage.output,
      duration_ms: Date.now() - started,
    });
    return new Response(JSON.stringify(result), { headers });
  } catch (e) {
    await db.from("qa_log").insert({ question, outcome: "error", model: MODEL, input_tokens: usage.input,
      output_tokens: usage.output, duration_ms: Date.now() - started, answer: { error: String(e).slice(0, 500) } });
    return new Response(JSON.stringify({ error: "אירעה שגיאה. נסה שוב." }), { status: 500, headers });
  }
});
