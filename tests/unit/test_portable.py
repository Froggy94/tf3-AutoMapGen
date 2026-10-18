"""Start of the packaged program: GUI on a double click, a clear message if the program folder is read-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from automapgen.app.portable import NotWritableError, PortableStart


def test_double_click_starts_the_gui(tmp_path: Path) -> None:
    start = PortableStart(tmp_path)

    assert start.arguments([]) == ["gui"]
    assert start.arguments(["check", "map"]) == ["check", "map"]


def test_writable_folder_passes_and_stays_clean(tmp_path: Path) -> None:
    PortableStart(tmp_path).check_writable()

    assert list(tmp_path.iterdir()) == []


def test_missing_folder_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(NotWritableError, match="Unpack the ZIP into a folder of your own"):
        PortableStart(tmp_path / "missing").check_writable()
