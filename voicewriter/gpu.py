"""Make the pip-installed CUDA libraries visible to CTranslate2 on Windows."""

import os
import sys
from pathlib import Path

_done = False


def add_cuda_dll_dirs() -> None:
    global _done
    if _done:
        return
    _done = True
    nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not nvidia.is_dir():
        return
    for bin_dir in nvidia.glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
