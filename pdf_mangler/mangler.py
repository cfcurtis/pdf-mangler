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
from PIL import Image, ImageFilter
from io import BytesIO

import pikepdf
from pdf_mangler import text_utils as tu

DEFAULT_CONFIG = Path(__file__).parent / "defaults.yaml"
ANNOT_TEXT_FIELDS = ["/T", "/Contents", "/RC", "/Subj", "/Dest", "/CA", "/AC"]
FONT_CHANGE = pikepdf.Operator("Tf")
TEXT_SHOW_OPS = [pikepdf.Operator(op) for op in ["Tj", "TJ", "'", '"']]
PATH_CONSTRUCTION_OPS = [pikepdf.Operator(op) for op in ["m", "l", "c", "v", "y", "re"]]
CLIPPING_PATH_OPS = [pikepdf.Operator(op) for op in ["W", "W*"]]
PATH_START_OPS = [pikepdf.Operator(op) for op in ["m", "re"]]
BLOCK_BEGIN_OPS = [pikepdf.Operator(op) for op in ["BI"]]
BLOCK_END_OPS = [pikepdf.Operator(op) for op in ["EI"]]

MODE_MAP = {
    "/DeviceRGB": "RGB",
    "/DeviceGray": "L",
    "/DeviceCMYK": "CMYK",
    "/CalRGB": "RGB",
    "/CalGray": "L",
    "/Lab": "LAB",
}

logger = logging.getLogger(__name__)


