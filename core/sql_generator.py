def tables_to_sql(tables, relations):

    lines = []

    for tname, cols in tables.items():

        lines.append(f"CREATE TABLE {tname} (")

        col_defs = []
        fk_defs = []

        pk_cols = [
            c["name"]
            for c in cols
            if c["pk"]
        ]

        for col in cols:

            dtype = col.get("type", "VARCHAR(100)")

            if not dtype:
                dtype = "VARCHAR(100)"

            notnull = " NOT NULL" if col["pk"] else ""

            col_defs.append(
                f"    {col['name']} {dtype}{notnull}"
            )

        if pk_cols:

            col_defs.append(
                f"    PRIMARY KEY ({', '.join(pk_cols)})"
            )

        for col in cols:

            if col.get("fk") and col.get("ref"):

                for rel in relations:

                    if (
                        rel["from"] == tname and
                        rel.get("from_col", "").upper() == col["name"].upper()
                    ):

                        fk_defs.append(
                            f"    FOREIGN KEY ({col['name']}) "
                            f"REFERENCES {rel['to']}({rel.get('to_col', 'id')})"
                        )

                        break

        all_defs = col_defs + fk_defs

        lines.append(",\n".join(all_defs))

        lines.append(");\n")

    return "\n".join(lines)