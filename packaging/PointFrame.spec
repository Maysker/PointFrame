import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

project_root = os.path.abspath(os.path.join(SPECPATH, ".."))
entry_point = os.path.join(SPECPATH, "pointframe_entry.py")
source_root = os.path.join(project_root, "src")

webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

a = Analysis(
    [entry_point],
    pathex=[source_root],
    binaries=webview_binaries,
    datas=collect_data_files("pointframe") + webview_datas,
    hiddenimports=[
        *webview_hiddenimports,
        "webview.platforms.gtk",
        "gi",
        "gi.repository.Gtk",
        "gi.repository.WebKit2",
    ],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PointFrame",
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="PointFrame",
)