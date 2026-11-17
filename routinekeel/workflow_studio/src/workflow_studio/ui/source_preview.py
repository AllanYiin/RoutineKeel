"""Display saved source bytes; images are fitted without stretching."""

import io
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageOps, ImageTk

from ..ingestion.preview import fit_size


class SourcePreview(ttk.Frame):
    def __init__(self, parent, *, font):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.caption = tk.StringVar()
        ttk.Label(self, textvariable=self.caption, wraplength=500).grid(
            row=0, column=0, sticky="ew", pady=4
        )
        self.text = ScrolledText(self, width=1, height=5, wrap="word", state="disabled", font=font)
        self.canvas = tk.Canvas(self, width=1, height=1, background="#FFFFFF", highlightthickness=0)
        self.text.grid(row=1, column=0, sticky="nsew")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.grid_remove()
        self.original = self.rendered = None
        self.canvas.bind("<Configure>", lambda _event: self._render())

    def show(self, name, content, preview):
        self.caption.set(f"{name} · {len(content)} bytes")
        self.original = self.rendered = None
        self.canvas.delete("all")
        if preview.get("kind") == "image":
            with Image.open(io.BytesIO(content)) as source:
                self.original = ImageOps.exif_transpose(source).copy()
            self.text.grid_remove()
            self.canvas.grid()
            self._render()
        else:
            self.canvas.grid_remove()
            self.text.grid()
            try:
                decoded = content.decode("utf-8-sig")
                shown = decoded[:16000] + ("\n（預覽已截斷）" if len(decoded) > 16000 else "")
            except UnicodeDecodeError:
                shown = "此來源為二進位檔案，沒有可用的文字預覽。"
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", shown)
            self.text.configure(state="disabled")

    def _render(self):
        if self.original is None:
            return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2 or height < 2:
            return
        size = fit_size(
            self.original.width, self.original.height, max(1, width - 12), max(1, height - 12)
        )
        self.rendered = ImageTk.PhotoImage(self.original.resize(size, Image.Resampling.LANCZOS))
        self.canvas.delete("all")
        self.canvas.create_image(width // 2, height // 2, image=self.rendered)
