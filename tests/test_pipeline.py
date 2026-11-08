from ingest.knesset_site import Amendment, LawItem, verify
from ingest.wikisource import title_from_url


def _a(pdf):
    return Amendment(1, "תיקון", "ישיר", "2026-07-16T00:00:00", "ספר החוקים", "3546", "763", pdf, None)


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
    status, note = verify(item, {"14147458", "628986"})
    assert status == "verified" and note["checked"] == 2 and note["without_pdf"] == 1
    status, note = verify(item, {"14147458"})
    assert status == "outdated"
    assert note["missing"][0]["pdf"].endswith("24_lsr_628986.pdf")
