"""Recognize an operator console on POSIX and Windows."""

import sys


def interactive_console():
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if sys.platform != "win32":
        return True
    # Windows reports redirected NUL as a TTY. GetConsoleMode rejects it.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    get_mode = ctypes.WinDLL("kernel32").GetConsoleMode
    get_mode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_mode.restype = wintypes.BOOL
    mode = wintypes.DWORD()
    return bool(get_mode(msvcrt.get_osfhandle(sys.stdin.fileno()), ctypes.byref(mode)))
