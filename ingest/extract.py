"""PDF -> clean Hebrew text.

Hebrew PDFs often come out in *visual* order (each line reversed). We detect
that with a simple, transparent test: Hebrew final letters (ך ם ן ף ץ) belong
at the END of words. If they mostly appear at the START, the line order is
reversed, and we flip it back (keeping numbers and Latin runs readable).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

FINALS = "ךםןףץ"
_HEB = "֐-׿"
_WORD = re.compile(rf"[{_HEB}]{{2,}}")
_LTR_RUN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.,:/%]*[0-9A-Za-z]|[0-9A-Za-z]")
_PAGE_NUM = re.compile(r"^\s*[-–]?\s*\d{1,4}\s*[-–]?\s*$")


@dataclass
class Extraction:
    text: str
    status: str          # ok | reversed_fixed | empty
    note: str = ""


def final_letter_score(text: str) -> tuple[int, int]:
    """(# words with a final letter at the end, # words with one at the start)."""
    end = start = 0
    for w in _WORD.findall(text):
        end += w[-1] in FINALS
        start += w[0] in FINALS
    return end, start


def is_reversed(text: str) -> bool:
    end, start = final_letter_score(text)
    return start > end and start >= 5


def unreverse_line(line: str) -> str:
    flipped = line[::-1]
    # numbers / Latin were reversed too; flip each LTR run back
    flipped = _LTR_RUN.sub(lambda m: m.group(0)[::-1], flipped)
    # mirrored brackets
    return flipped.translate(str.maketrans("()[]", ")(]["))


def _drop_repeating_lines(pages: list[list[str]]) -> list[list[str]]:
    """Remove running headers/footers: identical lines on >40% of pages."""
    if len(pages) < 4:
        return pages
    counts = Counter(l.strip() for p in pages for l in set(p) if l.strip())
    limit = max(3, int(len(pages) * 0.4))
    noise = {l for l, c in counts.items() if c >= limit and len(l) < 120}
    return [[l for l in p if l.strip() not in noise] for p in pages]


def clean_pages(pages: list[str]) -> Extraction:
    joined = "\n".join(pages)
    if len(joined.strip()) < 50:
        return Extraction("", "empty", "no text layer - needs OCR")

    status = "ok"
    split = [p.splitlines() for p in pages]
    if is_reversed(joined):
        split = [[unreverse_line(l) for l in p] for p in split]
        status = "reversed_fixed"

    split = _drop_repeating_lines(split)
    lines = [l.rstrip() for p in split for l in p if not _PAGE_NUM.match(l)]
    text = "\n".join(lines)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    end, start = final_letter_score(text)
    return Extraction(text, status, f"final-letter check end={end} start={start}")


def extract_pdf(pdf_bytes: bytes) -> Extraction:
    import fitz  # PyMuPDF

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pages = [page.get_text("text", sort=True) for page in doc]
    return clean_pages(pages)
