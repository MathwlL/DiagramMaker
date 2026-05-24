import tkinter as tk
from tkinter import ttk


class SettingsPanel(tk.Frame):

    def __init__(self, parent, app):

        super().__init__(
            parent,
            bg="#252526",
            width=250
        )

        self.app = app

        self.pack_propagate(False)

        title = tk.Label(
            self,
            text="Settings",
            bg="#252526",
            fg="white",
            font=("Segoe UI", 12, "bold")
        )

        title.pack(pady=10)

        ttk.Button(
            self,
            text="Auto Layout",
            command=self.auto_layout
        ).pack(
            fill="x",
            padx=10,
            pady=5
        )

    def auto_layout(self):

        if hasattr(self.app, "canvas"):

            self.app.canvas.positions = {}

            self.app.canvas.redraw()