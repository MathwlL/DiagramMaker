import tkinter as tk

from core.constants import *
from core.layout import auto_layout


class ERCanvas(tk.Canvas):

    def __init__(self, parent, app):

        super().__init__(
            parent,
            bg="#1e1e1e",
            highlightthickness=0
        )

        self.app = app

        self.tables = {}
        self.relations = []
        self.positions = {}

        self.bind("<Configure>", self.on_resize)

    def on_resize(self, event):

        self.redraw()

    def load_schema(self, schema):

        self.tables = schema.get("tables", {})
        self.relations = schema.get("relations", [])

        self.positions = auto_layout(
            self.tables,
            self.relations,
            self.winfo_width(),
            self.winfo_height()
        )

        self.redraw()

    def redraw(self):

        self.delete("all")

        self.draw_relations()

        self.draw_tables()

    def draw_tables(self):

        for table_name, columns in self.tables.items():

            x, y = self.positions.get(table_name, (50, 50))

            height = HEADER_H + (len(columns) * ROW_H)

            self.create_rectangle(
                x,
                y,
                x + TABLE_W,
                y + height,
                fill="#2d2d30",
                outline="#4ec9b0",
                width=2
            )

            self.create_rectangle(
                x,
                y,
                x + TABLE_W,
                y + HEADER_H,
                fill="#007acc",
                outline=""
            )

            self.create_text(
                x + TABLE_W // 2,
                y + HEADER_H // 2,
                text=table_name,
                fill="white",
                font=FONT_HDR
            )

            cy = y + HEADER_H

            for col in columns:

                label = col["name"]

                if col.get("pk"):
                    label += " [PK]"

                if col.get("fk"):
                    label += " [FK]"

                self.create_text(
                    x + 10,
                    cy + ROW_H // 2,
                    anchor="w",
                    text=label,
                    fill="white",
                    font=FONT_COL
                )

                self.create_text(
                    x + TABLE_W - 10,
                    cy + ROW_H // 2,
                    anchor="e",
                    text=col.get("type", ""),
                    fill="#bbbbbb",
                    font=FONT_TYPE
                )

                cy += ROW_H

    def draw_relations(self):

        for rel in self.relations:

            from_table = rel["from"]
            to_table = rel["to"]

            if from_table not in self.positions:
                continue

            if to_table not in self.positions:
                continue

            x1, y1 = self.positions[from_table]
            x2, y2 = self.positions[to_table]

            start_x = x1 + TABLE_W
            start_y = y1 + HEADER_H

            end_x = x2
            end_y = y2 + HEADER_H

            self.create_line(
                start_x,
                start_y,
                end_x,
                end_y,
                fill="#dcdcaa",
                width=2,
                smooth=True
            )