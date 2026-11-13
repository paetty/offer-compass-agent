"""Isolated Microsoft Word to PDF renderer for the local preview service."""

import sys

import pythoncom
import win32com.client


def main():
    source, target = sys.argv[1:3]
    word = document = None
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(
            source, ConfirmConversions=False, ReadOnly=True,
            AddToRecentFiles=False, Revert=False
        )
        document.ExportAsFixedFormat(target, 17)
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
