# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for GeoPilot gateway 2.0.

collect_all('gateway_py3') provides hiddenimports for every submodule.
We filter out .py files from datas (PyInstaller would place them as inert
data; the frozen importer cannot import from data files). The hiddenimports
list drives PYZ compilation so every module is importable at runtime.
Only web/ static assets remain as data files.
"""
from PyInstaller.utils.hooks import collect_all

block_cipher = None

gw_datas, gw_binaries, gw_hiddenimports = collect_all('gateway_py3')

# Keep only non-.py data files (web assets, JSON schemas, etc.). .py files
# must go through PYZ as compiled modules, not as inert data.
gw_data_filtered = [
    (src, dest) for src, dest in gw_datas
    if not src.endswith('.py')
]

a = Analysis(
    ['../run_gateway.py'],
    pathex=['..'],
    binaries=gw_binaries,
    datas=gw_data_filtered + [
        ('../operation_catalog', 'operation_catalog'),
        ('../gateway_py3/web', 'gateway_py3/web'),
    ],
    hiddenimports=[
        'pydantic',
        'pydantic.deprecated.decorator',
        'pydantic.json_schema',
        'langgraph',
        'langgraph.graph',
        'langgraph.checkpoint',
        'langgraph.checkpoint.sqlite',
        'langgraph.checkpoint.base',
        'aiosqlite',
    ] + gw_hiddenimports,
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'yt_dlp', 'websockets', 'mutagen', 'brotli',
        'Crypto', 'curl_cffi', 'av', 'sounddevice', 'soundfile',
        'boto3', 'botocore', 'aliyunsdkcore',
        'IPython', 'PIL', 'PyQt5', 'matplotlib',
        'numpy', 'pandas', 'scipy', 'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ArcMapAIAssistantGateway',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ArcMapAIAssistantGateway',
)
