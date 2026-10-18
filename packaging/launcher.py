"""Entry point of the Windows build (PyInstaller): AutoMapGen.exe [command ...]; a double click starts the GUI."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from automapgen.app.portable import NotWritableError, PortableStart
from automapgen.cli import main as cli_main


def main() -> int:
    start = PortableStart(Path(sys.executable).resolve().parent)
    double_click = len(sys.argv) == 1
    try:
        start.check_writable()
        os.chdir(start.folder)
        code = cli_main(start.arguments(sys.argv[1:]))
    except NotWritableError as error:
        print(error, file=sys.stderr)
        code = 1
    except Exception:       # a double-clicked console closes at once: keep the error readable
        traceback.print_exc()
        code = 1
    if double_click and code:
        input("\nPress Enter to close this window.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
