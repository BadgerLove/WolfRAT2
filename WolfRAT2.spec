# -*- mode: python ; coding: utf-8 -*-


application_data = [
    ("wolfrat/icon.ico", "wolfrat"),
    ("wolfrat/wolfrat_lagcomp.dll", "wolfrat"),
    ("wolfrat/sounds/*.wav", "wolfrat/sounds"),
    ("wolfrat/web_templates/*.css", "wolfrat/web_templates"),
    ("wolfrat/web_templates/*.html", "wolfrat/web_templates"),
    ("wolfrat/web_templates/*.js", "wolfrat/web_templates"),
]

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=application_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="WolfRAT2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="wolfrat/icon.ico",
)
