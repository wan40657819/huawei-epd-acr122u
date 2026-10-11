"""Separate writable user data from bundled one-file resources."""
from pathlib import Path
import os
import sys
import tempfile


def resource_dir():
    return Path(__file__).resolve().parent


def app_dir():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else resource_dir()


def data_dir():
    base=app_dir()
    try:
        with tempfile.TemporaryFile(dir=base):
            pass
        return base
    except OSError:
        fallback=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'EInkTool'
        fallback.mkdir(parents=True,exist_ok=True)
        return fallback
