"""Split a law's text into sections (סעיפים).

Israeli laws number sections "1.", "2.", "7א.", "12ב." at the start of a line,
usually with a short marginal heading ("הגדרות", "סמכויות הראש") on the line
just before. Chapters start with "פרק ...". Sub-items like "(1)" or "(א)" are in
parentheses and stay inside their section.

Every section keeps its text VERBATIM - the agent quotes from it, and the
quote checker compares against it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

SECTION_START = re.compile(r"^\s*(?P<num>\d{1,3})(?P<suf>[א-ט]{0,2})\.\s*(?P<rest>.*)$")
CHAPTER = re.compile(r"^\s*(פרק|סימן)\s+[^\s]+.*$")
MAX_CHARS = 6000  # very long sections are cut into parts for search


@dataclass
class Section:
    seq: int
    section_num: str | None
    heading: str | None
    chapter: str | None
    content: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    return (
        0 < len(s) <= 45
        and not s.endswith((".", ":", ";", ","))
        and not s.startswith("(")
        and not SECTION_START.match(s)
        and bool(re.search(r"[א-ת]", s))
    )


def _suffix_rank(suf: str) -> int:
    return sum((ord(c) - ord("א") + 1) * (30 ** -i) for i, c in enumerate(suf)) if suf else 0


def split_sections(text: str) -> list[Section]:
    lines = text.splitlines()
    out: list[Section] = []
    chapter: str | None = None
    cur_num: str | None = None
    cur_head: str | None = None
    cur_chapter: str | None = None
    buf: list[str] = []
    last_key = (0, 0.0)

    def flush() -> None:
        body = "\n".join(buf).strip()
        if not body:
            return
        parts = [body[i:i + MAX_CHARS] for i in range(0, len(body), MAX_CHARS)] if len(body) > MAX_CHARS else [body]
        for p in parts:
            out.append(Section(len(out), cur_num, cur_head, cur_chapter, p))

    for i, line in enumerate(lines):
        if CHAPTER.match(line):
            flush(); buf = []
            chapter = line.strip()
            cur_num, cur_head, cur_chapter = None, None, chapter
            continue

        m = SECTION_START.match(line)
        if m:
            key = (int(m["num"]), _suffix_rank(m["suf"]))
            # accept only forward-moving numbering, so "1." inside a list doesn't split
            if key > last_key and key[0] <= last_key[0] + 5:
                heading = None
                if buf and _looks_like_heading(buf[-1]):
                    heading = buf.pop().strip()
                flush()
                buf = [line.strip()]
                cur_num, cur_head, cur_chapter = m["num"] + m["suf"], heading, chapter
                last_key = key
                continue
        buf.append(line)

    flush()
    return out
