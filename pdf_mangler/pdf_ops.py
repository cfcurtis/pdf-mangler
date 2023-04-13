# Listing of PDF operators to ignore or mangle
ALL_OPS = [
    b"b",  # Close, fill, and stroke path using nonzero winding number rule
    b"B",  # Fill and stroke path using nonzero winding number rule
    b"b*",  # Close, fill, and stroke path using even-odd rule
    b"B*",  # Fill and stroke path using even-odd rule
    b"BDC",  # (PDF 1.2) Begin marked-content sequence with property list
    b"BI",  # Begin inline image object
    b"BMC",  # (PDF 1.2) Begin marked-content sequence
    b"BT",  # Begin text object
    b"BX",  # (PDF 1.1) Begin compatibility section
    b"c",  # Append curved segment to path (three control points)
    b"cm",  # Concatenate matrix to current transformation matrix
    b"CS",  # (PDF 1.1) Set color space for stroking operations
    b"cs",  # (PDF 1.1) Set color space for nonstroking operations
    b"d",  # Set line dash pattern
    b"d0",  # Set glyph width in Type 3 font
    b"d1",  # Set glyph width and bounding box in Type 3 font
    b"Do",  # Invoke named XObject
    b"DP",  # (PDF 1.2) Define marked-content point with property list
    b"EI",  # End inline image object
    b"EMC",  # (PDF 1.2) End marked-content sequence
    b"ET",  # End text object
    b"EX",  # (PDF 1.1) End compatibility section
    b"f",  # Fill path using nonzero winding number rule
    b"F",  # Fill path using nonzero winding number rule (obsolete)
    b"f*",  # Fill path using even-odd rule
    b"G",  # Set gray level for stroking operations
    b"g",  # Set gray level for nonstroking operations
    b"gs",  # (PDF 1.2) Set parameters from graphics state parameter dictionary
    b"h",  # Close subpath
    b"i",  # Set flatness tolerance
    b"ID",  # Begin inline image data
    b"j",  # Set line join style
    b"J",  # Set line cap style
    b"K",  # Set CMYK color for stroking operations
    b"k",  # Set CMYK color for nonstroking operations
    b"l",  # Append straight line segment to path
    b"m",  # Begin new subpath
    b"M",  # Set miter limit
    b"MP",  # (PDF 1.2) Define marked-content point
    b"n",  # End path without filling or stroking
    b"q",  # Save graphics state
    b"Q",  # Restore graphics state
    b"re",  # Append rectangle to path
    b"RG",  # Set RGB color for stroking operations
    b"rg",  # Set RGB color for nonstroking operations
    b"ri",  # Set color rendering intent
    b"s",  # Close and stroke path
    b"S",  # Stroke path
    b"SC",  # (PDF 1.1) Set color for stroking operations
    b"sc",  # (PDF 1.1) Set color for nonstroking operations
    b"SCN",  # (PDF 1.2) Set color for stroking operations (ICCBased and special color spaces)
    b"scn",  # (PDF 1.2) Set color for nonstroking operations (ICCBased and special color spaces)
    b"sh",  # (PDF 1.3) Paint area defined by shading pattern
    b"T*",  # Move to start of next text line
    b"Tc",  # Set character spacing
    b"Td",  # Move text position
    b"TD",  # Move text position and set leading
    b"Tf",  # Set text font and size
    b"Tj",  # Show text
    b"TJ",  # Show text, allowing individual glyph positioning
    b"TL",  # Set text leading
    b"Tm",  # Set text matrix and text line matrix
    b"Tr",  # Set text rendering mode
    b"Ts",  # Set text rise
    b"Tw",  # Set word spacing
    b"Tz",  # Set horizontal text scaling
    b"v",  # Append curved segment to path (initial point replicated)
    b"w",  # Set line width
    b"W",  # Set clipping path using nonzero winding number rule
    b"W*",  # Set clipping path using even-odd rule
    b"y",  # Append curved segment to path (final point replicated)
    b"'",  # Move to next line and show text
    b'"',  # Set word and character spacing, move to next line, and show text
]

WHITESPACE = [b"\x00", b"\x09", b"\x0A", b"\x0C", b"\x0D", b"\x20"]
DELIMITERS = [
    b"\x28",
    b"\x29",
    b"\x3C",
    b"\x3E",
    b"\x5B",
    b"\x5D",
    b"\x7B",
    b"\x7D",
    b"\x2F",
    b"\x25",
]

FONT_CHANGE = b"Tf"
TEXT_SHOW_OPS = [b"Tj", b"TJ", b"'", b'"']
PATH_CONSTRUCTION_OPS = [b"m", b"l", b"c", b"v", b"y", b"re"]
CLIPPING_PATH_OPS = [b"W", b"W*"]
PATH_START_OPS = [b"m", b"re"]
