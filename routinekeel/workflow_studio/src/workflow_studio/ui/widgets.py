import tkinter as tk
from tkinter import ttk


class ScrollColumn(ttk.Frame):
    """Keep controls reachable when a small window or advanced options overflow."""

    def __init__(self, parent, *, padding=0):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, background="#FFFFFF", width=1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=padding)
        self.body_window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.pending = None
        self.canvas.bind("<Configure>", self._schedule_layout)
        self.body.bind("<Configure>", self._schedule_layout)
        top = self.winfo_toplevel()
        top.bind("<FocusIn>", self._focus_changed, add=True)
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            top.bind(event, self._wheel, add=True)

    def _schedule_layout(self, _event=None):
        if self.pending is None:
            self.pending = self.after_idle(self._layout)

    def _layout(self):
        self.pending = None
        height = max(self.body.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.itemconfigure(self.body_window, width=self.canvas.winfo_width(), height=height)
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
        if height > self.canvas.winfo_height() + 1:
            if not self.scrollbar.winfo_manager():
                self.scrollbar.pack(side="right", fill="y")
        elif self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def _contains(self, widget):
        while isinstance(widget, tk.Misc):
            if widget == self.body:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _focus_changed(self, event):
        if self._contains(event.widget):
            self.after_idle(lambda: self.ensure_visible(event.widget))

    def ensure_visible(self, widget):
        if not widget.winfo_exists() or not widget.winfo_ismapped():
            return
        top = widget.winfo_rooty() - self.canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        viewport = self.canvas.winfo_height()
        if top < 0 or bottom > viewport:
            delta = top if top < 0 else bottom - viewport
            self.canvas.yview_moveto(
                max(0, (self.canvas.canvasy(0) + delta) / max(1, self.body.winfo_height()))
            )

    def _wheel(self, event):
        widget = event.widget
        if not self._contains(widget) or isinstance(
            widget, (tk.Text, tk.Canvas, ttk.Treeview, ttk.Combobox)
        ):
            return None
        if getattr(event, "num", None) in (4, 5):
            units = -3 if event.num == 4 else 3
        elif getattr(event, "delta", 0):
            units = (-1 if event.delta > 0 else 1) * max(1, abs(event.delta) // 120) * 3
        else:
            return None
        self.canvas.yview_scroll(units, "units")
        return "break"

    def destroy(self):
        if self.pending is not None:
            self.after_cancel(self.pending)
        super().destroy()


class BoundText(tk.Text):
    """A multiline field that keeps the existing StringVar controller contract."""

    def __init__(self, parent, variable, **kwargs):
        super().__init__(parent, **kwargs)
        self.variable = variable
        self._syncing = False
        self._trace = variable.trace_add("write", self._from_variable)
        self.bind("<<Modified>>", self._to_variable)
        self.bind("<Tab>", lambda _event: self._move_focus(False))
        self.bind("<Shift-Tab>", lambda _event: self._move_focus(True))
        try:
            self.bind("<ISO_Left_Tab>", lambda _event: self._move_focus(True))
        except tk.TclError:
            # Some Tk platform keymaps expose only Shift-Tab.
            pass
        self._from_variable()

    def _move_focus(self, backwards):
        target = self.tk_focusPrev() if backwards else self.tk_focusNext()
        if target:
            target.focus_set()
        return "break"

    def _from_variable(self, *_args):
        value = self.variable.get()
        if self.get("1.0", "end-1c") == value:
            return
        self._syncing = True
        self.delete("1.0", "end")
        self.insert("1.0", value)
        self.edit_modified(False)
        self._syncing = False

    def _to_variable(self, _event=None):
        if not self._syncing and self.edit_modified():
            self.variable.set(self.get("1.0", "end-1c"))
            self.edit_modified(False)

    def destroy(self):
        self.variable.trace_remove("write", self._trace)
        super().destroy()
