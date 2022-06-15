import sys
import hashlib
import random
import zlib
import logging
import time
from tqdm import tqdm

import pikepdf
from pdf_mangler import text_utils as tu

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

ANNOT_TEXT_FIELDS = ["/T", "/Contents", "/RC", "/Subj", "/Dest", "/CA", "/AC"]
ACTION_FIELDS = ["/OpenAction", "/A", "/AA", "/URI"]
FONT_CHANGE = pikepdf.Operator("Tf")
TEXT_SHOW_OPS = [pikepdf.Operator(op) for op in ["Tj", "TJ", "'", '"']]
PATH_CONSTRUCTION_OPS = [pikepdf.Operator(op) for op in ["m", "l", "c", "v", "y", "re"]]
CLIPPING_PATH_OPS = [pikepdf.Operator(op) for op in ["W", "W*"]]
MAX_PATH_TWEAK = 0.2  # Percent
MIN_PATH_TWEAK = 18  # pdf units, 1/4"
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
    if "/SMask" in obj.keys():
        replace_image(obj.SMask)

    # inspired by https://github.com/pikepdf/pikepdf/blob/54fea134e09fd75e2602f72f37260016c50def99/tests/test_sanity.py#L63
    # replace with a random intermediate shade of grey
    grey = hex(random.randint(100, 200))
    image_data = bytes(grey, "utf-8") * 3 * obj.Width * obj.Height
    obj.write(image_data)


def replace_javascript(obj: pikepdf.Object) -> None:
    """
    Check if an object is javascript, and if so, replace it.
    """
    try:
        if "/JS" in obj.keys():
            js_string = f'app.alert("Javascript detected in object {obj.objgen}");'
            # replace with javascript that doesn't really do anything
            if isinstance(obj.JS, pikepdf.String):
                obj.JS = pikepdf.String(js_string)
            elif isinstance(obj.JS, pikepdf.Stream):
                obj.JS.write(js_string.encode("pdfdoc"))
        else:
            # go down a level, javascript is sneaky
            for key in obj.keys():
                replace_javascript(obj[key])
    except AttributeError:
        # not a dictionary object
        pass


