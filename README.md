# pdf-mangler

pdf-mangler is a Python library to mangle the contents of a PDF while preserving structure. The goal is to remove identifying metadata, randomly replace text, distort vector graphics, and replace images such that the meaning and intellectual property of the document is lost, but the structure is preserved. This allows for PDF software developers to reproduce issues encountered by end users with proprietary documents.

This project is in the very early stages of development and does not handle all possible PDF elements.