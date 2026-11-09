#!/usr/bin/env python3
"""Check that the tools QuarTeX needs are installed, and say how to install any that are missing.

Run (Mac):      python3 check_setup.py
Run (Windows):  py check_setup.py
"""
import platform
import shutil
import subprocess
import sys

PYTHON_INSTALL = {
    "mac": "brew install python@3.12",
    "windows": "winget install Python.Python.3.12   (or download from https://www.python.org/downloads/)",
}
REQUIRED = {
    "pandoc": {
        "why": "converts LaTeX to Markdown",
        "mac": "brew install pandoc",
        "windows": "winget install JohnMacFarlane.Pandoc   (or download from https://pandoc.org/installing.html)",
    },
    "quarto": {
        "why": "renders the Quarto document to HTML and PDF",
        "mac": "download the macOS installer from https://quarto.org/docs/download/",
        "windows": "winget install Posit.Quarto   (or download from https://quarto.org/docs/download/)",
    },
}
OPTIONAL = {
    "gs": {
        "why": "converts EPS figures to SVG (only needed if the source has EPS files)",
        "mac": "brew install ghostscript",
        "windows": "download the installer from https://ghostscript.com/releases/",
    },
    "pdftocairo": {
        "why": "converts PDF figures to SVG (only needed if the source has PDF figures)",
        "mac": "brew install poppler",
        "windows": "download the Windows build from https://github.com/oschwartz10612/poppler-windows/releases and add its Library\\bin folder to your PATH",
    },
    "node": {
        "why": "needed only for the --check-math option",
        "mac": "brew install node",
        "windows": "download the installer from https://nodejs.org/",
    },
}


def version_of(cmd):
    try:
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=30)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:
        return "installed"


def check():
    """Return (ok, lines). ok is False only when a required item is missing."""
    lines = []
    ok = True
    mac = platform.system() == "Darwin"
    if sys.version_info < (3, 10):
        ok = False
        lines.append(f"MISSING  Python 3.10 or newer (you have {platform.python_version()})")
        lines.append(f"         install: {PYTHON_INSTALL['mac'] if mac else PYTHON_INSTALL['windows']}")
    else:
        lines.append(f"ok       Python {platform.python_version()}")
    for name, info in REQUIRED.items():
        path = shutil.which(name)
        if path:
            lines.append(f"ok       {name}: {version_of(name)}")
        else:
            ok = False
            lines.append(f"MISSING  {name} ({info['why']})")
            lines.append(f"         install: {info['mac'] if mac else info['windows']}")
    for name, info in OPTIONAL.items():
        if shutil.which(name):
            lines.append(f"ok       {name} (optional)")
        else:
            lines.append(f"optional {name} not installed ({info['why']})")
            lines.append(f"         install: {info['mac'] if mac else info['windows']}")
    return ok, lines


if __name__ == "__main__":
    ok, lines = check()
    print("\n".join(lines))
    print()
    print("Everything needed is installed. Start the converter with: python3 webapp.py (Windows: py webapp.py)"
          if ok else "Install the missing items above, then run this check again.")
    sys.exit(0 if ok else 1)
