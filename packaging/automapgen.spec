# PyInstaller spec of the Windows build: one folder with AutoMapGen.exe. Run through packaging/build.py, which adds
# configs, assets, docs and licences next to the exe and zips the folder.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("automapgen") + collect_data_files("rasterio")      # graphs, GUI page, Lua templates;
                                                                              # GDAL and PROJ data
analysis = Analysis(
    ["launcher.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=collect_submodules("automapgen") + collect_submodules("rasterio"),
    excludes=["tkinter", "pytest", "mypy", "ruff", "IPython", "matplotlib"],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="AutoMapGen", console=True)
COLLECT(exe, analysis.binaries, analysis.datas, name="AutoMapGen")
