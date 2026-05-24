"""Diagram registry and contracts.

The UI asks the registry for a diagram definition instead of hard-coding every
diagram type. New diagram families can register parser/renderer/export metadata
without changing the application shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

from core.parser import parse_sql


Parser = Callable[[str], dict]


@dataclass(frozen=True)
class DiagramDefinition:
    key: str
    label: str
    parser: Parser
    modes: tuple[str, ...]
    exportable: bool = True


class DiagramRegistry:
    def __init__(self):
        self._items: Dict[str, DiagramDefinition] = {}

    def register(self, definition: DiagramDefinition) -> None:
        if definition.key in self._items:
            raise ValueError(f"Diagram already registered: {definition.key}")
        self._items[definition.key] = definition

    def get(self, key: str) -> Optional[DiagramDefinition]:
        return self._items.get(key)

    def all(self) -> Iterable[DiagramDefinition]:
        return self._items.values()

    def parse(self, key: str, source: str) -> dict:
        definition = self.get(key)
        if not definition:
            raise ValueError(f"Unknown diagram type: {key}")
        return definition.parser(source)


registry = DiagramRegistry()
registry.register(
    DiagramDefinition(
        key="er",
        label="ER Database Diagram",
        parser=parse_sql,
        modes=("Conceitual", "Logico", "Fisico"),
    )
)
