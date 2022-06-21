import sys
from pathlib import Path
import yaml
import hashlib
import random
import zlib
import logging
import time
from decimal import Decimal
from math import sqrt
from tqdm import tqdm

import pikepdf
from pdf_mangler import text_utils as tu

DEFAULT_CONFIG = Path(__file__).parent / "defaults.yaml"
ANNOT_TEXT_FIELDS = ["/T", "/Contents", "/RC", "/Subj", "/Dest", "/CA", "/AC"]
FONT_CHANGE = pikepdf.Operator("Tf")
TEXT_SHOW_OPS = [pikepdf.Operator(op) for op in ["Tj", "TJ", "'", '"']]
PATH_CONSTRUCTION_OPS = [pikepdf.Operator(op) for op in ["m", "l", "c", "v", "y", "re"]]
CLIPPING_PATH_OPS = [pikepdf.Operator(op) for op in ["W", "W*"]]
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

    return dims


class Mangler:
    def __init__(
        self,
        filename: str = None,
        pdf: pikepdf.Pdf = None,
        config_file=None,
    ) -> None:
        """
        Initialize with a new filename or already opened pdf object.
        """
        if filename:
            self.filename = filename
        elif pdf:
            self._pdf = pdf

        if not config_file:
            config_file = DEFAULT_CONFIG

        try:
            with open(config_file, "r") as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed loading config file {config_file} with error {e}")
            logger.info(f"Falling back to defaults")
            with open(DEFAULT_CONFIG, "r") as f:
                self.config = yaml.safe_load(f)

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

    def replace_image(self, obj: pikepdf.Object) -> None:
        """
        Replaces the image object with a random uniform colour image.
        Something like kittens would be more fun.
        """
        if not self.config["mangle"]["images"]:
            return

        if "/SMask" in obj.keys():
            self.replace_image(obj.SMask)

        # inspired by https://github.com/pikepdf/pikepdf/blob/54fea134e09fd75e2602f72f37260016c50def99/tests/test_sanity.py#L63
        # replace with a random intermediate shade of grey
        grey = hex(random.randint(100, 200))
        image_data = bytes(grey, "utf-8") * 3 * obj.Width * obj.Height
        obj.write(image_data)

    def replace_javascript(self, obj: pikepdf.Object) -> None:
        """
        Check if an object is javascript, and if so, replace it.
        """
        if not self.config["mangle"]["javascript"]:
            return

        js_string = f'app.alert("Javascript detected in object {obj.objgen}");'
        # replace with javascript that doesn't really do anything
        if isinstance(obj.JS, pikepdf.String):
            obj.JS = pikepdf.String(js_string)
        elif isinstance(obj.JS, pikepdf.Stream):
            obj.JS.write(js_string.encode("pdfdoc"))

    def strip_metadata(self) -> None:
        """
        Remove identifying information from the PDF.
        """
        if not self.config["mangle"]["metadata"]:
            return

        # retain some information from the metadata
        keep = {}
        with self._pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            for key in meta.keys():
                if any([field in key for field in self.config["metadata"]["keep"]]):
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
        if not self.config["mangle"]["outlines"]:
            return
        try:
            # replace the title text
            entry.Title = pikepdf.String(tu.replace_text(str(entry.Title)))

            if "/First" in entry.keys():
                self.mangle_outlines(entry.First)
            if "/Next" in entry.keys():
                self.mangle_outlines(entry.Next)
        except AttributeError:
            pass

    def mangle_ocg_order(self, oc_list: pikepdf.Array) -> None:
        """
        Recursively goes through the OCG order list and mangles any strings.
        """
        for i, item in enumerate(oc_list):
            if isinstance(item, pikepdf.Array):
                self.mangle_ocg_order(item)
            elif isinstance(item, pikepdf.String):
                oc_list[i] = pikepdf.String(tu.replace_text(str(item)))

    def mangle_ocgs(self, oc_props: pikepdf.Object) -> None:
        """
        Mangles the names of the OCGs.
        """
        if not self.config["mangle"]["ocg_names"]:
            return

        if "/OCGs" in oc_props.keys():
            for ocg in oc_props.OCGs:
                ocg.Name = pikepdf.String(tu.replace_text(str(ocg.Name)))

        if "/D" in oc_props.keys() and "/Order" in oc_props.D.keys():
            # look for sneaky grouping titles in the order array
            self.mangle_ocg_order(oc_props.D.Order)

    def mangle_root(self) -> None:
        """
        Mangles information from the root, such as OCGs and Outlines.
        """
        for key in self._pdf.Root.keys():
            if key == "/OCProperties":
                self.mangle_ocgs(self._pdf.Root[key])

            elif key == "/Outlines":
                self.mangle_outlines(self._pdf.Root[key])

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
                        if isinstance(obj.Contents, pikepdf.Array):
                            # loop through
                            for stream in obj.Contents:
                                contents += stream.read_raw_bytes()
                        else:
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
        if not self.config["mangle"]["text"]:
            return

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
        if not self.config["mangle"]["paths"]:
            return operands

        new_point_ids = None

        if operator == "m":
            # single point to start/end path
            if self.config["path"]["tweak_start"]:
                operands = [
                    op + Decimal(random.random() * (self.config["path"]["min_tweak"]))
                    for op in operands
                ]
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
            diag = sqrt(operands[2] ** 2 + operands[3] ** 2)

            # if the rectangle covers most of the page, don't modify it (likely a border)
            if (
                operands[2] < self.state["page_dims"][0] * self.config["path"]["percent_page_keep"]
                and operands[3]
                < self.state["page_dims"][1] * self.config["path"]["percent_page_keep"]
            ):
                # we don't need to update the previous point because re doesn't modify it
                max_tweak = max(
                    self.config["path"]["min_tweak"], diag * self.config["path"]["percent_tweak"]
                )
                operands = [op + Decimal(random.random() * max_tweak) for op in operands]
        else:
            # Don't know what this is, so raise a warning if it happens
            logger.warning(f"Unknown path operator {operator} found on page {self.state['page']}")

        if new_point_ids is not None:
            x = abs(operands[new_point_ids[0]] - self.state["point"][0])
            y = abs(operands[new_point_ids[1]] - self.state["point"][1])
            mag = sqrt(x**2 + y**2)

            # update the previous point
            self.state["point"] = (operands[new_point_ids[0]], operands[new_point_ids[1]])

            # if a line is parallel to and spans most of the page, don't modify it
            if (
                x < self.state["page_dims"][0] * self.config["path"]["percent_page_keep"]
                and y > self.state["page_dims"][1] * self.config["path"]["percent_page_keep"]
            ):
                max_tweak = max(
                    self.config["path"]["min_tweak"], mag * self.config["path"]["percent_tweak"]
                )
                for id in new_point_ids:
                    operands[id] = operands[id] + Decimal(random.random() * max_tweak)

        return operands

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
        if not self.config["mangle"]["content"]:
            if "/Content" in stream.keys():
                return stream.Content
            else:
                return stream

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
                operands = self.mangle_path(list(operands), str(operator))

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
                        pass
                    elif xobj.Subtype == "/Form":
                        xobj.write(self.mangle_content(xobj))
                        # forms might recursively reference other forms
                        self.mangle_references(xobj)
                    else:
                        print(xobj.Subtype)
                        pass

            elif key == "/Thumb" and self.config["mangle"]["thumbnails"]:
                # just delete the thumbnail, can't seem to parse the image
                del page.Thumb

            elif key == "/PieceInfo" and self.config["mangle"]["metadata"]:
                # Delete the PieceInfo, it can be hiding PII metadata
                del page.PieceInfo

            elif key == "/B":
                # Article thread bead, deal with this when we have a good example
                logger.info(f"Found an article bead on page {page.index}, not yet handled")
                pass

            elif key == "/Annots" and self.config["mangle"]["annotations"]:
                # annotations
                for annot in page.Annots:
                    if annot.Subtype == "/Link":
                        try:
                            # mangle the URI
                            if "/URI" in annot.A.keys():
                                annot.A.URI = pikepdf.String(tu.replace_text(str(annot.A.URI)))
                            # otherwise if it's an internal link, that's fine
                        except AttributeError:
                            pass
                    else:
                        # replace all text strings
                        for key in annot.keys():
                            if key in ANNOT_TEXT_FIELDS:
                                annot[key] = pikepdf.String(tu.replace_text(str(annot[key])))

    def mangle_objects(self) -> None:
        """
        Go through all the objects in the document and look for JS or images to mangle.
        """
        for obj in tqdm(self.pdf.objects, desc="Objects", leave=False):
            try:
                if "/JS" in obj.keys():
                    self.replace_javascript(obj)
                elif "/Subtype" in obj.keys() and obj.Subtype == "/Image":
                    self.replace_image(obj)
            except AttributeError:
                # Not a dictionary
                pass

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
        self.mangle_objects()

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
    config_file = None
    if len(sys.argv) > 2:
        config_file = sys.argv[2]

    mglr = Mangler(sys.argv[1], config_file)
    mglr.mangle_pdf()
    mglr.save()


if __name__ == "__main__":
    main(log_level=logging.DEBUG, show_output=True)
