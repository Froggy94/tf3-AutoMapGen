"""Windows build: PyInstaller folder + configs, assets, docs and third-party licences, zipped.

    .venv/Scripts/python.exe packaging/build.py        -> dist/tf3-AutoMapGen-<version>-windows.zip
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("configs", "assets", "schemas", "docsHuman", "docsAI/reference")
FILES = ("LICENSE", "NOTICE", "README.md", "CHANGELOG.md", "docsAI/glossary.md")   # glossary: linked by the reference
EXAMPLE_PROJECTS = ("innsbruck_example.toml",)   # other files in configs/projects are the developers' test projects
LICENSE_NAME = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)


class LicenceCollector:
    """Licence files of the installed distributions the program needs (its dependencies, recursively)."""

    def __init__(self, roots: list[str]) -> None:
        self._roots = roots

    def distributions(self) -> list[metadata.Distribution]:
        found: dict[str, metadata.Distribution] = {}
        todo = list(self._roots)
        while todo:
            name = _canonical(todo.pop())
            if name in found:
                continue
            try:
                dist = metadata.distribution(name)
            except metadata.PackageNotFoundError:
                continue
            found[name] = dist
            todo += [_requirement_name(r) for r in dist.requires or [] if "extra ==" not in r]
        return sorted(found.values(), key=lambda d: _canonical(d.metadata["Name"]))

    def write(self, target: Path) -> None:
        for dist in self.distributions():
            name, version = dist.metadata["Name"], dist.version
            files = [f for f in dist.files or [] if LICENSE_NAME.search(f.name) and ".dist-info" in str(f)]
            folder = target / f"{name}-{version}"
            folder.mkdir(parents=True, exist_ok=True)
            for file in files:
                shutil.copyfile(Path(str(dist.locate_file(file))), folder / file.name)
            if not files:
                (folder / "LICENSE-INFO.txt").write_text(
                    f"{name} {version}: {dist.metadata.get('License-Expression') or dist.metadata.get('License')}\n",
                    encoding="utf-8")


class WindowsBuild:
    """The release folder and its ZIP below `dist`."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    def run(self) -> Path:
        program = self._pyinstaller()
        self._copy_release_files(program)
        LicenceCollector([_requirement_name(r) for r in self._project["dependencies"]]).write(program / "licenses")
        return self._zip(program)

    def _pyinstaller(self) -> Path:
        work = self._root / "build" / "pyinstaller"
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(work / "dist"),
                        "--workpath", str(work / "work"), str(self._root / "packaging" / "automapgen.spec")],
                       check=True, cwd=self._root / "packaging")
        return work / "dist" / "AutoMapGen"

    def _copy_release_files(self, program: Path) -> None:
        for folder in FOLDERS:
            shutil.copytree(self._root / folder, program / folder, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.local.toml"))
        projects = program / "configs" / "projects"
        for file in projects.glob("*.toml"):
            if file.name not in EXAMPLE_PROJECTS:
                file.unlink()
        for name in FILES:
            (program / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._root / name, program / name)

    def _zip(self, program: Path) -> Path:
        name = f"tf3-AutoMapGen-{self._project['version']}-windows"
        target = self._root / "dist" / name
        target.parent.mkdir(exist_ok=True)
        return Path(shutil.make_archive(str(target), "zip", program.parent, program.name))


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    return re.split(r"[\s;<>=!~\[(]", requirement.strip(), maxsplit=1)[0]


if __name__ == "__main__":
    print(WindowsBuild(ROOT).run())
