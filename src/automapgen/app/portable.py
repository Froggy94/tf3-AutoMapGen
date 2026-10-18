"""Start of the packaged program (Windows build, PyInstaller): the program folder is the working folder.

The ZIP is unpacked into a folder the user can write to; configs, projects, downloads, cache and output live next to
the exe, so map makers see and edit the configs like in a source checkout. A double click starts the GUI.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path


class NotWritableError(Exception):
    """The program folder cannot be written (e.g. below C:\\Program Files)."""


class PortableStart:
    """Working folder and command of a start of the packaged program."""

    def __init__(self, program_folder: Path) -> None:
        self._folder = program_folder

    @property
    def folder(self) -> Path:
        return self._folder

    def arguments(self, argv: Sequence[str]) -> list[str]:
        """The command line; without arguments (double click) the GUI."""
        return list(argv) or ["gui"]

    def check_writable(self) -> None:
        """Raises NotWritableError with what to do if the program folder cannot be written."""
        probe = self._folder / f".write_test_{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as error:
            raise NotWritableError(
                f"The program folder {self._folder} cannot be written ({error.strerror}).\n"
                "tf3-AutoMapGen keeps projects, downloads, cache and maps next to the program. Unpack the ZIP into a "
                "folder of your own, e.g. Documents\\tf3-AutoMapGen, not below C:\\Program Files.") from error
