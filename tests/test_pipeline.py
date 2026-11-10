from ingest.knesset_site import Amendment, LawItem, verify
from ingest.wikisource import title_from_url


def _a(pdf, page="763", date="2026-07-16T00:00:00"):
    return Amendment(1, "תיקון", "ישיר", date, "ספר החוקים", "3546", page, pdf, None)


def test_title_from_knesset_link():
    assert title_from_url("https://he.wikisource.org/wiki/חוק_הבחירות_לכנסת") == "חוק הבחירות לכנסת"
    assert title_from_url("https://he.wikisource.org/wiki/%D7%97%D7%95%D7%A7_%D7%94%D7%91%D7%97%D7%99%D7%A8%D7%95%D7%AA") == "חוק הבחירות"
    assert title_from_url("https://www.kolzchut.org.il/he/x") is None   # only Wikisource


def test_verify_marks_missing_amendments():
    item = LawItem(2000191, "חוק", "תקף", None, None, "u", [
        _a("https://fs.knesset.gov.il/25/law/25_lsr_14147458.pdf"),
        _a("https://fs.knesset.gov.il//24/law/24_lsr_628986.pdf"),
        _a(None),
    ])
    # the third amendment has no PDF; it is matched by its publication reference (ס״ח תשפ״ו, 763)
    status, note = verify(item, {"14147458", "628986"}, {("סח", "תשפו", "763")})
    assert status == "verified" and note["checked"] == 3 and note["unchecked"] == 0
    status, note = verify(item, {"14147458"})
    assert status == "outdated"
    assert note["missing"][0]["pdf"].endswith("24_lsr_628986.pdf")


def test_hebrew_years():
    from ingest.knesset_site import candidate_years, hebrew_year_letters
    assert hebrew_year_letters(5785) == "תשפה"
    assert hebrew_year_letters(5740) == "תשמ"   # compared with final letters folded
    assert hebrew_year_letters(5715) == "תשטו"
    assert hebrew_year_letters(5716) == "תשטז"
    assert hebrew_year_letters(5761) == "תשסא"
    assert candidate_years("2024-10-30T00:00:00") == {"תשפד", "תשפה"}


def test_match_by_publication_reference():
    """Basic Law: Jerusalem - amendment 3 is cited as 'תשפ״ה, 10' with no PDF link (real preamble)."""
    from ingest.wikitext import cited_publications
    pre = ("{{ח:פתיח-התחלה}}\n{{ח:מאגר|2000045}} {{ח:תיבה|ס״ח תש״ם, 186|חוק־יסוד: ירושלים בירת ישראל|https://fs.knesset.gov.il/9/law/9_lsr_208452.pdf}}; "
           "{{ח:תיבה|תשס״א, 28|תיקון|https://fs.knesset.gov.il/15/law/15_lsr_300277.pdf}}; "
           "{{ח:תיבה|תשע״ח, 94|תיקון מס׳ 2|https://fs.knesset.gov.il/20/law/20_lsr_457849.pdf}}; "
           "{{ח:תיבה|תשפ״ה, 10|תיקון מס׳ 3}}.\n{{ח:סוגר}}\n{{ח:מפריד}}\n")
    pubs = cited_publications(pre)
    assert ("סח", "תשמ", "186") in pubs and ("סח", "תשפה", "10") in pubs
    a3 = Amendment(3, "חוק-יסוד: ירושלים בירת ישראל (תיקון מס' 3)", "ישיר", "2024-10-30T00:00:00",
                   "ספר החוקים", "3299", "10", "https://fs.knesset.gov.il/25/law/25_lsr_5106966.pdf", None)
    item = LawItem(2000045, "חוק-יסוד: ירושלים בירת ישראל", "תקף", None, None, "u", [a3])
    assert verify(item, set(), pubs)[0] == "verified"
    # same page, wrong year -> not a match
    a3.publication_date = "2010-01-01T00:00:00"
    assert verify(item, set(), pubs)[0] == "outdated"


def test_page_numbers_carry_year_and_series():
    from ingest.wikitext import cited_publications
    pre = "{{ח:תיבה|ס״ח תשי״א, 110|א}}, {{ח:תיבה|210|ב}}; {{ח:תיבה|תשפ״א, 196, 201, 203|ג}}{{ח:מפריד}}"
    assert cited_publications(pre) == {("סח", "תשיא", "110"), ("סח", "תשיא", "210"),
                                       ("סח", "תשפא", "196"), ("סח", "תשפא", "201"), ("סח", "תשפא", "203")}


def test_mobile_wikisource_link():
    assert title_from_url("https://he.m.wikisource.org/wiki/חוק_יום_בריאות_הנפש") == "חוק יום בריאות הנפש"
