"""Parser tests on a real excerpt of the Wikisource Elections Law page (revision 3040540)."""

import pathlib

from ingest.wikitext import parse_law, render

WIKI = (pathlib.Path(__file__).parent / "fixtures" / "elections_law_excerpt.wiki").read_text(encoding="utf-8")


def test_metadata_and_cited_pdfs():
    law = parse_law(WIKI)
    assert law.title == "חוק הבחירות לכנסת [נוסח משולב], תשכ״ט–1969"
    assert law.israel_law_id == 2000191
    assert law.cited_pdf_ids == {"208018", "211818", "317292", "14147458"}


def test_sections_numbers_headings_chapters():
    secs = parse_law(WIKI).sections
    by = {s.section_num: s for s in secs}
    assert [s.section_num for s in secs] == ["1", "2", "7א", "8", "57", "150", "(1)", "(8)"]
    assert by["1"].heading == "הגדרות"
    assert by["1"].chapter == "פרק א׳: פרשנות"
    assert by["8"].chapter == "פרק ג׳: מפת הבחירות"
    assert by["7א"].heading is None
    assert by["57"].amended_by == "תשכ״ט, תשפ״ו־2"
    assert by["(1)"].anchor == "תוספת פרט 1"
    assert by["(1)"].chapter.startswith("תוספת")
    assert "תגמול לבעלי תפקידים" in by["(1)"].chapter


def test_text_is_verbatim_plain():
    by = {s.section_num: s for s in parse_law(WIKI).sections}
    s1 = by["1"].content
    assert s1.startswith("1. הגדרות\nבחוק זה –")
    assert "ועדת הבחירות המרכזית שהוקמה לפי סעיף 15(א);" in s1
    assert "”הבחירות“ – (נמחקה);" in s1
    assert "{{" not in s1 and "}}" not in s1
    assert by["7א"].content == "7א.\n(בוטל)."
    s57 = by["57"].content
    assert "(א) רשימת מועמדים תכיל לא יותר מ־120 שמות" in s57
    assert "(ט) (1) רשימת מועמדים, בצירוף כתב הסכמה" in s57
    assert "(2) רשימת המועמדים תוגש בידי בא־כוח המפלגה או ממלא מקומו כאמור בסעיף 19ב לחוק המפלגות." in s57
    assert "הודעה על חלוקת שטח המדינה" in by["8"].content


def test_nothing_after_signatures_or_in_toc():
    secs = parse_law(WIKI).sections
    allt = "\n".join(s.content for s in secs)
    assert "זרח ורהפטיג" not in allt
    assert "תוכן עניינים" not in allt
    assert "קטגוריה" not in allt
    assert secs[-1].content.endswith("כל עובד אחר של מפלגה או של ועדת בחירות.")


def test_render_nested():
    assert render("{{ח:הערה|ראו {{ח:פנימי|סעיף 3|סעיף 3}}.}}") == "ראו סעיף 3."
    assert render("{{ח:חיצוני|חוק המפלגות}}") == "חוק המפלגות"
