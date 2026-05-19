ArcMap AI Assistant Add-In
==========================

This is the ArcMap Python add-in shell for ArcMap AI Assistant.
It exposes one editable native ArcMap combo box and hot-loads the external
Python 2 runtime.

Install:

1. Run makeaddin.py to create ArcMapAIAssistantAddIn.esriaddin.
2. Double-click ArcMapAIAssistantAddIn.esriaddin.
3. Open ArcMap.
4. If the toolbar is not visible, enable it from Customize > Toolbars > ArcMap AI Assistant.
5. Type /key sk-... once in the toolbar input box to save the DeepSeek API key.
6. Type /health in the toolbar input box and press Enter. ArcMap will start the local gateway automatically.
7. Type a GIS request to create a workflow draft.
8. Approve the workflow in the Web console.
9. Type /execute in ArcMap to execute the approved workflow.

Hot-load runtime:

D:\Development\Python\Arcpy\arcmap_runtime_py2\runtime.py

Edit runtime files, then press Enter again in ArcMap. No ArcMap restart or
add-in reinstall is needed for runtime-only changes. Reinstall only when
config.xml or the add-in shell changes.

Project layout:

config.xml
  ArcMap add-in metadata, target product, toolbar, and button declaration.

Install/ArcMapAIAssistant_addin.py
  Python implementation loaded by ArcMap. Keep this Python 2.7 compatible.
  It contains the native combo box input and hot-loads the external runtime.

Images/
  Optional button icons. The current minimal build does not use an icon.

makeaddin.py
  Packages the project into a .esriaddin file.