class Mangler:
    def __init__(self, filename: str = None, pdf: pikepdf.Pdf = None) -> None:
        """
        Initialize with a new filename or already opened pdf object.
        """
        if filename:
            self.filename = filename
        elif pdf:
            self._pdf = pdf

    @property
    def filename(self) -> str:
        return self._pdf.filename

    @filename.setter
    def filename(self, filename: str) -> None:
        self.pdf = pikepdf.Pdf.open(filename)

    @property
    def pdf(self) -> pikepdf.Pdf:
        return self._pdf

    @pdf.setter
    def pdf(self, pdf: pikepdf.Pdf) -> None:
        """
        Sets the pdf and initializes state.
        """
        self._pdf = pdf
        self.create_hash_name()
        self.state = {"point": None, "font": "default", "page": 0, "page_dims": [0, 0, 0]}
        self.font_map = {
            "default": tu.LATIN_1,
        }

    def strip_metadata(self) -> None:
        """
        Remove identifying information from the PDF.
        """
        # retain some information from the metadata
        keep = {}
        with self._pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            for key in meta.keys():
                if any([field in key for field in KEEP_META]):
                    keep[key] = meta[key]

        # obliterate the rest
        del self._pdf.Root.Metadata
        del self._pdf.docinfo

        # Recreate the metadata with just the fields of interest
        with self._pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            for key in keep.keys():
                meta[key] = keep[key]

    def mangle_outlines(self, entry: pikepdf.Dictionary) -> None:
        """
        Recursively mangles the titles of the outline entries
        """
        # replace the title text
        entry.Title = pikepdf.String(tu.replace_text(str(entry.Title)))

        # then check for actions
        for key in entry.keys():
            if key in ACTION_FIELDS:
                replace_javascript(entry[key])

        if "/First" in entry.keys():
            self.mangle_outlines(entry.First)
        if "/Next" in entry.keys():
            self.mangle_outlines(entry.Next)

    def mangle_root(self) -> None:
        """
        Mangles information from the root, such as OCGs and Outlines.
        """
        if (
            "/OCProperties" in self._pdf.Root.keys()
            and "/OCGs" in self._pdf.Root.OCProperties.keys()
        ):
            for ocg in self._pdf.Root.OCProperties.OCGs:
                ocg.Name = pikepdf.String(tu.replace_text(str(ocg.Name)))

        for key in self._pdf.Root.keys():
            # replace any javascript actions in the root
            if key in ACTION_FIELDS:
                replace_javascript(self._pdf.Root[key])

        if "/Outlines" in self._pdf.Root.keys() and "/First" in self._pdf.Root.Outlines.keys():
            self.mangle_outlines(self._pdf.Root.Outlines.First)

    def create_hash_name(self) -> None:
        """
        Creates a new name for the pdf based on the unique ID.
        """
        if "/ID" in self._pdf.trailer.keys():
            self.hash_name = hashlib.md5(bytes(self._pdf.trailer.ID[0])).hexdigest()
        else:
            # Loop through the objects and concatenate contents, then hash.
            # This ignores metadata and probably doesn't guarantee a consistent ID.
            contents = b""
            for obj in self._pdf.objects:
                try:
                    if "/Contents" in obj.keys():
                        contents += obj.Contents.read_raw_bytes()
                except AttributeError:
                    # no Contents, skip this one
                    pass

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
                self.font_map[name] = tu.map_charset(str(font.FontDescriptor.CharSet))
            elif "/FirstChar" in font.keys():
                # define the map based on the first char and last char
                self.font_map[name] = tu.map_numeric_range(int(font.FirstChar), int(font.LastChar))
            else:
                # Assume it's Latin-1
                self.font_map[name] = tu.LATIN_1

    def mangle_text(self, operands: list) -> None:
        """
        Modifies the text operands.
        """
        # Replace text with random characters
        if isinstance(operands[0], pikepdf.String):
            operands[0] = pikepdf.String(
                tu.replace_text(str(operands[0]), self.font_map[self.state["font"]])
            )
        elif isinstance(operands[0], pikepdf.Array):
            for i in range(len(operands[0])):
                if isinstance(operands[0][i], pikepdf.String):
                    operands[0][i] = pikepdf.String(
                        tu.replace_text(str(operands[0][i]), self.font_map[self.state["font"]])
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
        for key in page.keys():
            if key == "/Resources" and "/XObject" in page.Resources.keys():
                for _, xobj in tqdm(page.Resources.XObject.items(), desc="XObjects", leave=False):
                    if xobj.Subtype == "/Image":
                        replace_image(xobj)
                    elif xobj.Subtype == "/Form":
                        xobj.write(self.mangle_content(xobj))
                        # forms might recursively reference other forms
                        self.mangle_references(xobj)

            elif key == "/Thumb":
                # just delete the thumbnail, can't seem to parse the image
                del page.Thumb

            elif key == "/PieceInfo":
                # Delete the PieceInfo, it can be hiding PII metadata
                del page.PieceInfo

            elif key == "/B":
                # Article thread bead, deal with this when we have a good example
                logger.info(f"Found an article bead on page {page.index}, not yet handled")
                pass

            elif key == "/Annots":
                # annotations
                for annot in page.Annots:
                    if annot.Subtype == "/Link":
                        # mangle the URI
                        if "/URI" in annot.A.keys():
                            annot.A.URI = pikepdf.String(tu.replace_text(str(annot.A.URI)))
                        # otherwise if it's an internal link, that's fine
                    else:
                        # replace all text strings and javascript in the annotation
                        for key in annot.keys():
                            if key in ANNOT_TEXT_FIELDS:
                                annot[key] = pikepdf.String(tu.replace_text(str(annot[key])))
                            elif key in ACTION_FIELDS:
                                replace_javascript(annot[key])

            elif key in ACTION_FIELDS:
                replace_javascript(page[key])

    def mangle_pdf(self) -> None:
        """
        Mangle the metadata and content of the pdf.
        """
        start = time.process_time()
        info_str = f"Mangling PDF with {len(self._pdf.pages)} pages"
        logger.info(info_str)
        print(info_str)

        self.strip_metadata()
        self.mangle_root()
        for page in tqdm(self._pdf.pages, desc="Pages"):
            self.state["page"] = page.index

            # first mangle the contents of the page itself
            page.Contents = self._pdf.make_stream(self.mangle_content(page))

            # then deal with the references
            self.mangle_references(page)

        info_str = (
            f"Time elapsed: {time.process_time() - start:0.2f}s\n"
            f"Finished mangling PDF with hash name {self.hash_name}"
        )
        logger.info(info_str + f"\n{'*'*80}\n")
        print(info_str)

    def save(self) -> None:
        """
        Save the mangled pdf.
        """
        self._pdf.save(self.hash_name, fix_metadata_version=False)


def main(log_level: int = logging.INFO, show_output: bool = False) -> None:
    """
    Main function to create and run the Mangler.
    """
    # configure the log file, if it's not already done
    root_logger = logging.getLogger()
    if not root_logger.hasHandlers():
        logging.basicConfig(filename="pdf_mangler.log", encoding="utf-8", level=log_level)

    if show_output:
        # also log to stdout
        stdout_handler = logging.StreamHandler(sys.stdout)
        root_logger.addHandler(stdout_handler)

    # Load the PDF, mangle, and save
    mglr = Mangler(sys.argv[1])
    mglr.mangle_pdf()
    mglr.save()


if __name__ == "__main__":
    main(log_level=logging.DEBUG, show_output=True)
