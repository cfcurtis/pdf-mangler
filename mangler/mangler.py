import sys
import hashlib
import random
import zlib
import warnings

warnings.filterwarnings("error")

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
TEXT_SHOW_OPS = [pikepdf.Operator(op) for op in ["Tj", "TJ", "'", '"']]


def strip_metadata(pdf):
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


def mangle_ocgs(pdf):
    """
    Replaces human-readable optional content group names with mangled versions.
    """
    if "/OCProperties" in pdf.Root.keys() and "/OCGs" in pdf.Root.OCProperties.keys():
        for ocg in pdf.Root.OCProperties.OCGs:
            ocg.Name = replace_text(str(ocg.Name))


def create_hash_name(pdf):
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


def replace_image(obj):
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


def replace_text(text):
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

    return pikepdf.String(random_text)


def mangle_content(stream):
    """
    Go through the stream instructions and mangle the content.
    Replace text with random characters and distort vector graphics.
    """
    commands = []
    for operands, operator in pikepdf.parse_content_stream(stream):
        if operator in TEXT_SHOW_OPS:
            # Replace text with random characters
            if isinstance(operands[0], pikepdf.String):
                operands[-1] = replace_text(str(operands[-1]))
            elif isinstance(operands[0], pikepdf.Array):
                operands[0][-1] = replace_text(str(operands[0][-1]))
            else:
                # Not sure what this means, so raise a warning if it happens
                print(f"Warning: unknown operand {operands[0]}")
        # TODO: distort vector graphics

        commands.append((operands, operator))

    return pikepdf.unparse_content_stream(commands)


def mangle_pdf(pdf):
    """
    Mangle the contents of a PDF by going through all the objects
    """

    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Dictionary):
            if "/Type" in obj.keys() and obj.Type == "/Page":
                pikepdf.Page(obj).contents_coalesce()
                obj.Contents.write(mangle_content(obj.Contents))
        elif isinstance(obj, pikepdf.Stream):
            if "/Type" in obj.keys() and obj.Type == "/Metadata":
                # more metadata, obliterate it
                del obj
            elif any([f"/Length{n}" in obj.keys() for n in [1, 2, 3]]):
                # This is a font stream. Surely there's a better way to identify them.
                # Keep the fonts as is.
                pass
            elif "/Subtype" in obj.keys():
                if obj.Subtype == pikepdf.Name("/Image"):
                    # Replace images with random images
                    replace_image(obj)
                elif obj.Subtype == pikepdf.Name("/Form"):
                    # Form XObjects are kind of like sub-pages
                    obj.write(mangle_content(obj))
                else:
                    # Something else to deal with
                    pass
            else:
                # Unknown kind of stream, Try mangling the content
                try:
                    obj.write(mangle_content(obj))
                except Exception as e:
                    print(
                        f"Could not mangle unknown stream object {obj.unparse().decode()}: {type(e)}"
                    )
        elif isinstance(obj, pikepdf.Array):
            if obj == pikepdf.Array([pikepdf.Name("/View"), pikepdf.Name("/Design")]):
                # Defines how layers are used, keep this
                pass
            else:
                pass
        else:
            # Something else to deal with
            print(repr(obj))
            pass


def main(in_filename):
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
