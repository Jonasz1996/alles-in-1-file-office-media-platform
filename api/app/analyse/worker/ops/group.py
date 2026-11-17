"""group: gezichten van één eigenaar groeperen en voorstellen doen (fase 6, X2 "Groeperen en
voorstellen", Keuze 62; X2b).

Alleen in het kind werk. De worker ziet indexen, geen UUID's, namen of nodes.

**De invoer** is een verzegelde memfd van de app (wire.GROUP_HEAD): 'PFG1', n, known, persons,
rejects; dan n × 512 B vectoren (eerst de `known` bevestigde, dan de open), known × int32
persoon-index, rejects × (int32 gezicht, int32 persoon). Alles wordt nagemeten: de zegels, de
precieze grootte, de grenzen van elke index en dat elke waarde eindig is; anders bad_request.

**De uitvoer** gaat in de memfd uit: per gezicht (int32 voorstel, float32 score, int32 groep),
n × 12 B. −1 betekent geen voorstel of geen groep; een bevestigd gezicht krijgt (−1, 0, −1).

**Voorstellen** (mode suggest, en ook bij full): per open gezicht per persoon de hoogste cosinus
met zijn bevestigde gezichten (np.maximum.reduceat, in blokken van 512). Een afgewezen paar telt
niet. De beste persoon (bij gelijke stand de laagste index) wordt het voorstel als zijn score
≥ t_suggest is en hij minstens margin boven de tweede ligt. De score is dan die cosinus, anders 0.

**Groepen** (mode full), alleen over de open gezichten:

1. per gezicht de buren met cosinus ≥ t_link, gesorteerd op (−cosinus, index), de eerste k
   (zonder zichzelf); eerst de drempel, dan pas sorteren, in blokken van 512;
2. alleen wederzijdse buren worden een rand;
3. union-find, de sterkste rand eerst (bij gelijke stand op (i, j)); twee groepen gaan pas samen
   als de gemiddelde cosinus tussen hun steekproeven ≥ t_link − 0,05 is. De steekproef is per
   groep de eerste 32 leden op index;
4. groepen van ≥ min_group krijgen een nummer, de grootste eerst (bij gelijke stand de laagste
   kleinste index), vanaf 0; de rest −1.

api/tests/analyse/groep_ref.py doet hetzelfde in puur Python; tests_analyse/test_worker_group.py
vergelijkt ze.
"""

from __future__ import annotations

import os

import numpy as np

from app.analyse import wire
from app.analyse.wire import Code
from app.analyse.worker.child import Job, OpFailed

BLOCK = 512
SAMPLE = 32
MERGE_SLACK = 0.05
# Hooguit zoveel gezichten per opdracht: de open (PLATFORM_FACES_GROUP_MAX, 60.000) plus de
# bevestigde. De app stuurt er nooit meer; het houdt een fout verzoek klein.
N_MAX = 120_000
REJECTS_MAX = 1_000_000
OUT = np.dtype([("s", "<i4"), ("f", "<f4"), ("g", "<i4")])


def _bad(detail: str) -> OpFailed:
    return OpFailed(Code.BAD_REQUEST, detail)


def parse(data: bytes) -> tuple[np.ndarray, int, np.ndarray, int, np.ndarray]:
    """(vectoren n × 128 float32, known, persoon per bevestigd gezicht, persons, rejects × 2)."""
    head = wire.GROUP_HEAD.size
    if len(data) < head:
        raise _bad("invoer te kort")
    magic, n, known, persons, rejects = wire.GROUP_HEAD.unpack_from(data)
    if magic != wire.GROUP_MAGIC:
        raise _bad("invoer zonder PFG1")
    if n > N_MAX or known > n or rejects > REJECTS_MAX or persons > max(known, 0):
        raise _bad("aantallen buiten de grenzen")
    size = head + n * wire.FACE_VEC_BYTES + known * 4 + rejects * 8
    if len(data) != size:
        raise _bad("invoer heeft een andere grootte")
    at = head
    vecs = np.frombuffer(data, dtype="<f4", count=n * wire.FACE_DIM, offset=at)
    vecs = vecs.reshape(n, wire.FACE_DIM)
    at += n * wire.FACE_VEC_BYTES
    person = np.frombuffer(data, dtype="<i4", count=known, offset=at)
    at += known * 4
    rej = np.frombuffer(data, dtype="<i4", count=rejects * 2, offset=at).reshape(rejects, 2)
    if not np.isfinite(vecs).all():
        raise _bad("een waarde is niet eindig")
    if known and (person.min() < 0 or person.max() >= persons):
        raise _bad("persoon-index buiten de grenzen")
    if rejects and (
        rej[:, 0].min() < known
        or rej[:, 0].max() >= n
        or rej[:, 1].min() < 0
        or rej[:, 1].max() >= persons
    ):
        raise _bad("afwijzing buiten de grenzen")
    return np.ascontiguousarray(vecs), known, person, persons, rej


