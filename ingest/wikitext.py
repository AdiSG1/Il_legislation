"""Parse an Israeli law page from Hebrew Wikisource ("ספר החוקים הפתוח").

These pages use a fixed set of templates (all start with "ח:"):

  {{ח:סעיף|7|כותרת|תיקון: ...|עוגן=...}}   start of a section, with marginal heading
  {{ח:סעיף*|(1)||עוגן=תוספת פרט 1}}         item in a schedule (תוספת)
  {{ח:קטע2|פרק ב|פרק ב׳: ...}}              chapter heading (קטע3/קטע4 = lower levels)
  {{ח:ת}} {{ח:תת|(א)}} {{ח:תתת|(1)}} ...     paragraph / sub-paragraph markers
  {{ח:הערה|...}}                             editorial note in the text, e.g. "(בוטל)."
  {{ח:פנימי|target|shown}} {{ח:חיצוני|...}}  links to other sections / other laws
  {{ח:תיבה|ס״ח ...|name|https://fs.knesset.gov.il/...pdf}}  official publication refs
  {{ח:מאגר|2000191}}                         the Knesset IsraelLawID of this law

We turn the page into plain-text sections, keeping the wording exactly, and
collect the official Knesset PDF ids the page cites (used for verification).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

KNESSET_PDF_ID = re.compile(r"fs\.knesset\.gov\.il/+\d+/law/\d+_lsr_(?:ec_)?(\d+)\.pdf", re.I)
MAX_CHARS = 6000


# ------------------------------------------------------------------ templates
def _split_templates(text: str) -> list[tuple[str, str]]:
    """Split text into ('text', s) and ('tpl', inner) pieces, honoring nesting."""
    out: list[tuple[str, str]] = []
    i, n, start = 0, len(text), 0
    while i < n:
        if text.startswith("{{", i):
            if i > start:
                out.append(("text", text[start:i]))
            depth, j = 0, i
            while j < n:
                if text.startswith("{{", j):
                    depth += 1
                    j += 2
                elif text.startswith("}}", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            out.append(("tpl", text[i + 2:j - 2]))
            i = start = j
        else:
            i += 1
    if start < n:
        out.append(("text", text[start:]))
    return out


def _params(inner: str) -> tuple[str, list[str], dict[str, str]]:
    """Template name, positional params, named params (top-level '|' only)."""
    parts, depth, cur = [], 0, []
    k = 0
    while k < len(inner):
        if inner.startswith("{{", k):
            depth += 1; cur.append("{{"); k += 2; continue
        if inner.startswith("}}", k):
            depth -= 1; cur.append("}}"); k += 2; continue
        if inner.startswith("[[", k):
            depth += 1; cur.append("[["); k += 2; continue
        if inner.startswith("]]", k):
            depth -= 1; cur.append("]]"); k += 2; continue
        c = inner[k]
        if c == "|" and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(c)
        k += 1
    parts.append("".join(cur))
    name = parts[0].strip()
    pos: list[str] = []
    named: dict[str, str] = {}
    for p in parts[1:]:
        m = re.match(r"^\s*([^=\s{}\[\]]{1,20})\s*=(.*)$", p, re.S)
        if m and not m.group(1).startswith("("):
            named[m.group(1)] = m.group(2)
        else:
            pos.append(p)
    return name, pos, named


SKIP = {"ח:התחלה", "ח:סוף", "ח:סוגר", "ח:מפריד", "ח:פתיח-התחלה", "ח:פתיח-סוף", "ח:מאגר",
        "ח:כותרת", "מיזמים", "מוקטן"}
PARA = {"ח:ת": 0, "ח:תת": 1, "ח:תתת": 2, "ח:תתתת": 3}


def render(text: str) -> str:
    """Wikitext fragment -> plain text (inline templates only)."""
    out = []
    for kind, s in _split_templates(text):
        if kind == "text":
            out.append(s)
            continue
        name, pos, named = _params(s)
        if name in ("ח:הערה", "ח:הערה*"):
            out.append(render(pos[0]) if pos else "")
        elif name in ("ח:פנימי", "ח:חיצוני", "ח:קישור"):
            shown = pos[1] if len(pos) > 1 and pos[1].strip() else (pos[0] if pos else "")
            out.append(render(shown))
        elif name in PARA:
            labels = [render(p).strip() for p in pos if p.strip()]
            out.append((" ".join(labels) + " ") if labels else "")
        elif name in SKIP or name.startswith("ח:"):
            continue  # structural templates are handled by the section splitter
        else:
            continue  # navigation boxes etc.
    s = "".join(out)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)       # [[link|text]] -> text
    s = re.sub(r"'{2,}", "", s)                                     # bold / italic
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)                                   # other html tags
    return s


# ------------------------------------------------------------------ sections
@dataclass
class WikiSection:
    seq: int
    section_num: str | None
    anchor: str | None
    heading: str | None
    chapter: str | None
    amended_by: str | None
    content: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass
class ParsedLaw:
    title: str | None
    israel_law_id: int | None
    cited_pdf_ids: set[str]
    sections: list[WikiSection]
    cited_publications: set[tuple[str, str, str]] = field(default_factory=set)   # (series, year, page)


# ------------------------------------------------------------------ publication references
# The preamble cites each publication as {{ח:תיבה|<ref>|<name>|<optional pdf>}}, where <ref>
# looks like "ס״ח תשפ״ה, 10" or "תשס״א, 28" or just "210" - series and year carry over
# from the previous reference when they are left out.
_SERIES = {"סח": "סח", "דמי": "דמי", "ער": "ער", "קת": "קת", "הח": "הח", "יפ": "יפ"}
_REF = re.compile(
    r"^\s*(?:(?P<series>[א-ת]{1,3}[״\"][א-ת])\s+)?(?:(?P<year>[א-ת]{1,4}[״\"'׳][א-ת]?)\s*,\s*)?(?P<pages>\d[\d\s,]*)"
)


_FINALS = str.maketrans("ךםןףץ", "כמנפצ")


def plain_letters(s: str) -> str:
    """Hebrew letters only, final forms folded (תש״ם -> תשמ), for comparing names and years."""
    return re.sub(r"[^א-ת]", "", s or "").translate(_FINALS)


def cited_publications(wikitext: str) -> set[tuple[str, str, str]]:
    pre = wikitext.split("{{ח:מפריד}}", 1)[0]
    out: set[tuple[str, str, str]] = set()
    series = year = ""
    for kind, inner in _split_templates(pre):
        if kind != "tpl":
            continue
        name, pos, _ = _params(inner)
        if name != "ח:תיבה" or not pos:
            continue
        ref = render(pos[0]).strip()
        m = _REF.match(ref)
        if not m:
            continue
        if m.group("series"):
            series = _SERIES.get(plain_letters(m.group("series")), plain_letters(m.group("series")))
        if m.group("year"):
            year = plain_letters(m.group("year"))
        for page in re.findall(r"\d+", m.group("pages")):
            out.add((series, year, page))
    return out


def _clean_lines(lines: list[str]) -> str:
    text = "\n".join(l.strip() for l in lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_law(wikitext: str) -> ParsedLaw:
    title = None
    m = re.search(r"\{\{ח:כותרת\|([^}]*)\}\}", wikitext)
    if m:
        title = render(m.group(1)).strip()
    m = re.search(r"\{\{ח:מאגר\|(\d+)", wikitext)
    israel_id = int(m.group(1)) if m else None
    cited = set(KNESSET_PDF_ID.findall(wikitext))

    # body = after the preamble separator; drop the table of contents
    body = wikitext.split("{{ח:מפריד}}", 1)[1] if "{{ח:מפריד}}" in wikitext else wikitext
    body = re.sub(r"\{\{ח:קטע2\|\|תוכן עניינים\}\}", "", body)
    body = re.sub(r'<div class="law-toc[^"]*">.*?</div>', "", body, flags=re.S)
    body = re.sub(r"\[\[קטגוריה:[^\]]*\]\]", "", body)

    sections: list[WikiSection] = []
    chapters: dict[int, str] = {}
    cur: dict | None = None
    buf: list[str] = []

    def chapter_path() -> str | None:
        parts = [chapters[k] for k in sorted(chapters) if chapters[k]]
        return " / ".join(parts) or None

    def flush() -> None:
        nonlocal buf
        if cur is None:
            buf = []
            return
        content = _clean_lines(buf)
        buf = []
        if not content:
            return
        pieces = [content[i:i + MAX_CHARS] for i in range(0, len(content), MAX_CHARS)]
        for p in pieces:
            sections.append(WikiSection(len(sections), cur["num"], cur["anchor"], cur["heading"],
                                        cur["chapter"], cur["amended"], p))

    for line in body.splitlines():
        stripped = line.strip()
        tm = re.match(r"^\{\{(ח:[^|}]+)(.*)\}\}$", stripped, re.S)
        if tm and not _split_templates(stripped)[1:]:
            name, pos, named = _params(stripped[2:-2])
            if name in ("ח:קטע2", "ח:קטע3", "ח:קטע4"):
                level = int(name[-1])
                label = render(pos[1]).strip() if len(pos) > 1 else ""
                if label:
                    for k in [k for k in chapters if k >= level]:
                        del chapters[k]
                    chapters[level] = label
                continue
            if name in ("ח:סעיף", "ח:סעיף*"):
                flush()
                num = pos[0].strip() if pos else None
                heading = render(pos[1]).strip() if len(pos) > 1 else ""
                amended = next((p.split(":", 1)[1].strip() for p in pos[2:] if p.strip().startswith("תיקון")), None)
                anchor = named.get("עוגן", "").strip() or (f"סעיף {num}" if num else None)
                cur = {"num": num, "anchor": anchor, "heading": heading or None,
                       "chapter": chapter_path(), "amended": amended}
                # the section number and heading are part of the quoted text
                buf = [f"{num}. {heading}".strip() if name == "ח:סעיף" else f"{num} {heading}".strip()]
                continue
            if name == "ח:חתימות":
                flush()
                cur = None
                continue
        if cur is not None:
            buf.append(render(line))
    flush()
    return ParsedLaw(title, israel_id, cited, sections, cited_publications(wikitext))
