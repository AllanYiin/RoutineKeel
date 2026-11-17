"""Process-local DPI and legible native typography; never changes Windows settings."""

import sys
from tkinter import font


def enable_high_dpi():
    if sys.platform != "win32":
        return "native"
    import ctypes

    try:
        setter = ctypes.windll.user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_bool
        if setter(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    return "already-configured"


def configure_display(root):
    root.update_idletasks()
    dpi = root.winfo_fpixels("1i")
    if sys.platform == "win32":
        import ctypes

        try:
            getter = ctypes.windll.user32.GetDpiForWindow
            getter.argtypes = [ctypes.c_void_p]
            getter.restype = ctypes.c_uint
            dpi = getter(root.winfo_id()) or dpi
        except (AttributeError, OSError):
            pass
    dpi = max(72.0, min(float(dpi), 384.0))
    root.tk.call("tk", "scaling", dpi / 72.0)
    available = {name.casefold(): name for name in font.families(root)}
    choices = (
        "Microsoft JhengHei UI",
        "Microsoft JhengHei",
        "PingFang TC",
        "Noto Sans CJK TC",
        "Noto Sans TC",
    )
    family = next(
        (available[name.casefold()] for name in choices if name.casefold() in available),
        font.nametofont("TkDefaultFont", root=root).actual("family"),
    )
    for name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkTooltipFont",
        "TkIconFont",
    ):
        font.nametofont(name, root=root).configure(family=family, size=11)
    root.option_add("*Font", (family, 11))
    scale = dpi / 96.0
    width = min(round(1280 * scale), round(root.winfo_screenwidth() * 0.94))
    height = min(round(850 * scale), round(root.winfo_screenheight() * 0.88))
    root.geometry(f"{width}x{height}")
    root.minsize(min(width, round(980 * scale)), min(height, round(640 * scale)))
    return {"dpi": dpi, "scale": scale, "font_family": family}