def suggest(
    vecs: np.ndarray,
    known: int,
    person: np.ndarray,
    persons: int,
    rejects: np.ndarray,
    args: wire.GroupArgs,
    job: Job,
) -> tuple[np.ndarray, np.ndarray]:
    """(voorstel per open gezicht of −1, score of 0) voor de open gezichten known..n."""
    n = len(vecs)
    m = n - known
    best_p = np.full(m, -1, dtype=np.int32)
    best_s = np.zeros(m, dtype=np.float32)
    if m == 0 or known == 0 or persons == 0:
        return best_p, best_s
    order = np.argsort(person, kind="stable")
    conf = vecs[:known][order]
    sorted_p = person[order]
    present = np.unique(sorted_p)  # personen met minstens één bevestigd gezicht, oplopend
    starts = np.searchsorted(sorted_p, present)
    # afwijzingen per open gezicht (relatief) en kolom in present
    rej_rows = rejects[:, 0] - known if len(rejects) else np.zeros(0, dtype=np.int64)
    rej_cols = np.searchsorted(present, rejects[:, 1]) if len(rejects) else rej_rows
    if len(rejects):
        hit = present[np.minimum(rej_cols, len(present) - 1)] == rejects[:, 1]
        rej_rows, rej_cols = rej_rows[hit], rej_cols[hit]
    for b0 in range(0, m, BLOCK):
        job.stop.check()
        b1 = min(b0 + BLOCK, m)
        sims = vecs[known + b0 : known + b1] @ conf.T
        per = np.maximum.reduceat(sims, starts, axis=1)  # (rijen, len(present))
        if len(rej_rows):
            sel = (rej_rows >= b0) & (rej_rows < b1)
            per[rej_rows[sel] - b0, rej_cols[sel]] = -np.inf
        top = np.argmax(per, axis=1)
        rows = np.arange(b1 - b0)
        first = per[rows, top]
        if per.shape[1] > 1:
            rest = per.copy()
            rest[rows, top] = -np.inf
            second = rest.max(axis=1)
        else:
            second = np.full(b1 - b0, -np.inf, dtype=per.dtype)
        ok = (first >= args.t_suggest) & ((first - second) >= args.margin)
        best_p[b0:b1] = np.where(ok, present[top], -1)
        best_s[b0:b1] = np.where(ok, first, 0.0)
    return best_p, best_s


def neighbours(open_vecs: np.ndarray, args: wire.GroupArgs, job: Job) -> np.ndarray:
    """De gerichte buren (i, j, cosinus): per i hooguit k, cosinus ≥ t_link, op (−cos, j)."""
    m = len(open_vecs)
    parts_i, parts_j, parts_s = [], [], []
    for b0 in range(0, m, BLOCK):
        job.stop.check()
        b1 = min(b0 + BLOCK, m)
        sims = open_vecs[b0:b1] @ open_vecs.T
        rows = np.arange(b1 - b0)
        sims[rows, rows + b0] = -np.inf
        r, c = np.nonzero(sims >= args.t_link)
        if not len(r):
            continue
        s = sims[r, c]
        order = np.lexsort((c, -s, r))
        r, c, s = r[order], c[order], s[order]
        # rang binnen de rij: positie min het begin van die rij
        starts = np.searchsorted(r, r, side="left")
        keep = (np.arange(len(r)) - starts) < args.k
        parts_i.append(r[keep] + b0)
        parts_j.append(c[keep])
        parts_s.append(s[keep])
        del sims
    if not parts_i:
        return np.zeros(0, dtype=[("i", "<i8"), ("j", "<i8"), ("s", "<f4")])
    out = np.zeros(sum(len(p) for p in parts_i), dtype=[("i", "<i8"), ("j", "<i8"), ("s", "<f4")])
    out["i"] = np.concatenate(parts_i)
    out["j"] = np.concatenate(parts_j)
    out["s"] = np.concatenate(parts_s)
    return out


