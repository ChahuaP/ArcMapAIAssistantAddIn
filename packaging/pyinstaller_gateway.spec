# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the production GeoPilot gateway."""

block_cipher = None

a = Analysis(
    ['../run_gateway.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        ('../VERSION', '.'),
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
    ],
    hookspath=[],
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
