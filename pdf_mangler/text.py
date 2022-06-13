import os
import unicodedata
import random

# Categories from https://unicodebook.readthedocs.io/unicode.html#unicode-categories
# Default character categories, assuming roman alphabet and punctuation
CHAR_CATS = {
    "Lu": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "Ll": "abcdefghijklmnopqrstuvwxyz",
    "Nd": "0123456789",
}
# punctuation, mark, separator, or "other"
PASS_CATS = "PMZCS"

# Read the glyphlist file and define as a constant
GLYPHLIST = {}
with open(os.path.join(os.path.dirname(__file__), "glyphlist.txt"), "r") as f:
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


def categorize_chars(charset: str) -> dict:
    """
    Maps the characters in the given charset to their Unicode category.
    """
    char_glyphs = get_font_glyphs(charset)
    cats = {}
    for cat, char in zip(map(unicodedata.category, char_glyphs), char_glyphs):
        if cat[0] in PASS_CATS:
            pass
        elif cat not in cats.keys():
            cats[cat] = char
        else:
            cats[cat] += char

    return cats


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
                print(f"Warning: Unknown glyph {name}")
        else:
            print(f"Warning: Unknown glyph {name}")

    return glyphs


def replace_text(text: str, char_cats: dict = CHAR_CATS) -> str:
    """
    Replace text with random characters, preserving punctuation,
    case, and numeric type.
    """
    random_text = ""
    for cat, char in zip(map(unicodedata.category, text), text):
        if cat[0] in PASS_CATS:
            random_text += char
        elif cat in char_cats.keys():
            # otherwise replace with a random character from the same category
            random_text += random.choice(char_cats[cat])
        else:
            print(f"Warning: Passing through {char} with unknown category {cat}")
            random_text += char

    return random_text
