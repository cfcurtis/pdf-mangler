import unittest
import os
import pikepdf
import warnings

warnings.filterwarnings("error")

from pdf_mangler import mangler

# set the cwd to the tests directory
os.chdir(os.path.dirname(os.path.realpath(__file__)))


class TestMangler(unittest.TestCase):
    def test_integration(self):
        mglr = mangler.Mangler("sunny_mountain_overalls.pdf")
        mglr.mangle_pdf()
        mglr.save()

    def test_parse_unparse(self):
        pdf = pikepdf.open("sample-sigconf.pdf")
        page_1 = pdf.pages[0]
        commands = pikepdf.parse_content_stream(page_1)
        title_charset = str(page_1.Resources.Font.F216.FontDescriptor.CharSet).replace("/", "")
        title_char_cats = text.map_charset(title_charset)
        new_commands = []
        I_TITLE = 9
        I_FIRST_NAME = 12
        for i, (operands, operator) in enumerate(commands):
            if i == I_TITLE:
                for j in range(0, len(operands[0]), 2):
                    operands[0][j] = text.replace_text(str(operands[0][j]), title_char_cats)

            new_commands.append((operands, operator))

        page_1.Contents.write(pikepdf.unparse_content_stream(new_commands))
        pdf.save("sample-sigconf-text-replaced.pdf")
