# DiagramMaker

Desktop tool for creating, editing, exporting, and persisting database diagrams.

## Run

```powershell
python main.py
```

`main.py` is the supported entry point and contains the production Tk application.

## Current Features

- SQL `CREATE TABLE` parsing
- ER diagram rendering
- Conceptual, logical, and physical display modes
- Movable tables, relation labels, and relation bend points
- Visual table/relation builder
- SQL generation from the visual model
- PNG/JPEG/PDF export through Pillow viewport capture
- Project persistence through a NoSQL-style local JSON provider
- Blender-inspired dark workspace with top bar, tool rail, canvas, inspector, and status area

## Project Persistence

Projects are saved under `projects/` by `core.nosql.LocalJsonProvider`.

The provider API is intentionally small so MongoDB, Firebase, Supabase, CouchDB, or DynamoDB providers can be added later without changing the UI.

## Architecture Docs

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Build EXE

Use the existing PyInstaller spec or build script from the project root. The executable output is generated under `dist/`.
