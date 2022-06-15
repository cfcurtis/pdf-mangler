import os
import sys
import glob
import logging
from pdf_mangler.mangler import Mangler

# set the cwd to the proprietary directory
os.chdir(os.path.dirname(os.path.realpath(__file__)))
already_processed = os.listdir()

# simple script to loop through all files in a directory and mangle them
# configure logger to show original filename
logging.basicConfig(filename="mangle_all.log", encoding="utf-8", level=logging.DEBUG)
mglr = Mangler()

for doc in glob.iglob(sys.argv[1] + "/**/*.pdf", recursive=True):
    try:
        mglr.filename = doc
        if mglr.hash_name in already_processed:
            continue
        print("Mangling: " + doc)
        logging.info("Mangling " + doc)
        mglr.mangle_pdf()
        mglr.save()
    except Exception as e:
        error_str = f"Error processing {doc}: {e}"
        print(error_str)
        logging.error(error_str)
