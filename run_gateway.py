"""Thin launcher: lets PyInstaller see gateway_py3 as a full package.

PyInstaller treats this file as the entry point; since it's at the repo
root, gateway_py3/ is correctly resolved as a package with all subpackages.
"""
from gateway_py3.app import main

if __name__ == "__main__":
    main()
