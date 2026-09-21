"""Entry point for the frozen build.

PyInstaller runs its entry script as __main__, which breaks the package-relative
imports in scanverdict/__main__.py. This imports the package properly instead.
"""

import sys

from scanverdict.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
