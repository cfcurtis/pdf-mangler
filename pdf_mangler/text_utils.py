import os
import unicodedata
import random
import logging
import re

logger = logging.getLogger(__name__)

# punctuation, mark, separator, or "other"
PASS_CATS = set("PMZCS")

# compile the regex to look for things between ()
in_parens = re.compile(rb"\((.*?)\)")
# compile the regex to look for things between <>
in_angle = re.compile(rb"\<(.*?)\>")
# compile the regex to look for things between []
in_square_brackets = re.compile(rb"\[(.*?)\]")

# Read the glyphlist file and define as a constant
GLYPHLIST = {}
with open(os.path.join(os.path.dirname(__file__), "fonts/glyphlist.txt"), "r") as f:
    for line in f:
        if line.startswith("#"):
            continue
        line = line.strip()
        if not line:
            continue
        pdf_name, unicode_hex = line.split(";")
        if len(unicode_hex) > 4:
            # combination character
            GLYPHLIST[pdf_name] = "".join(chr(int(hex, 16)) for hex in unicode_hex.split())
        else:
            GLYPHLIST[pdf_name] = chr(int(unicode_hex, 16))

# Categories from https://unicodebook.readthedocs.io/unicode.html#unicode-categories
# Default character categories, assuming latin alphabet and punctuation
# Read the Adobe Latin-1 character set and define as default
# charsets downloaded from https://github.com/adobe-type-tools/adobe-latin-charsets
LATIN_1 = {}
with open(os.path.join(os.path.dirname(__file__), "fonts/adobe-latin-1.txt")) as f:
    # skip the header line
    f.readline()
    for line in f.readlines():
        char = chr(int(line.split()[0], 16))
        cat = unicodedata.category(char)
        if cat[0] in PASS_CATS:
            continue
        elif cat not in LATIN_1.keys():
            LATIN_1[cat] = [char]
        else:
            LATIN_1[cat] += [char]

LATIN_1["default"] = {
    # The "default" should really be defined based on the language of the PDF
    "Lu": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    "Ll": list("abcdefghijklmnopqrstuvwxyz"),
    "Nd": list("0123456789"),
}


def map_charset(charset: str) -> dict:
    """
    Maps the characters in the given charset to their Unicode category.
    """
    char_glyphs = get_font_glyphs(charset)
    return categorize_glyphs(char_glyphs)


def int_to_pdf_hex(i: int) -> bytes:
    """
    Converts an integer to a PDF-like hex string.
    """
    return f"{i:04X}".encode()


def pdf_hex_to_int(hex: bytes) -> int:
    """
    Converts a PDF-like hex string to an integer.
    """
    return int(hex, 16)


def map_pair(from_b: bytes, to_b: bytes, to_unicode: dict) -> None:
    """
    Establishes the actual mapping between bytes
    """
    to_unicode[from_b] = ""
    # handle the case where the character maps to a combination of characters
    # split matches[i + 1] into groups of 4
    for j in range(0, len(to_b), 4):
        to_unicode[from_b] += chr(int(to_b[j : j + 4], 16))


def map_unicode(stream: bytes, char_cats: dict = {}) -> dict:
    """
    Parses the ToUnicode stream and returns a dict of hex/unicode pairs.
    """
    to_unicode = {}
    start = stream.find(b"beginbfchar")
    end = stream.find(b"endbfchar")
    matches = in_angle.findall(stream, start + 11, end)
    for i in range(0, len(matches), 2):
        map_pair(matches[i], matches[i + 1], to_unicode)

    start = stream.find(b"beginbfrange")
    end = stream.find(b"endbfrange")
    angle_it = in_angle.finditer(stream, start + 13, end)
    square_it = in_square_brackets.finditer(stream, start + 13, end)
    angle = next(angle_it, None)
    square = next(square_it, None)

    while angle:
        rs = pdf_hex_to_int(angle.group(1))
        angle = next(angle_it, None)
        re = pdf_hex_to_int(angle.group(1))
        map_from = map(int_to_pdf_hex, range(rs, re + 1))
        map_to = next(angle_it, None)

        # check to see if the next square bracket is before the next angle bracket
        # This should mean that there's one character per char in the range
        if square and square.start() < map_to.start():
            for from_b in map_from:
                if map_to.end() > square.end():
                    # something's gone crazy
                    raise IndexError("Not enough characters to map to in bfrange")

                map_pair(from_b, map_to.group(1), to_unicode)
                map_to = next(angle_it, None)

        # otherwise, the whole range maps to the same character
        else:
            for from_b in map_from:
                map_pair(from_b, map_to.group(1), to_unicode)

        angle = next(angle_it, None)

    # map the resulting charset
    if not char_cats:
        char_cats = categorize_glyphs(to_unicode.values())

    char_cats["ToUnicode"] = to_unicode

    # and back again
    char_cats["FromUnicode"] = {val: key for key, val in to_unicode.items()}
    return char_cats


