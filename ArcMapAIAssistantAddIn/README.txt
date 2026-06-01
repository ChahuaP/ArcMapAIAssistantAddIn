ArcMap AI Assistant Add-In
==========================

This is the ArcMap Python add-in shell for GeoPilot.
It exposes one native ArcMap toolbar button that starts the local console.
Gateway and ArcMap Bridge reuse the same command internally for silent sync
and workflow execution.

Install for development:

1. Run makeaddin.py to create ArcMapAIAssistantAddIn.esriaddin.
2. Double-click ArcMapAIAssistantAddIn.esriaddin.
3. Open ArcMap.
4. If the toolbar is not visible, enable it from Customize > Toolbars > ArcMap AI Assistant.
5. Click the toolbar button to start GeoPilot and open the console.

Runtime path:

The add-in reads %APPDATA%\ArcMapAIAssistant\install.json and loads:

<install_dir>\arcmap_runtime_py2\runtime.py

Edit runtime files, then press Enter again in ArcMap. No ArcMap restart or
add-in reinstall is needed for runtime-only changes. Reinstall only when
config.xml or the add-in shell changes.

Project layout:

config.xml
  ArcMap add-in metadata, target product, toolbar, and the single console button.

Install/ArcMapAIAssistant_addin.py
  Python implementation loaded by ArcMap under the Python 2.7 runtime.
  It hot-loads the installed runtime and dispatches Bridge silent commands.

makeaddin.py
  Packages the project into a .esriaddin file.
