import sys
import hashlib
import random
import zlib

import pikepdf
from pdf_mangler.fonts import utils as fu

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

FONT_CHANGE = pikepdf.Operator("Tf")
TEXT_SHOW_OPS = [pikepdf.Operator(op) for op in ["Tj", "TJ", "'", '"']]
PATH_CONSTRUCTION_OPS = [pikepdf.Operator(op) for op in ["m", "l", "c", "v", "y", "re"]]
MAX_PATH_TWEAK = 18  # 1/4" in PDF units
BLOCK_BEGIN_OPS = [pikepdf.Operator(op) for op in ["BI"]]
BLOCK_END_OPS = [pikepdf.Operator(op) for op in ["EI"]]


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


class Mangler:
    def __init__(self, filename: str) -> None:
        self.pdf = pikepdf.Pdf.open(filename)
        self.state = {"cur_pt": None, "cur_font": "default"}
        self.font_map = {
            "default": fu.CHAR_CATS,
        }

    def strip_metadata(self) -> None:
        """
        Remove identifying information from the PDF.
        """
        # retain information on creator tool
        keep = {}
        with self.pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            for key in meta.keys():
                if any([field in key for field in KEEP_META]):
                    keep[key] = meta[key]

        # obliterate the rest
        del self.pdf.Root.Metadata
        del self.pdf.docinfo

        # Recreate the metadata with just the fields of interest
        with self.pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            for key in keep.keys():
                meta[key] = keep[key]

    def mangle_outlines(self, entry: pikepdf.Dictionary) -> None:
        """
        Recursively mangles the titles of the outline entries
        """
        entry.Title = pikepdf.String(fu.replace_text(str(entry.Title)))
        if "/First" in entry.keys():
            self.mangle_outlines(entry.First)
        if "/Next" in entry.keys():
            self.mangle_outlines(entry.Next)

    def mangle_root(self) -> None:
        """
        Mangles information from the root, such as OCGs and Outlines.
        """
        if "/OCProperties" in self.pdf.Root.keys() and "/OCGs" in self.pdf.Root.OCProperties.keys():
            for ocg in self.pdf.Root.OCProperties.OCGs:
                ocg.Name = pikepdf.String(fu.replace_text(str(ocg.Name)))

        if "/Outlines" in self.pdf.Root.keys():
            self.mangle_outlines(self.pdf.Root.Outlines.First)

    def create_hash_name(self) -> None:
        """
        Creates a new name for the pdf based on the unique ID.
        """
        hash_name = None
        if "/ID" in self.pdf.trailer.keys():
            hash_name = hashlib.md5(bytes(self.pdf.trailer.ID[0])).hexdigest()
        else:
            # Loop through the objects and concatenate contents, then hash.
            # This ignores metadata and probably doesn't guarantee a consistent ID.
            contents = b""
            for obj in self.pdf.objects:
                if "/Contents" in obj.keys():
                    contents += obj.Contents.read_raw_bytes()

            hash_name = hashlib.md5(contents).hexdigest()

        return hash_name + ".pdf"

    def define_font_maps(self, page: pikepdf.Page) -> None:
        """
        Parses the fonts on the page and defines the character/category mapping
        """
        for name, font in page.Resources.Font.items():
            if "/FontDescriptor" in font.keys() and "/CharSet" in font.FontDescriptor.keys():
                self.font_map[name] = fu.map_charset(str(font.FontDescriptor.CharSet))
            elif "/FirstChar" in font.keys():
                # define the map based on the first char and last char
                self.font_map[name] = fu.map_numeric_range(int(font.FirstChar), int(font.LastChar))
            else:
                print(f"Font {name} has no CharSet specified, TBD")

    def mangle_text(self, operands: list) -> None:
        """
        Modifies the text operands.
        """
        # Replace text with random characters
        if isinstance(operands[0], pikepdf.String):
            operands[0] = pikepdf.String(
                fu.replace_text(str(operands[0]), self.font_map[self.state["cur_font"]])
            )
        elif isinstance(operands[0], pikepdf.Array):
            for i in range(len(operands[0])):
                if isinstance(operands[0][i], pikepdf.String):
                    operands[0][i] = pikepdf.String(
                        fu.replace_text(str(operands[0][i]), self.font_map[self.state["cur_font"]])
                    )
        else:
            # Not sure what this means, so raise a warning if it happens
            print(f"Warning: unknown operand {operands[0]}")

    def mangle_path(self, operands: list, operator: str) -> list:
        """
        Randomly modifies path construction operands to mangle vector graphics.
        """
        new_ops = list(operands)
        tweak_ids = []
        if operator in ["m", "l"]:
            # single point to start/end path
            tweak_ids = [0, 1]
        if operator == "c":
            # Bezier curve with two control points.
            # Don't modify the control points, just the end point
            tweak_ids = [4, 5]
        if operator in ["v", "y"]:
            # Bezier curves with one control point.
            # Don't modify the control point, just the end point.
            tweak_ids = [2, 3]
        if operator == "re":
            # rectangle. Modify them all
            tweak_ids = [0, 1, 2, 3]

        for id in tweak_ids:
            new_ops[id] = new_ops[id] + random.randint(-MAX_PATH_TWEAK, MAX_PATH_TWEAK)

        return new_ops

    def mangle_block(self, block: list) -> None:
        """
        Mangles info in a block of commands.
        """
        if block[0][1] == pikepdf.Operator("BI"):
            # Inline image
            print("Inline image detected, not yet handled")
        else:
            print(f"Block starting with {block[0][1]} detected, not yet handled")

    def mangle_content(self, stream: pikepdf.Object) -> bytes:
        """
        Go through the stream instructions and mangle the content.
        Replace text with random characters and distort vector graphics.
        """
        commands = []
        block = None
        for operands, operator in pikepdf.parse_content_stream(stream):
            if block is not None:
                block.append((operands, operator))
            elif operator == FONT_CHANGE:
                self.state["cur_font"] = operands[0]
            elif operator in TEXT_SHOW_OPS:
                self.mangle_text(operands)
            elif operator in BLOCK_BEGIN_OPS:
                # start of a block, so we need to save a buffer and mangle all at once
                block = [(operands, operator)]
            elif operator in BLOCK_END_OPS:
                # end of a block, mangle away
                self.mangle_block(block)
                block = None
            elif operator in PATH_CONSTRUCTION_OPS:
                operands = self.mangle_path(operands, str(operator))

            commands.append((operands, operator))

        return pikepdf.unparse_content_stream(commands)

    def mangle_references(self, page: pikepdf.Page) -> None:
        """
        Recursively go through any references on the page and mangle those
        """
        if "/Resources" in page.keys() and "/XObject" in page.Resources.keys():
            for _, xobj in page.Resources.XObject.items():
                if xobj.Subtype == "/Image":
                    replace_image(xobj)
                elif xobj.Subtype == "/Form":
                    xobj.write(self.mangle_content(xobj))
                    # forms might recursively reference other forms
                    self.mangle_references(xobj)

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

        if "/Annots" in page.keys():
            # annotations
            for annot in page.Annots:
                if annot.Subtype == "/Link":
                    # mangle the URI
                    if "/URI" in annot.A.keys():
                        annot.A.URI = pikepdf.String(fu.replace_text(str(annot.A.URI)))
                    # otherwise if it's an internal link, that's fine

    def mangle_pdf(self) -> None:
        """
        Mangle the metadata and content of the pdf.
        """

        self.strip_metadata()
        self.mangle_root()

        for page in self.pdf.pages:
            # define the character maps of the fonts on the page
            self.define_font_maps(page)

            # first mangle the contents of the page itself
            page.Contents.write(self.mangle_content(page))

            # then deal with the references
            self.mangle_references(page)

    def save(self) -> None:
        """
        Save the mangled pdf.
        """
        self.pdf.save(self.create_hash_name(), fix_metadata_version=False)


def main(in_filename: str) -> None:
    """
    Main function to create and run the Mangler.
    """
    # Load the PDF and strip the metadata
    mglr = Mangler(in_filename)
    mglr.mangle_pdf()

    # Save the resulting PDF
    mglr.save()


if __name__ == "__main__":
    main(sys.argv[1])
