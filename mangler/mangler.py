import sys
import hashlib
import random
import zlib

import pikepdf

# Character list copied from https://github.com/sypht-team/pdf-anonymizer
KEEP_CHARS = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ \t\f\r\u00A0\u2013\u2014\u2018\u2019\u201C\u201D\u2020\u2021\u2022\u2023\u2026\u20AC\u2212\u00A9\u00AE\u00AD"
KEEP_META = [
    "format",
    "CreatorTool",
    "CreateDate",
    "RenditionClass",
    "StartupProfile",
    "PDFVersion",
    "HasVisibleTransparency",
    "HasVisibleOverprint",
    "CreatorSubTool",
    "Producer",
]
KEEP_OBJECTS = ["font", "intent_array", "pages_dict", "graphics_state"]
TEXT_SHOW_OPS = [pikepdf.Operator(op) for op in ["Tj", "TJ", "'", '"']]
BLOCK_BEGIN_OPS = [pikepdf.Operator(op) for op in ["BI"]]
BLOCK_END_OPS = [pikepdf.Operator(op) for op in ["EI"]]


def strip_metadata(pdf: pikepdf.Pdf) -> None:
    """
    Remove identifying information from the PDF.
    """
    # retain information on creator tool
    keep = {}
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        for key in meta.keys():
            if any([field in key for field in KEEP_META]):
                keep[key] = meta[key]

    # obliterate the rest
    del pdf.Root.Metadata
    del pdf.docinfo

    # Recreate the metadata with just the fields of interest
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        for key in keep.keys():
            meta[key] = keep[key]


def mangle_ocgs(pdf: pikepdf.Pdf) -> None:
    """
    Replaces human-readable optional content group names with mangled versions.
    """
    if "/OCProperties" in pdf.Root.keys() and "/OCGs" in pdf.Root.OCProperties.keys():
        for ocg in pdf.Root.OCProperties.OCGs:
            ocg.Name = pikepdf.String(replace_text(str(ocg.Name)))


def create_hash_name(pdf: pikepdf.Pdf) -> None:
    """
    Creates a new name for the pdf based on the unique ID.
    """
    hash_name = None
    if "/ID" in pdf.trailer.keys():
        hash_name = hashlib.md5(bytes(pdf.trailer.ID[0])).hexdigest()
    else:
        # Loop through the objects and concatenate contents, then hash.
        # This ignores metadata and probably doesn't guarantee a consistent ID.
        contents = b""
        for obj in pdf.objects:
            if "/Contents" in obj.keys():
                contents += obj.Contents.read_raw_bytes()

        hash_name = hashlib.md5(contents).hexdigest()

    return hash_name + ".pdf"


def replace_image(obj: pikepdf.Object) -> None:
    """
    Replaces the image object with a random uniform colour image.
    Something like kittens would be more fun.
    """
    # replacing image code adapted from https://pikepdf.readthedocs.io/en/latest/topics/images.html

    pdfimg = pikepdf.PdfImage(obj)
    pil_img = pdfimg.as_pil_image()

    # copy a random pixel value to all pixels
    pix = pil_img.getpixel(
        (random.randint(0, pdfimg.width - 1), random.randint(0, pdfimg.height - 1))
    )
    pil_img.putdata([pix] * (pdfimg.width * pdfimg.height))

    # write it back to the image object
    obj.write(zlib.compress(pil_img.tobytes()), filter=pikepdf.Name("/FlateDecode"))


def replace_text(text: str) -> str:
    """
    Replace text with random characters, preserving punctuation,
    case, and numeric type.
    """
    random_text = ""
    for char in text:
        if char in KEEP_CHARS:
            random_text += char
        elif char.isdigit():
            random_text += str(random.randint(0, 9))
        elif char.isalpha():
            if char.isupper():
                random_text += chr(random.randint(65, 90))
            else:
                random_text += chr(random.randint(97, 122))
        else:
            random_text += char

    return random_text


def mangle_text(operands: list) -> None:
    """
    Modifies the text operands.
    """
    # Replace text with random characters
    if isinstance(operands[0], pikepdf.String):
        operands[0] = pikepdf.String(replace_text(str(operands[0])))
    elif isinstance(operands[0], pikepdf.Array):
        for i in range(len(operands[0])):
            if isinstance(operands[0][i], pikepdf.String):
                operands[0][i] = pikepdf.String(replace_text(str(operands[0][i])))
    else:
        # Not sure what this means, so raise a warning if it happens
        print(f"Warning: unknown operand {operands[0]}")


def mangle_block(block: list) -> None:
    """
    Mangles info in a block of commands.
    """
    pass


def mangle_content(stream: pikepdf.Object) -> bytes:
    """
    Go through the stream instructions and mangle the content.
    Replace text with random characters and distort vector graphics.
    """
    commands = []
    block = None
    for operands, operator in pikepdf.parse_content_stream(stream):
        if block is not None:
            block.append((operands, operator))
        elif operator in TEXT_SHOW_OPS:
            mangle_text(operands)
        elif operator in BLOCK_BEGIN_OPS:
            # start of a block, so we need to save a buffer and mangle all at once
            block = [(operands, operator)]
        elif operator in BLOCK_END_OPS:
            # end of a block, mangle away
            mangle_block(block)
            block = None

        # TODO: distort vector graphics

        commands.append((operands, operator))

    return pikepdf.unparse_content_stream(commands)


def mangle_references(page: pikepdf.Page) -> None:
    """
    Recursively go through any references on the page and mangle those
    """
    if "/Resources" in page.keys() and "/XObject" in page.Resources.keys():
        for _, xobj in page.Resources.XObject.items():
            if xobj.Subtype == "/Image":
                replace_image(xobj)
            elif xobj.Subtype == "/Form":
                xobj.write(mangle_content(xobj))
                # forms might recursively reference other forms
                mangle_references(xobj)

    if "/Thumb" in page.keys():
        # just delete the thumbnail, can't seem to parse the image
        del page.Thumb

    if "/PieceInfo" in page.keys():
        # Delete the PieceInfo, it can be hiding PII metadata
        del page.PieceInfo

    if "/B" in page.keys():
        # Article thread bead, deal with this when we have a good example
        print("Found an article bead!")
        pass


def mangle_pdf(pdf: pikepdf.Pdf) -> None:
    """
    Mangle the contents of a PDF by going through all the pages and associated objects.
    """

    for page in pdf.pages:
        # first mangle the contents of the page itself
        page.contents_coalesce()
        page.Contents.write(mangle_content(page.Contents))

        # then deal with the references
        mangle_references(page)


def main(in_filename: str) -> None:
    """
    Main function to load, process, and save the PDF.
    """
    # Load the PDF and strip the metadata
    pdf = pikepdf.open(in_filename)
    strip_metadata(pdf)
    mangle_ocgs(pdf)
    mangle_pdf(pdf)

    # Save the PDF
    pdf.save(create_hash_name(pdf), fix_metadata_version=False)


if __name__ == "__main__":
    main(sys.argv[1])