def get_page_dims(page: pikepdf.Page) -> float:
    """
    Checks the various boxes defined on the page and returns the smallest width, height, and diagonal.
    """
    dims = [float("inf")] * 2
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
            self.pdf = pdf

        if not config_file:
            config_file = DEFAULT_CONFIG

        try:
            with open(config_file, "r") as f:
                self._config = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed loading config file {config_file} with error {e}")
            logger.info(f"Falling back to defaults")
            with open(DEFAULT_CONFIG, "r") as f:
                self._config = yaml.safe_load(f)

        self.updater = None

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

    def config(self, *keys: str):
        """
        Returns the config value for the given category and key, or None if it doesn't exist.
        """
        try:
            config = self._config
            for key in keys:
                config = config[key]

            return config
        except KeyError:
            logger.warning(f"Config key {keys} not found")
            return None

    def replace_image(self, obj: pikepdf.Object) -> None:
        """
        Shuffles the bytes in an image object and writes them back.
        Something like kittens would be more fun.
        """
        if not self.config("mangle", "images"):
            return

        if "/SMask" in obj.keys():
            self.replace_image(obj.SMask)

        filter = None
        decode_parms = None
        if "/Filter" in obj.keys():
            filter = obj.Filter

        if "/DecodeParms" in obj.keys():
            decode_parms = obj.DecodeParms

        blur_failed = False

        if self.config("image", "style") == "blur":
            try:
                # replacing image code inspired by https://pikepdf.readthedocs.io/en/latest/topics/images.html
                pdfimg = pikepdf.PdfImage(obj)
                pil_img = pdfimg.as_pil_image()
                og_mode = pil_img.mode
                if og_mode != "RGB":
                    # Gaussian Blur filter only works on RGB
                    pil_img = pil_img.convert("RGB")

                pil_img = pil_img.filter(
                    ImageFilter.GaussianBlur(
                        radius=min([obj.Height, obj.Width]) * self.config("image", "blur_radius")
                    )
                )

                if og_mode != "RGB":
                    # convert it back to the original mode
                    pil_img = pil_img.convert(og_mode)

            except Exception as e:
                logger.error(
                    f"Failed blurring image with object id {obj.objgen}, falling back to greyscale replacement"
                )
                blur_failed = True

        if blur_failed or self.config("image", "style") in ["grey", "gray"]:
            mode = "RGB"
            if "/ColorSpace" in obj.keys() and obj.ColorSpace in MODE_MAP:
                mode = MODE_MAP[obj.ColorSpace]

            if len(mode) == 1:
                pil_img = Image.new(mode=mode, size=(obj.Width, obj.Height), color=128)
            else:
                pil_img = Image.new(
                    mode=mode, size=(obj.Width, obj.Height), color=(128,) * len(mode)
                )

        try:
            # A bit hacky, but it seems to work
            if filter == pikepdf.Name("/DCTDecode"):
                # Likely JPEG
                with BytesIO() as bytestream:
                    pil_img.save(bytestream, format="JPEG")
                    obj.write(
                        bytestream.getvalue(),
                        filter=filter,
                        decode_parms=decode_parms,
                        type_check=False,
                    )
            else:
                # Probably PNG?
                obj.write(zlib.compress(pil_img.tobytes()), filter=filter)

        except:
            logger.error(
                f"Could not write image with original parameters, creating RBG FlateDecode image"
            )
            pil_img = Image.new(mode="RGB", size=(obj.Width, obj.Height), color=(128, 128, 128))
            obj.write(zlib.compress(pil_img.tobytes()), filter=pikepdf.Name("/FlateDecode"))

    def replace_javascript(self, obj: pikepdf.Object) -> None:
        """
        Check if an object is javascript, and if so, replace it.
        """
        if not self.config("mangle", "javascript"):
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
        if not self.config("mangle", "metadata"):
            return

        # retain some information from the metadata
        keep = {}
        with self._pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            for key in meta.keys():
                if any([field in key for field in self.config("metadata", "keep")]):
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
        if not self.config("mangle", "outlines"):
            return
        try:
            # replace the title text
            if "/Title" in entry.keys():
                entry.Title = pikepdf.String(tu.replace_text(str(entry.Title)))
            if "/First" in entry.keys():
                self.mangle_outlines(entry.First)
            if "/Next" in entry.keys():
                self.mangle_outlines(entry.Next)
        except AttributeError:
            # not a dictionary
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
        if not self.config("mangle", "ocg_names"):
            return

        if "/OCGs" in oc_props.keys():
            for ocg in oc_props.OCGs:
                ocg.Name = pikepdf.String(tu.replace_text(str(ocg.Name)))

        if "/D" in oc_props.keys() and "/Order" in oc_props.D.keys():
            # look for sneaky grouping titles in the order array
            self.mangle_ocg_order(oc_props.D.Order)

    def mangle_pieceinfo(self, piece_info: pikepdf.Dictionary) -> None:
        """
        Recursively goes through pieceinfo and mangles any text.
        """
        for key in piece_info.keys():
            if isinstance(piece_info[key], pikepdf.String):
                piece_info[key] = pikepdf.String(tu.replace_text(str(piece_info[key])))
            elif isinstance(piece_info[key], pikepdf.Stream):
                # overwrite with empty stream
                piece_info[key].write(b"")
            elif isinstance(piece_info[key], pikepdf.Dictionary):
                self.mangle_pieceinfo(piece_info[key])

    def mangle_root(self) -> None:
        """
        Mangles information from the root, such as OCGs and Outlines.
        """
        for key in self._pdf.Root.keys():
            if key == "/OCProperties":
                self.mangle_ocgs(self._pdf.Root[key])

            elif key == "/Outlines":
                self.mangle_outlines(self._pdf.Root[key])

            elif key == "/PieceInfo":
                self.mangle_pieceinfo(self._pdf.Root[key])

    def create_hash_name(self) -> None:
        """
        Creates a new name for the pdf based on the unique ID.
        """
        if "/ID" in self._pdf.trailer.keys():
            self.hash_name = hashlib.md5(bytes(self._pdf.trailer.ID[0])).hexdigest()
        else:
            # Loop through the pages and concatenate contents, then hash.
            # This ignores metadata and probably doesn't guarantee a consistent ID.
            contents = b""
            for page in self.pdf.pages:
                if isinstance(page.Contents, pikepdf.Array):
                    # loop through the contents array
                    for stream in page.Contents:
                        contents += stream.read_raw_bytes()
                else:
                    contents += page.Contents.read_raw_bytes()

            self.hash_name = hashlib.md5(contents).hexdigest()

        self.hash_name += ".pdf"

    def add_font_map(self, font: pikepdf.Object) -> None:
        """
        Defines the character/category mapping for the font object reference.
        """
        name = font.objgen

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
        if not self.config("mangle", "text"):
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

    def is_background_line(self, x: Decimal, y: Decimal) -> bool:
        """
        Checks to see if the line runs parallel to and most of the length of the page.
        """
        p_x, p_y = [d * self.config("path", "percent_page_keep") for d in self.state["page_dims"]]
        # 9 is 1/8" in pdf units, seems like a reasonable value for parallelness
        return (x > p_x and y < 9) or (y > p_y and x < 9) or (x > p_x and y > p_y)

    def mangle_path(self, operands: list, operator: str) -> list:
        """
        Randomly modifies path construction operands to mangle vector graphics.
        """
        if not self.config("mangle", "paths"):
            return operands

        new_point_ids = None

        if operator == "m":
            self.state["point"] = (operands[0], operands[1])
            # single point to start/end path
            if self.config("path", "tweak_start"):
                operands = [
                    op
                    + Decimal(
                        random.uniform(
                            -self.config("path", "min_tweak"), self.config("path", "min_tweak")
                        )
                    )
                    for op in operands
                ]
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
            if not self.is_background_line(abs(operands[2]), abs(operands[3])):
                # we don't need to update the previous point because re doesn't modify it
                max_tweak = max(
                    self.config("path", "min_tweak"), diag * self.config("path", "percent_tweak")
                )
                operands = [op + Decimal(random.uniform(-max_tweak, max_tweak)) for op in operands]
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
            if not self.is_background_line(x, y):
                max_tweak = max(
                    self.config("path", "min_tweak"), mag * self.config("path", "percent_tweak")
                )
                for id in new_point_ids:
                    operands[id] = operands[id] + Decimal(random.uniform(-max_tweak, max_tweak))

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
        if not self.config("mangle", "content"):
            if "/Content" in stream.keys():
                return stream.Content
            else:
                return stream

        og_commands = pikepdf.parse_content_stream(stream)
        commands = []
        block = None
        for i, (operands, operator) in enumerate(og_commands):
            if block is not None:
                block.append((operands, operator))
            elif operator == FONT_CHANGE:
                self.state["font"] = stream.Resources.Font[operands[0]].objgen
            elif operator in TEXT_SHOW_OPS:
                self.mangle_text(operands)
            elif operator in CLIPPING_PATH_OPS and self.config("path", "exclude_clip"):
                # back up, undo the previous path modification
                for j in range(len(commands) - 1, -1, -1):
                    if commands[j][1] in PATH_CONSTRUCTION_OPS:
                        commands[j] = og_commands[j]
                    if commands[j][1] in PATH_START_OPS:
                        break
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
                if self.updater:
                    items = page.Resources.XObject.items()
                else:
                    items = tqdm(page.Resources.XObject.items(), desc="XObjects", leave=False)
                for _, xobj in items:
                    if xobj.Subtype == "/Form":
                        xobj.write(self.mangle_content(xobj))
                        # forms might recursively reference other forms
                        self.mangle_references(xobj)

            elif key == "/Thumb" and self.config("mangle", "thumbnails"):
                # just delete the thumbnail, can't seem to parse the image
                del page.Thumb

            elif key == "/PieceInfo" and self.config("mangle", "metadata"):
                # Go through the pieceinfo and mangle strings
                self.mangle_pieceinfo(page[key])

            elif key == "/B":
                # Article thread bead, deal with this when we have a good example
                logger.info(f"Found an article bead on page {page.index}, not yet handled")
                pass

            elif key == "/Annots" and self.config("mangle", "annotations"):
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
        if self.updater:
            items = self.pdf.objects
        else:
            items = tqdm(self.pdf.objects, desc="Objects", leave=False)
        for obj in items:
            try:
                if "/JS" in obj.keys():
                    self.replace_javascript(obj)
                elif "/Subtype" in obj.keys() and obj.Subtype == "/Image":
                    self.replace_image(obj)
                elif "/Type" in obj.keys() and obj.Type == "/Font":
                    self.add_font_map(obj)

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

        counter = 0
        if self.updater:
            items = self._pdf.pages
            self.updater.SetRange(len(self._pdf.pages))
            self.updater.Update(counter)
        else:
            items = tqdm(self._pdf.pages, desc="Pages", leave=False)

        for page in items:
            if self.updater and self.updater.WasCancelled():
                raise InterruptedError("Cancelled by user")

            self.state["page"] = page.index
            # store some info about the page itself
            self.state["page_dims"] = get_page_dims(page)

            # first mangle the contents of the page itself
            page.Contents = self._pdf.make_stream(self.mangle_content(page))

            # then deal with the references
            self.mangle_references(page)

            self.updater and self.updater.Update(counter)
            counter += 1

        # One last call to updater to ensure it's at 100%
        self.updater and self.updater.Update(counter)

        info_str = (
            f"Time elapsed: {time.process_time() - start:0.2f}s\n"
            f"Finished mangling PDF with hash name {self.hash_name}"
        )
        logger.info(info_str + f"\n{'*'*80}\n")
        print(info_str)

    def save(self, folder: str = ".") -> None:
        """
        Save the mangled pdf.
        """
        enc = None
        # copy over the encryption info, but blow away the passwords
        if self._pdf.is_encrypted:
            enc = pikepdf.Encryption(
                owner="",
                user="",
                R=self._pdf.encryption.R,
                allow=self._pdf.allow,
            )
        self._pdf.save(Path(folder) / self.hash_name, fix_metadata_version=False, encryption=enc)


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
