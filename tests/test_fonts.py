from pdf_mangler.fonts import utils as fu


def test_map_charset():
    charset = "/a/s/d/f/J/K/L/M/colon/emdash/zero/one/two"
    expected_cats = {
        "Ll": "asdf",
        "Lu": "JKLM",
        "Nd": "012",
    }
    char_cats = fu.map_charset(charset)
    assert char_cats == expected_cats


def test_map_numeric_range():
    assert fu.map_numeric_range(48, 67) == {"Nd": "0123456789", "Lu": "ABC"}
