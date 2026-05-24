import re


def _extract_table_bodies(sql):
    results = []

    pat = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?(\w+)[`"\]]?\s*\(',
        re.IGNORECASE
    )

    i = 0

    while i < len(sql):

        m = pat.search(sql, i)

        if not m:
            break

        tname = m.group(1)

        start = m.end()
        depth = 1
        j = start

        while j < len(sql) and depth > 0:

            if sql[j] == '(':
                depth += 1

            elif sql[j] == ')':
                depth -= 1

            j += 1

        body = sql[start:j - 1]

        results.append((tname.upper(), body))

        i = j

    return results


def _split_defs(text):

    parts = []
    depth = 0
    cur = []

    for ch in text:

        if ch == '(':
            depth += 1

        elif ch == ')':
            depth -= 1

        if ch == ',' and depth == 0:
            parts.append(''.join(cur).strip())
            cur = []

        else:
            cur.append(ch)

    if cur:
        parts.append(''.join(cur).strip())

    return parts


def parse_sql(sql: str) -> dict:

    tables = {}
    relations = []
    rel_seen = set()

    sql = re.sub(r'--[^\n]*', '', sql)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)

    CONSTRAINT_KW = re.compile(
        r'^(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT|KEY|INDEX|FULLTEXT|SPATIAL)\b',
        re.IGNORECASE
    )

    fk_pat = re.compile(
        r'(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)'
        r'\s+REFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
        re.IGNORECASE
    )

    col_pat = re.compile(
        r'^[`"\[]?(\w+)[`"\]]?\s+(\w+\s*(?:\([^)]*\))?)(.*)',
        re.IGNORECASE | re.DOTALL
    )

    for tname, body in _extract_table_bodies(sql):

        columns = []
        pk_cols = set()
        fk_map = {}

        pk_tbl = re.search(
            r'PRIMARY\s+KEY\s*\(([^)]+)\)',
            body,
            re.IGNORECASE
        )

        if pk_tbl:
            for c in pk_tbl.group(1).split(','):
                pk_cols.add(c.strip().strip('`"[] \t').upper())

        for fk in fk_pat.finditer(body):

            lcs = [
                c.strip().strip('`"[] \t').upper()
                for c in fk.group(1).split(',')
            ]

            rt = fk.group(2).upper()

            rcs = [
                c.strip().strip('`"[] \t').upper()
                for c in fk.group(3).split(',')
            ]

            for lc, rc in zip(lcs, rcs):

                fk_map[lc] = (rt, rc)

                key = (tname, lc, rt)

                if key not in rel_seen:

                    rel_seen.add(key)

                    relations.append({
                        "from": tname,
                        "from_col": lc,
                        "to": rt,
                        "to_col": rc,
                        "card_from": "1,1",
                        "card_to": "0,n",
                        "label_offset": [0, 0]
                    })

        for defn in _split_defs(body):

            defn = defn.strip()

            if not defn or CONSTRAINT_KW.match(defn):
                continue

            m = col_pat.match(defn)

            if not m:
                continue

            cn = m.group(1).upper()

            ct = re.sub(r'\s+', '', m.group(2)).upper()

            rest = m.group(3)

            is_pk = (
                cn in pk_cols or
                bool(re.search(r'\bPRIMARY\s+KEY\b', rest, re.IGNORECASE))
            )

            if is_pk:
                pk_cols.add(cn)

            ref_table = None

            ifk = re.search(
                r'\bREFERENCES\s+[`"\[]?(\w+)[`"\]]?\s*\(([^)]+)\)',
                rest,
                re.IGNORECASE
            )

            if ifk:

                ref_table = ifk.group(1).upper()

                rc = ifk.group(2).strip().strip('`"[] \t').upper()

                if cn not in fk_map:

                    fk_map[cn] = (ref_table, rc)

                    key = (tname, cn, ref_table)

                    if key not in rel_seen:

                        rel_seen.add(key)

                        relations.append({
                            "from": tname,
                            "from_col": cn,
                            "to": ref_table,
                            "to_col": rc,
                            "card_from": "1,1",
                            "card_to": "0,n",
                            "label_offset": [0, 0]
                        })

            if cn in fk_map:
                ref_table = fk_map[cn][0]

            columns.append({
                "name": cn,
                "type": ct,
                "pk": is_pk,
                "fk": cn in fk_map,
                "ref": ref_table
            })

        if columns:
            tables[tname] = columns

    return {
        "tables": tables,
        "relations": relations
    }