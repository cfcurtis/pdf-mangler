from pdf_mangler import text_utils as tu


def dict_equal(a, b):
    """
    Compare dictionaries for equality, ignoring order of list items.
    """
    if set(a.keys()) != set(b.keys()):
        return False

    for key in a.keys():
        if isinstance(a[key], dict):
            if not isinstance(b[key], dict):
                return False
            return dict_equal(a[key], b[key])
        elif isinstance(a[key], list):
            if not isinstance(b[key], list):
                return False
            if set(a[key]) != set(b[key]):
                return False

    return True


def test_map_charset():
    charset = "/a/s/d/f/J/K/L/M/colon/emdash/zero/one/two/ccedilla"
    expected_cats = {
        "Ll": "asdfç",
        "Lu": "JKLM",
        "Nd": "012",
        "default": {"Ll": "asdf", "Lu": "JKLM", "Nd": "012"},
    }
    assert dict_equal(expected_cats, tu.map_charset(charset))


def test_map_numeric_range():
    expected_cats = {"Nd": "0123456789", "Lu": "ABC", "default": {"Nd": "0123456789", "Lu": "ABC"}}
    assert dict_equal(expected_cats, tu.map_numeric_range(48, 67))
