from ingest.extract import clean_pages, is_reversed, unreverse_line
from ingest.sections import split_sections

SAMPLE = """חוק לדוגמה, התשפ"ו-2026
פרק א': כללי
הגדרות
1. בחוק זה –
"הרשות" – הרשות שהוקמה לפי סעיף 3;
"השר" – שר המשפטים.
מטרה
2. מטרתו של חוק זה לקבוע כללים לדוגמה.
פרק ב': הרשות
הקמה
3. מוקמת בזה רשות.
סמכויות הראש
4. לראש הרשות יהיו הסמכויות האלה:
(1) למנות עובדים;
(2) לקבוע נהלים.
הגבלה
4א. על אף האמור בסעיף 4, לא ימנה הראש עובד אלא באישור השר.
5. תחילתו של חוק זה ביום פרסומו.
"""


def test_sections_numbers_headings_chapters():
    secs = split_sections(SAMPLE)
    nums = [s.section_num for s in secs if s.section_num]
    assert nums == ["1", "2", "3", "4", "4א", "5"]
    by = {s.section_num: s for s in secs}
    assert by["1"].heading == "הגדרות"
    assert by["4"].heading == "סמכויות הראש"
    assert by["4א"].heading == "הגבלה"
    assert by["4"].chapter.startswith("פרק ב'")
    assert by["1"].chapter.startswith("פרק א'")
    # sub-items stay inside their section, verbatim
    assert "(2) לקבוע נהלים." in by["4"].content
    assert by["4"].content.startswith("4. לראש הרשות")


def test_list_numbers_do_not_split():
    text = "1. בחוק זה –\nהערה\n1. פריט ברשימה\n2. סעיף שני."
    nums = [s.section_num for s in split_sections(text) if s.section_num]
    assert nums == ["1", "2"]


def test_reversed_detection_and_fix():
    logical = SAMPLE.splitlines()
    # visual order as many PDFs store it: Hebrew reversed, numbers left-to-right,
    # brackets mirrored. unreverse_line is its own inverse, so it simulates that.
    visual = [unreverse_line(l) for l in logical]
    assert not is_reversed(SAMPLE)
    assert is_reversed("\n".join(visual))
    ext = clean_pages(["\n".join(visual)])
    assert ext.status == "reversed_fixed"
    assert ext.text == SAMPLE.strip()


def test_headers_and_page_numbers_removed():
    page = "ספר החוקים – נוסח משולב\n{}\n- {} -"
    pages = [page.format(SAMPLE, i) for i in range(1, 6)]
    ext = clean_pages(pages)
    assert "ספר החוקים – נוסח משולב" not in ext.text
    assert "\n- 3 -" not in ext.text