def categorize_glyphs(glyphs: list[str]) -> dict:
    """
    Maps the characters in the given glyphs to their Unicode category.
    """
    cats = {}
    for char in glyphs:
        try:
            cat = unicodedata.category(char)
        except TypeError:
            # must be a surrogate pair
            cat = "Cs"

        if cat[0] in PASS_CATS:
            pass
        elif cat not in cats.keys():
            cats[cat] = [char]
        else:
            cats[cat] += [char]

    # create a subset of the categories that are in the default categories
    cats["default"] = {}
    for key in cats.keys():
        if key == "default":
            continue

        if key in LATIN_1["default"].keys():
            isect = list(set(cats[key]).intersection(set(LATIN_1["default"][key])))
            if len(isect) > 0:
                cats["default"][key] = isect

    return cats


def map_numeric_range(first: int, last: int) -> dict:
    """
    Maps the characters in a given numeric range to their Unicode category.
    """
    glyphs = [chr(i) for i in range(first, last + 1)]
    return categorize_glyphs(glyphs)


def get_font_glyphs(charset):
    """
    Return a list of unicode glyphs for the given charset.
    Charset is a long string with names separated by /
    """
    glyphs = []
    for name in charset.split("/")[1:]:
        if name in GLYPHLIST.keys():
            glyphs.append(GLYPHLIST[name])
        elif "_" in name:
            # double character like ff, we can ignore this
            pass
        elif name[0] == "u":
            try:
                # unicode character
                if name[:3] == "uni":
                    glyphs.append(chr(int(name[3:], 16)))
                else:
                    glyphs.append(chr(int(name[1:], 16)))
            except ValueError:
                logger.warning(f"Unknown glyph name {name}")
        else:
            logger.warning(f"Unknown glyph name {name}")

    return glyphs


def replace_text(text: str, char_cats: dict = LATIN_1) -> str:
    """
    Replace text with random characters, preserving punctuation,
    case, and numeric type.
    """
    random_text = ""
    for cat, char in zip(map(unicodedata.category, text), text):
        if cat[0] in PASS_CATS:
            random_text += char
        elif cat in char_cats.keys():
            if cat in char_cats["default"].keys() and char in char_cats["default"][cat]:
                # if it's in the default subset, choose one of those
                # (prevents a lot of random non-latin characters)
                random_text += random.choice(char_cats["default"][cat])
            else:
                # otherwise replace with a random character from the same category
                random_text += random.choice(char_cats[cat])
        elif cat in LATIN_1["default"].keys():
            # If it's in the default subset of the LATIN_1 charset, choose one of those.
            # Unsure how this occurs but seems to happen sometimes.
            random_text += random.choice(LATIN_1["default"][cat])
        else:
            logger.warning(f"Passing through {char} with unknown category {cat}")
            random_text += char

    return random_text


def replace_bytes(text: bytes, char_cats: dict = LATIN_1) -> bytes:
    """
    Replace text with random characters, preserving punctuation,
    case, and numeric type.
    """
    # convert to bytearray so we can modify it in place
    random_text = bytearray(text)
    for match in in_parens.finditer(text):
        # replace the text in the parentheses.
        # Probably a better way of doing this that doesn't require converting to/from strings
        random_text[match.start(1) : match.end(1)] = replace_text(
            match.group(1).decode(), char_cats
        ).encode()

    return random_text


def replace_hex_bytes(text: bytes, char_cats: dict = LATIN_1) -> bytes:
    """
    Replace hexadecimally encoded text.
    """
    random_text = bytearray(text)

    # check for angle brackets indicating hex encoding
    for match in in_angle.finditer(text):
        # more complicated, we need to go through each pair of hex digits
        for i in range(match.start(1), match.end(1) - 1, 2):
            try:
                hex_char = text[i : i + 2]
            except IndexError:
                # if there's an odd number of characters, the last one is assumed to be 0. Just leave it.
                pass

        try:
            unihex = char_cats["ToUnicode"][hex_char]
            newtext = replace_text(unihex, char_cats).encode()
            random_text[i : i + 2] = char_cats["FromUnicode"][newtext.decode()]
        except KeyError as e:
            logger.warning(e)

    return random_text
