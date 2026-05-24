# DiagramMaker Architecture

## Application Flow

`main.py` is the stable entry point and currently hosts the production Tk application while core services are split into modules.

Current runtime flow:

1. `main.py`
2. SQL editor / visual builder / ER canvas
3. `core.parser.parse_sql`
4. `core.layout.auto_layout`
5. `ERCanvas` render/update/export
6. Optional project persistence through `core.nosql.LocalJsonProvider`

## Folder Structure

```text
DiagramMaker/
  main.py                  # production entry point and current full Tk application
  style.css                # Blender-like theme variables
  core/
    parser.py              # SQL parsing
    sql_generator.py       # SQL generation
    layout.py              # automatic diagram layout
    relations.py           # relation normalization/deduping
    diagrams.py            # diagram registry
    nosql.py               # NoSQL provider abstraction
  ui/
    canvas.py              # early modular canvas, kept compatible
    settings_panel.py      # settings panel shell
    visual_builder.py      # visual builder shell
  assets/
  docs/
```

## Diagram Architecture

Diagram types are registered in `core.diagrams.registry`.

A diagram definition has:

- `key`: stable identifier, for example `er`
- `label`: display name
- `parser`: source-to-model parser function
- `modes`: supported display modes
- `exportable`: whether export is supported

To add a new diagram:

1. Create a parser that returns a dictionary payload.
2. Add an independent renderer module.
3. Register a `DiagramDefinition` in `core.diagrams`.
4. Keep UI state separate from diagram data.

## NoSQL Architecture

`core.nosql` defines a provider contract:

- `create(project)`
- `get(project_id)`
- `list()`
- `update(project_id, payload)`
- `delete(project_id)`

The default provider is `LocalJsonProvider`, which stores project JSON files under `projects/`. It is intentionally shaped like a NoSQL document store so future providers can map directly to:

- MongoDB collections
- Firebase documents
- Supabase rows/jsonb
- CouchDB documents
- DynamoDB items

Project payloads are flexible JSON documents containing:

- tables
- relations
- positions
- diagram mode
- notation
- SQL source

Every update increments `version` and refreshes `updated_at`.

## UI Direction

The UI follows a Blender/Photoshop-like workspace:

- top menu/options bar
- left tool rail
- central canvas
- right properties panel
- bottom status feedback
- dark neutral theme controlled by `style.css`

Theme values are CSS custom properties loaded by the Tk application. Add new color tokens in `style.css` and map them in `CSS_THEME_MAP` when Python needs to consume them.

## Maintenance Rules

- Keep new services under `core/` and UI panels under `ui/`.
- Move behavior out of `main.py` incrementally when a module boundary is clear.
- Avoid circular imports: core modules must not import UI modules.
- Providers must implement the NoSQL contract without UI dependencies.
- Renderers should read normalized diagram data and avoid owning persistence.
