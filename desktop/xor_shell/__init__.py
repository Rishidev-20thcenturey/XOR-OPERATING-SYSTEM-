"""XOR Shell — the original XOR desktop experience layer.

The shell runs on the proven XFCE/X11 session while it matures.  XFCE
remains the fallback window/session manager; XOR supplies the visible
panel, dock, launcher, command centre and control centre.
"""

from .core import SHELL_VERSION

__version__ = SHELL_VERSION
__all__ = ["__version__"]
