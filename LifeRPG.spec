# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

os.environ["DJANGO_SETTINGS_MODULE"] = "webconfig.settings"
analysis = Analysis(
    ["desktop.py"],
    datas=collect_data_files("webgame") + [("assets/liferpg.ico", "assets")],
    hiddenimports=(
        collect_submodules("webconfig") + collect_submodules("webgame.migrations")
        + ["webgame.models", "webgame.management.commands.process_periods",
           "webview.platforms.edgechromium", "webview.platforms.winforms",
           "webview.platforms.mshtml"]
    ),
)
# The Django hook can discover the developer's database. Ship assets only.
analysis.datas = [
    entry for entry in analysis.datas
    if not entry[0].lower().endswith((".sqlite3", ".db", ".jsonl", ".bak"))
]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="LifeRPG",
          console=False, upx=False, icon="assets/liferpg.ico")
coll = COLLECT(exe, analysis.binaries, analysis.datas, upx=False, name="LifeRPG")
