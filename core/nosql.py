"""NoSQL persistence abstraction for diagram projects.

Providers implement the same small contract, so MongoDB/Firebase/Supabase/etc.
can be added without coupling the UI to a specific backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol
import json
import uuid


DiagramPayload = Dict[str, Any]


@dataclass
class DiagramProject:
    id: str
    name: str
    diagram_type: str
    payload: DiagramPayload
    version: int = 1
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "diagram_type": self.diagram_type,
            "payload": self.payload,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiagramProject":
        return cls(
            id=data["id"],
            name=data.get("name", "Untitled"),
            diagram_type=data.get("diagram_type", "er"),
            payload=data.get("payload", {}),
            version=int(data.get("version", 1)),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
        )


class NoSQLProvider(Protocol):
    def create(self, project: DiagramProject) -> DiagramProject: ...
    def get(self, project_id: str) -> Optional[DiagramProject]: ...
    def list(self) -> List[DiagramProject]: ...
    def update(self, project_id: str, payload: DiagramPayload) -> DiagramProject: ...
    def delete(self, project_id: str) -> None: ...


class ProviderError(RuntimeError):
    pass


class LocalJsonProvider:
    """File-backed provider used for production-safe local persistence."""

    def __init__(self, root: str | Path = "projects"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, project: DiagramProject) -> DiagramProject:
        if not project.id:
            project.id = uuid.uuid4().hex
        self._write(project)
        return project

    def get(self, project_id: str) -> Optional[DiagramProject]:
        path = self._path(project_id)
        if not path.exists():
            return None
        try:
            return DiagramProject.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            raise ProviderError(f"Could not load project {project_id}: {exc}") from exc

    def list(self) -> List[DiagramProject]:
        projects = []
        for path in sorted(self.root.glob("*.json")):
            project = self.get(path.stem)
            if project:
                projects.append(project)
        return projects

    def update(self, project_id: str, payload: DiagramPayload) -> DiagramProject:
        project = self.get(project_id)
        if not project:
            raise ProviderError(f"Project not found: {project_id}")
        project.payload = payload
        project.version += 1
        project.updated_at = _now_iso()
        self._write(project)
        return project

    def delete(self, project_id: str) -> None:
        path = self._path(project_id)
        if path.exists():
            path.unlink()

    def _path(self, project_id: str) -> Path:
        return self.root / f"{project_id}.json"

    def _write(self, project: DiagramProject) -> None:
        try:
            self._path(project.id).write_text(
                json.dumps(project.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ProviderError(f"Could not save project {project.id}: {exc}") from exc


def make_project(name: str, diagram_type: str, payload: DiagramPayload) -> DiagramProject:
    return DiagramProject(id=uuid.uuid4().hex, name=name, diagram_type=diagram_type, payload=payload)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
