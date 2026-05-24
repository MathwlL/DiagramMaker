import tkinter as tk
from tkinter import ttk


class VisualBuilder(tk.Toplevel):

    def __init__(self, parent, app):

        super().__init__(parent)

        self.app = app

        self.title("Visual Builder")

        self.geometry("400x400")

        self.configure(bg="#1e1e1e")

        tk.Label(
            self,
            text="Visual Builder",
            bg="#1e1e1e",
            fg="white",
            font=("Segoe UI", 14, "bold")
        ).pack(pady=15)

        ttk.Button(
            self,
            text="Close",
            command=self.destroy
        ).pack(pady=20)