ArcMap AI Assistant Add-In
==========================

This is the ArcMap Python add-in shell for ArcMap AI Assistant.
It exposes native ArcMap toolbar buttons and hot-loads the installed
Python 2 runtime.

Install for development:

1. Run makeaddin.py to create ArcMapAIAssistantAddIn.esriaddin.
2. Double-click ArcMapAIAssistantAddIn.esriaddin.
3. Open ArcMap.
4. If the toolbar is not visible, enable it from Customize > Toolbars > ArcMap AI Assistant.
5. Use the toolbar buttons to start the AI backend, show the console, sync context, and execute approved workflows.

Runtime path:

The add-in reads %APPDATA%\ArcMapAIAssistant\install.json and loads:

<install_dir>\arcmap_runtime_py2\runtime.py

For local development only, ARCMAP_AI_RUNTIME_PATH can override the runtime
path.

Edit runtime files, then press Enter again in ArcMap. No ArcMap restart or
add-in reinstall is needed for runtime-only changes. Reinstall only when
config.xml or the add-in shell changes.

Project layout:

config.xml
  ArcMap add-in metadata, target product, toolbar, and button declaration.

Install/ArcMapAIAssistant_addin.py
  Python implementation loaded by ArcMap. Keep this Python 2.7 compatible.
  It contains the native buttons and hot-loads the installed runtime.

Images/
  Optional button icons. The current minimal build does not use an icon.

makeaddin.py
  Packages the project into a .esriaddin file.
