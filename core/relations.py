CARDINALITIES = (
    "1,1",
    "0,1",
    "1,n",
    "0,n"
)


def _norm_name(value):
    return (value or "").strip().upper()


def relation_key(rel):

    return (
        _norm_name(rel.get("from")),
        _norm_name(rel.get("from_col")),
        _norm_name(rel.get("to")),
        _norm_name(rel.get("to_col")),
    )


def dedupe_relations(relations):

    cleaned = []
    seen = set()

    for rel in relations:

        key = relation_key(rel)

        if key in seen:
            continue

        seen.add(key)

        rel.setdefault("card_from", "1,1")
        rel.setdefault("card_to", "0,n")

        rel.setdefault("label_offset", [0, 0])

        rel.setdefault("label_offset_from", [0, 0])
        rel.setdefault("label_offset_to", [0, 0])

        rel.setdefault("waypoints", [])

        cleaned.append(rel)

    return cleaned