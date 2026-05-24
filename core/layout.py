import math


def auto_layout(tables, relations, cw=1600, ch=1100):

    names = list(tables.keys())

    n = len(names)

    if not n:
        return {}

    cols_n = max(1, math.ceil(math.sqrt(n)))

    pos = {}

    for i, name in enumerate(names):

        r, c = divmod(i, cols_n)

        pos[name] = [
            80 + c * 210,
            80 + r * 180
        ]

    for _ in range(150):

        forces = {
            nm: [0.0, 0.0]
            for nm in names
        }

        for i in range(len(names)):

            for j in range(i + 1, len(names)):

                a, b = names[i], names[j]

                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]

                d = max(1, math.hypot(dx, dy))

                rep = 22000 / (d * d)

                fx = rep * dx / d
                fy = rep * dy / d

                forces[a][0] -= fx
                forces[a][1] -= fy

                forces[b][0] += fx
                forces[b][1] += fy

        for rel in relations:

            f = rel["from"]
            t = rel["to"]

            if f not in pos or t not in pos:
                continue

            dx = pos[t][0] - pos[f][0]
            dy = pos[t][1] - pos[f][1]

            d = max(1, math.hypot(dx, dy))

            att = d * 0.055

            fx = att * dx / d
            fy = att * dy / d

            forces[f][0] += fx
            forces[f][1] += fy

            forces[t][0] -= fx
            forces[t][1] -= fy

        for nm in names:

            pos[nm][0] = max(
                60,
                min(cw - 180,
                    pos[nm][0] + max(-25, min(25, forces[nm][0])))
            )

            pos[nm][1] = max(
                60,
                min(ch - 140,
                    pos[nm][1] + max(-25, min(25, forces[nm][1])))
            )

    return {
        nm: (int(p[0]), int(p[1]))
        for nm, p in pos.items()
    }