import tkinter as tk


class BoundText(tk.Text):
    """A multiline field that keeps the existing StringVar controller contract."""

    def __init__(self, parent, variable, **kwargs):
        super().__init__(parent, **kwargs)
        self.variable = variable
        self._syncing = False
        self._trace = variable.trace_add("write", self._from_variable)
        self.bind("<<Modified>>", self._to_variable)
        self._from_variable()

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
