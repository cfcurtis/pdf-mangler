import sys
import hashlib
import random
import zlib
import logging
import time

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
CLIPPING_PATH_OPS = [pikepdf.Operator(op) for op in ["W", "W*"]]
MAX_PATH_TWEAK = 0.2  # Percent
MIN_PATH_TWEAK = 9  # pdf units, 1/8"
BLOCK_BEGIN_OPS = [pikepdf.Operator(op) for op in ["BI"]]
BLOCK_END_OPS = [pikepdf.Operator(op) for op in ["EI"]]

logger = logging.getLogger(__name__)


def get_page_dims(page: pikepdf.Page) -> float:
    """
    Checks the various boxes defined on the page and returns the smallest width, height, and diagonal.
    """
    dims = [float("inf")] * 3
    for key in page.keys():
        if "Box" in key:
            # Box rectangles are defined differently than drawn rectangles, just to be fun
            rect = [float(p) for p in page[key]]
            width = abs(rect[0] - rect[2])
            height = abs(rect[1] - rect[3])
            dims[0] = min(dims[0], width)
            dims[1] = min(dims[1], height)
            dims[2] = min(dims[2], (width**2 + height**2) ** 0.5)

    return dims


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
        self.create_hash_name()
        self.state = {"point": None, "font": "default", "page": 0, "page_dims": [0, 0, 0]}
        self.font_map = {
            "default": fu.DEFAULT_CATS,
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
        if "/ID" in self.pdf.trailer.keys():
            self.hash_name = hashlib.md5(bytes(self.pdf.trailer.ID[0])).hexdigest()
        else:
            # Loop through the objects and concatenate contents, then hash.
            # This ignores metadata and probably doesn't guarantee a consistent ID.
            contents = b""
            for obj in self.pdf.objects:
                if "/Contents" in obj.keys():
                    contents += obj.Contents.read_raw_bytes()

            self.hash_name = hashlib.md5(contents).hexdigest()

        self.hash_name += ".pdf"

    def define_font_maps(self, page: pikepdf.Page) -> None:
        """
        Parses the fonts on the page and defines the character/category mapping
        """

        if "/Resources" not in page.keys() or "/Font" not in page.Resources.keys():
            # No fonts defined, stick with the default
            return

        for name, font in page.Resources.Font.items():
            if "/FontDescriptor" in font.keys() and "/CharSet" in font.FontDescriptor.keys():
                self.font_map[name] = fu.map_charset(str(font.FontDescriptor.CharSet))
            elif "/FirstChar" in font.keys():
                # define the map based on the first char and last char
                self.font_map[name] = fu.map_numeric_range(int(font.FirstChar), int(font.LastChar))
            else:
                logger.info(
                    f"Font {name} on page {self.state['page']} has no CharSet specified, not yet handled"
                )

    def mangle_text(self, operands: list) -> None:
        """
        Modifies the text operands.
        """
        # Replace text with random characters
        if isinstance(operands[0], pikepdf.String):
            operands[0] = pikepdf.String(
                fu.replace_text(str(operands[0]), self.font_map[self.state["font"]])
            )
        elif isinstance(operands[0], pikepdf.Array):
            for i in range(len(operands[0])):
                if isinstance(operands[0][i], pikepdf.String):
                    operands[0][i] = pikepdf.String(
                        fu.replace_text(str(operands[0][i]), self.font_map[self.state["font"]])
                    )
        else:
            # Not sure what this means, so raise a warning if it happens
            logger.warning(f"Unknown text operand {operands[0]} found on page {self.state['page']}")

    def mangle_path(self, operands: list, operator: str) -> list:
        """
        Randomly modifies path construction operands to mangle vector graphics.
        """
        operands = [float(op) for op in operands]
        new_ops = operands.copy()
        new_point_ids = None

        if operator == "m":
            # single point to start/end path, don't modify
            self.state["point"] = (operands[0], operands[1])
        elif operator == "l":
            # end of a path
            new_point_ids = (0, 1)
        elif operator == "c":
            # Bezier curve with two control points.
            # Don't modify the control points, just the end point
            new_point_ids = (4, 5)
        elif operator in ["v", "y"]:
            # Bezier curves with one control point.
            # Don't modify the control point, just the end point.
            new_point_ids = (2, 3)
        elif operator == "re":
            # rectangle, handle it separately
            diag = (operands[2] ** 2 + operands[3] ** 2) ** 0.5

            # if the rectangle covers most of the page, don't modify it (likely a border)
            if (
                operands[2] > self.state["page_dims"][0] * 0.9
                or operands[3] > self.state["page_dims"][1] * 0.9
                or diag > self.state["page_dims"][2] * 0.5
            ):
                return operands
            else:
                max_tweak = max(MIN_PATH_TWEAK, diag * MAX_PATH_TWEAK)
                for i in range(4):
                    new_ops[i] = operands[i] + random.random() * max_tweak
        else:
            # Don't know what this is, so raise a warning if it happens
            logger.warning(f"Unknown path operator {operator} found on page {self.state['page']}")

        if new_point_ids is not None:
            dist = (
                (operands[new_point_ids[0]] - self.state["point"][0]) ** 2
                + (operands[new_point_ids[1]] - self.state["point"][1]) ** 2
            ) ** 0.5
            # if the line spans most of the page, don't modify it
            if (
                dist > self.state["page_dims"][0] * 0.9
                or dist > self.state["page_dims"][1] * 0.9
                or dist > self.state["page_dims"][2] * 0.5
            ):
                return operands
            else:
                for id in new_point_ids:
                    max_tweak = max(MIN_PATH_TWEAK, dist * MAX_PATH_TWEAK)
                    new_ops[id] = operands[id] + random.random() * max_tweak
                self.state["point"] = (operands[new_point_ids[0]], operands[new_point_ids[1]])

        return new_ops

    def mangle_block(self, block: list) -> None:
        """
        Mangles info in a block of commands.
        """
        if block[0][1] == pikepdf.Operator("BI"):
            # Inline image
            logger.info(f"Inline image detected on page {self.state['page']}, not yet handled")
        else:
            logger.info(
                f"Block starting with {block[0][1]} detected on page {self.state['page']}, not yet handled"
            )

    def mangle_content(self, stream: pikepdf.Object) -> bytes:
        """
        Go through the stream instructions and mangle the content.
        Replace text with random characters and distort vector graphics.
        """
        # store some info about the page itself
        self.state["page_dims"] = get_page_dims(stream)

        # define the character maps of the fonts on the page
        self.define_font_maps(stream)

        og_commands = pikepdf.parse_content_stream(stream)
        commands = []
        block = None
        for i, (operands, operator) in enumerate(og_commands):
            if block is not None:
                block.append((operands, operator))
            elif operator == FONT_CHANGE:
                self.state["font"] = str(operands[0])
            elif operator in TEXT_SHOW_OPS:
                self.mangle_text(operands)
            elif operator in CLIPPING_PATH_OPS:
                # back up, undo the previous path modification
                commands[i - 1] = og_commands[i - 1]
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
            logger.info(f"Found an article bead on page {page.index}, not yet handled")
            pass

        if "/Annots" in page.keys():
            # annotations
            for annot in page.Annots:
                if annot.Subtype == "/Link":
                    # mangle the URI
                    if "/URI" in annot.A.keys():
                        annot.A.URI = pikepdf.String(fu.replace_text(str(annot.A.URI)))
                    # otherwise if it's an internal link, that's fine
                else:
                    logger.info(
                        f"Found an annotation of type {annot.Subtype} on page {page.index}, not yet handled"
                    )

    def mangle_pdf(self) -> None:
        """
        Mangle the metadata and content of the pdf.
        """

        logger.info(f"Mangling PDF with {len(self.pdf.pages)} pages")

        self.strip_metadata()
        self.mangle_root()

        for page in self.pdf.pages:
            self.state["page"] = page.index

            # first mangle the contents of the page itself
            page.Contents = self.pdf.make_stream(self.mangle_content(page))

            # then deal with the references
            self.mangle_references(page)

    def save(self) -> None:
        """
        Save the mangled pdf.
        """
        self.pdf.save(self.hash_name, fix_metadata_version=False)


def main() -> None:
    """
    Main function to create and run the Mangler.
    """
    if __name__ != "__main__":
        # if running as a module, log to file
        logger_handler = logging.FileHandler(filename="pdf_mangler.log")
        logger_formatter = logging.Formatter("%(levelname)s:%(name)s: %(message)s")
        logger_handler.setFormatter(logger_formatter)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(logger_handler)

    # Load the PDF and strip the metadata
    mglr = Mangler(sys.argv[1])
    mglr.mangle_pdf()

    # Save the resulting PDF
    mglr.save()
    logger.info(f"Time elapsed: {time.process_time():0.2f}s")
    logger.info(f"Finished mangling PDF with hash name {mglr.hash_name}\n{'*'*80}\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main()
