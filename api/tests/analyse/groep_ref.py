"""De referentie van group in puur Python (fase 6, X2 "Testen zonder echte gewichten", laag 1;
X2b). Zonder numpy, voor hooguit ±2.000 gezichten.

Hetzelfde als app/analyse/worker/ops/group.py (lees daar de regels), maar in float64 en met
lijsten. De nep-worker gebruikt hem voor group (tests/analyse/faces_nep.py), en
tests_analyse/test_worker_group.py vergelijkt hem met numpy.
"""

from __future__ import annotations

import math
import struct

from app.analyse import wire

SAMPLE = 32
MERGE_SLACK = 0.05
REF_MAX = 2_000


def parse(data: bytes) -> tuple[list[list[float]], int, list[int], int, list[tuple[int, int]]]:
    magic, n, known, persons, rejects = wire.GROUP_HEAD.unpack_from(data)
    if magic != wire.GROUP_MAGIC:
        raise ValueError("geen PFG1")
    size = wire.GROUP_HEAD.size + n * wire.FACE_VEC_BYTES + known * 4 + rejects * 8
    if len(data) != size:
        raise ValueError("andere grootte")
    at = wire.GROUP_HEAD.size
    flat = struct.unpack_from(f"<{n * wire.FACE_DIM}f", data, at)
    vecs = [list(flat[i * wire.FACE_DIM : (i + 1) * wire.FACE_DIM]) for i in range(n)]
    at += n * wire.FACE_VEC_BYTES
    person = list(struct.unpack_from(f"<{known}i", data, at))
    at += known * 4
    flat_rej = struct.unpack_from(f"<{rejects * 2}i", data, at)
    rej = [(flat_rej[2 * i], flat_rej[2 * i + 1]) for i in range(rejects)]
    if any(not math.isfinite(x) for x in flat):
        raise ValueError("niet eindig")
    return vecs, known, person, persons, rej


def dot(a: list[float], b: list[float]) -> float:
    return math.sumprod(a, b)


def suggest(vecs, known, person, rejects, t_suggest, margin) -> list[tuple[int, float]]:
    """(voorstel of −1, score of 0) per open gezicht."""
    rejected = set(rejects)
    out = []
    for j in range(known, len(vecs)):
        best: dict[int, float] = {}
        for i in range(known):
            p = person[i]
            s = dot(vecs[i], vecs[j])
            if s > best.get(p, -math.inf):
                best[p] = s
        for p in list(best):
            if (j, p) in rejected:
                best[p] = -math.inf
        if not best:
            out.append((-1, 0.0))
            continue
        ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
        top_p, top_s = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -math.inf
        if top_s >= t_suggest and top_s - second >= margin:
            out.append((top_p, top_s))
        else:
            out.append((-1, 0.0))
    return out


def groups(open_vecs, k, t_link, min_group) -> list[int]:
    m = len(open_vecs)
    if m > REF_MAX:
        raise ValueError(f"de referentie doet hooguit {REF_MAX} gezichten")
    # de bovendriehoek één keer, alleen wat ≥ t_link is
    near: list[list[tuple[float, int]]] = [[] for _ in range(m)]
    for i in range(m):
        vi = open_vecs[i]
        for j in range(i + 1, m):
            s = dot(vi, open_vecs[j])
            if s >= t_link:
                near[i].append((s, j))
                near[j].append((s, i))
    top = [{j for _s, j in sorted(row, key=lambda sj: (-sj[0], sj[1]))[:k]} for row in near]
    edges = []
    for i in range(m):
        for s, j in near[i]:
            if i < j and j in top[i] and i in top[j]:
                edges.append((s, i, j))
    edges.sort(key=lambda e: (-e[0], e[1], e[2]))
    parent = list(range(m))
    members = {i: [i] for i in range(m)}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    floor = t_link - MERGE_SLACK
    for _s, i, j in edges:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        si = sorted(members[ri])[:SAMPLE]
        sj = sorted(members[rj])[:SAMPLE]
        mean = math.fsum(dot(open_vecs[a], open_vecs[b]) for a in si for b in sj) / (
            len(si) * len(sj)
        )
        if mean < floor:
            continue
        parent[rj] = ri
        members[ri] += members.pop(rj)
    roots = [find(x) for x in range(m)]
    sizes: dict[int, list[int]] = {}
    for x, r in enumerate(roots):
        sizes.setdefault(r, []).append(x)
    big = [(len(v), min(v), r) for r, v in sizes.items() if len(v) >= min_group]
    big.sort(key=lambda t: (-t[0], t[1]))
    number = {r: g for g, (_n, _first, r) in enumerate(big)}
    return [number.get(r, -1) for r in roots]


def run(data: bytes, args: wire.GroupArgs) -> bytes:
    """De uitvoer (n × GROUP_OUT) voor de invoer data."""
    vecs, known, person, _persons, rejects = parse(data)
    sugg = suggest(vecs, known, person, rejects, args.t_suggest, args.margin)
    grp = [-1] * (len(vecs) - known)
    if args.mode == "full":
        grp = groups(vecs[known:], args.k, args.t_link, args.min_group)
    out = bytearray()
    for _i in range(known):
        out += wire.GROUP_OUT.pack(-1, 0.0, -1)
    for (p, s), g in zip(sugg, grp, strict=True):
        out += wire.GROUP_OUT.pack(p, s, g)
    return bytes(out)