def mutual(directed: np.ndarray, m: int) -> np.ndarray:
    """De randen (i < j) die in beide richtingen buren zijn, de sterkste eerst, dan (i, j)."""
    if not len(directed):
        return directed
    keys = directed["i"] * m + directed["j"]
    sorted_keys = np.sort(keys)
    rev = directed["j"] * m + directed["i"]
    pos = np.minimum(np.searchsorted(sorted_keys, rev), len(sorted_keys) - 1)
    both = (sorted_keys[pos] == rev) & (directed["i"] < directed["j"])
    edges = directed[both]
    order = np.lexsort((edges["j"], edges["i"], -edges["s"]))
    return edges[order]


def groups(open_vecs: np.ndarray, args: wire.GroupArgs, job: Job) -> np.ndarray:
    """Groep per open gezicht (−1 als zijn groep kleiner is dan min_group)."""
    m = len(open_vecs)
    out = np.full(m, -1, dtype=np.int32)
    if m == 0:
        return out
    edges = mutual(neighbours(open_vecs, args, job), m)
    parent = list(range(m))
    size = [1] * m
    sample: dict[int, list[int]] = {}

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    floor = args.t_link - MERGE_SLACK
    for count, (i, j, _s) in enumerate(edges.tolist()):
        if count % 4096 == 0:
            job.stop.check()
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        si = sample.get(ri, [ri])
        sj = sample.get(rj, [rj])
        mean = float((open_vecs[si] @ open_vecs[sj].T).mean(dtype=np.float64))
        if mean < floor:
            continue
        if size[ri] < size[rj] or (size[ri] == size[rj] and rj < ri):
            ri, rj = rj, ri
        parent[rj] = ri
        size[ri] += size[rj]
        sample[ri] = sorted(si + sj)[:SAMPLE]
        sample.pop(rj, None)
    roots = np.fromiter((find(x) for x in range(m)), dtype=np.int64, count=m)
    labels, first, counts = np.unique(roots, return_index=True, return_counts=True)
    big = counts >= args.min_group
    order = np.lexsort((first[big], -counts[big]))
    number = np.full(len(labels), -1, dtype=np.int32)
    number[np.flatnonzero(big)[order]] = np.arange(int(big.sum()), dtype=np.int32)
    out[:] = number[np.searchsorted(labels, roots)]
    return out


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        offset += os.pwrite(fd, view[offset:], offset)


def run(job: Job) -> dict:
    try:
        args = wire.GroupArgs.model_validate(job.args)
    except ValueError:
        raise OpFailed(Code.BAD_REQUEST, "args van group") from None
    if len(job.fds) != 2:
        raise _bad("group vraagt in en uit")
    src, out = job.fds
    if not wire.is_sealed(src):
        raise _bad("in is niet verzegeld")
    limit = wire.GROUP_HEAD.size + N_MAX * wire.FACE_VEC_BYTES + N_MAX * 4 + REJECTS_MAX * 8
    try:
        data = wire.read_all(src, limit)
    except (OSError, ValueError):
        raise _bad("in is niet te lezen") from None
    try:
        os.ftruncate(out, 0)
    except OSError:
        raise _bad("uit is geen memfd om in te schrijven") from None
    vecs, known, person, persons, rejects = parse(data)
    del data
    n = len(vecs)
    result = np.zeros(n, dtype=OUT)
    result["s"] = -1
    result["g"] = -1
    best_p, best_s = suggest(vecs, known, person, persons, rejects, args, job)
    result["s"][known:] = best_p
    result["f"][known:] = best_s
    if args.mode == "full":
        result["g"][known:] = groups(np.ascontiguousarray(vecs[known:]), args, job)
    job.stop.check()
    _write_all(out, result.tobytes())
    return wire.GroupData(n=n).model_dump(mode="json")
