import os
import unicodedata
import random
import logging

logger = logging.getLogger(__name__)

# punctuation, mark, separator, or "other"
PASS_CATS = set("PMZCS")

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
            LATIN_1[cat] = char
        else:
            LATIN_1[cat] += char

LATIN_1["default"] = {
    # The "default" should really be defined based on the language of the PDF
    "Lu": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "Ll": "abcdefghijklmnopqrstuvwxyz",
    "Nd": "0123456789",
}


def map_charset(charset: str) -> dict:
    """
    Maps the characters in the given charset to their Unicode category.
    """
    char_glyphs = get_font_glyphs(charset)
    return categorize_glyphs(char_glyphs)


def categorize_glyphs(glyphs: str) -> dict:
    """
    Maps the characters in the given glyphs to their Unicode category.
    """
    cats = {}
    for cat, char in zip(map(unicodedata.category, glyphs), glyphs):
        if cat[0] in PASS_CATS:
            pass
        elif cat not in cats.keys():
            cats[cat] = char
        else:
            cats[cat] += char

    # create a subset of the categories that are in the default categories
    cats["default"] = {}
    for key in cats.keys():
        if key == "default":
            continue

        if key in LATIN_1["default"].keys():
            isect = "".join(set(cats[key]).intersection(set(LATIN_1["default"][key])))
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
