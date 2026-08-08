"""PyInstaller hook: collect ALL gateway_py3 submodules into PYZ."""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('gateway_py3')
