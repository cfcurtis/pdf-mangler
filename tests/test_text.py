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


def test_to_unicode():
    # font stream from page 358 of ISO 32000-2:2020 with errata
    stream = rb"""
/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo
<</Registry (Adobe)
/Ordering (UCS2)
/Supplement 0
>> def
/CMapName /Adobe-Identity-UCS2 def
/CMapType 2 def
1 begincodespacerange
<0000>  <FFFF>
endcodespacerange
2 beginbfrange
<0000>  <005E>  <0020>
<005F>  <0061>   [<00660066> <00660069> <00660066006C>]
endbfrange
1 beginbfchar
<3A51> <D840DC3E>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""
    font_map = tu.map_unicode(stream)
    assert (
        font_map["ToUnicode"][b"3A51"] == "\ud840\udc3e"
    )  # surrogate pair, not sure how to actually handle this...
    assert font_map["ToUnicode"][b"0000"] == " "
    assert font_map["ToUnicode"][b"0001"] == " "
    assert font_map["ToUnicode"][b"005F"] == "ff"
    assert font_map["ToUnicode"][b"0060"] == "fi"
    assert font_map["ToUnicode"][b"0061"] == "ffl"


def test_map_numeric_range():
    expected_cats = {"Nd": "0123456789", "Lu": "ABC", "default": {"Nd": "0123456789", "Lu": "ABC"}}
    assert dict_equal(expected_cats, tu.map_numeric_range(48, 67))


def test_replace_bytes():
    test_arg = b"(Something) 4 (T) TJ"
    mangled = tu.replace_bytes(test_arg)
    assert mangled != test_arg
    assert len(mangled) == len(test_arg)
