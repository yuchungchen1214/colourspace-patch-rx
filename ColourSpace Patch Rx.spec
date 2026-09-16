# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[('vendor/argyll/macos-arm64/bin/spotread', 'argyll'), ('vendor/argyll/macos-arm64/bin/ccxxmake', 'argyll')],
    datas=[('assets', 'assets'), ('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.'), ('vendor/argyll/macos-arm64/NOTICE.md', 'argyll'), ('vendor/argyll/macos-arm64/License.txt', 'argyll'), ('vendor/argyll/macos-arm64/License2.txt', 'argyll'), ('vendor/argyll/macos-arm64/BUILDING.md', 'argyll'), ('vendor/argyll/macos-arm64/source-patch', 'argyll/source-patch')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ColourSpace Patch Rx',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ColourSpace Patch Rx',
)
app = BUNDLE(
    coll,
    name='ColourSpace Patch Rx.app',
    icon='assets/icon.icns',
    bundle_identifier=None,
)
