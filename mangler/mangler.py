import sys
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


def mangle_content(page):
    """
    Mangle the content of a PDF page.
    """


def main(in_filename, out_filename):
    """
    Main function to load, process, and save the PDF.
    """
    # Load the PDF and strip the metadata
    pdf = pikepdf.open(in_filename)
    strip_metadata(pdf)

    # Mangle the content of each page recursively.
    for page in pdf.pages:
        mangle_content(page)

    # Save the PDF.
    pdf.save(out_filename)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
