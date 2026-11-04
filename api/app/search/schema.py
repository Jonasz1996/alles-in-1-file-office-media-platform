"""Het schema van de zoekindex zoals migratie 0010 het maakt (bouwontwerp §3.1).

De migratie heeft een eigen, bevroren kopie van deze SQL. tests/search/test_schema_triggers.py
eist dat de triggers in sqlite_master gelijk zijn aan TRIGGERS hier, en de kopie in de migratie
aan TRIGGERS_0010; en tests/search/test_ftype.py dat FTYPE_SQL hetzelfde zegt als mime.ftype().
Wie hier iets verandert, schrijft dus ook een nieuwe migratie.

De macro's van het ontwerp staan hieronder als functies: IN(n), DEL(w), INS(w), NDEL(n, s),
NINS(n, s), END(p) en DEAD_SUBTREE.

- node.num is een vast nummer per node (uit search_seq), de rowid in beide FTS-tabellen.
- node.mpath is het pad van nummers, '/1/7/42/': een submap is één bereik op ix_node_mpath.
- node.dead is 1 als de node of een map erboven in de prullenbak ligt.
- node.ftype is de soort (mime.FTYPES), een virtuele kolom zonder trigger.

Zonder FTS5 (een SQLite zonder die module) maakt de migratie geen view en geen FTS-tabellen,
en alleen de triggers die nummer, pad, dead en de foto-rijen bijhouden: TRIGGERS_NO_FTS. Zoeken
gaat dan via LikeFallback (engine.select_engine).
"""

from __future__ import annotations

from app import mime

# --- De soort per node -------------------------------------------------------------------


def _sql_list(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(values))


# Dezelfde volgorde als mime.ftype(): eerst rekenbladen en presentaties, dan de grove soort
# van mime.category(), met een archief als "other". node.mime komt altijd uit mime.sniff()
# (kleine letters, zonder parameters).
_DOCUMENTS = mime.DOCUMENT_TYPES - mime.SPREADSHEET_TYPES - mime.PRESENTATION_TYPES
FTYPE_SQL = (
    "CASE WHEN kind = 'dir' THEN 'folder'"
    " WHEN kind <> 'file' OR mime IS NULL OR mime = '' THEN 'other'"
    f" WHEN mime IN ({_sql_list(mime.SPREADSHEET_TYPES)}) THEN 'spreadsheet'"
    f" WHEN mime IN ({_sql_list(mime.PRESENTATION_TYPES)}) THEN 'presentation'"
    f" WHEN mime IN ({_sql_list(mime.ARCHIVE_TYPES)}) THEN 'other'"
    " WHEN mime = 'application/pdf' THEN 'pdf'"
    " WHEN mime LIKE 'image/%' THEN 'image'"
    " WHEN mime LIKE 'video/%' THEN 'video'"
    " WHEN mime LIKE 'audio/%' THEN 'audio'"
    f" WHEN mime LIKE 'text/%' OR mime IN ({_sql_list(_DOCUMENTS)}) THEN 'document'"
    " ELSE 'other' END"
)

# --- Kolommen en indexen op bestaande tabellen -------------------------------------------

NODE_COLUMNS = (
    "ALTER TABLE node ADD COLUMN num INTEGER",
    "ALTER TABLE node ADD COLUMN mpath TEXT",
    "ALTER TABLE node ADD COLUMN dead INTEGER NOT NULL DEFAULT 0",
    f"ALTER TABLE node ADD COLUMN ftype TEXT GENERATED ALWAYS AS ({FTYPE_SQL}) VIRTUAL",
)
NODE_INDEXES = {
    "ix_node_num": "CREATE UNIQUE INDEX ix_node_num ON node (num)",
    "ix_node_mpath": "CREATE INDEX ix_node_mpath ON node (mpath)",
    "ix_node_trash": "CREATE INDEX ix_node_trash ON node (mpath) WHERE deleted_at IS NOT NULL",
    "ix_node_live_mod": (
        "CREATE INDEX ix_node_live_mod ON node (modified_at DESC, num DESC) WHERE dead = 0"
    ),
    "ix_node_ftype_mod": (
        "CREATE INDEX ix_node_ftype_mod ON node (ftype, modified_at DESC, num DESC) WHERE dead = 0"
    ),
    "ix_node_owner_blob": (
        "CREATE INDEX ix_node_owner_blob ON node (owner_id, blob_id) WHERE dead = 0"
    ),
}
ALBUM_ITEM_COLUMNS = (
    "ALTER TABLE album_item ADD COLUMN source TEXT NOT NULL DEFAULT 'hand'",
    "ALTER TABLE album_item ADD COLUMN shared INTEGER NOT NULL DEFAULT 1",
)

# --- Teller en stand van een herbouw -----------------------------------------------------

SEQ_TABLES = {
    "search_seq": (
        "CREATE TABLE search_seq (id INTEGER PRIMARY KEY CHECK (id = 1), next INTEGER NOT NULL)"
    ),
    "search_state": (
        "CREATE TABLE search_state (id INTEGER PRIMARY KEY CHECK (id = 1), cursor INTEGER, "
        "started_at TEXT, what TEXT)"
    ),
}

# --- De view en de FTS-tabellen ----------------------------------------------------------


def ss(x: str) -> str:
    """SS(x): ß en ẞ als "ss" in de index; de opgeslagen tekst houdt de ß (Keuze 21)."""
    return f"replace(replace({x}, 'ß', 'ss'), 'ẞ', 'ss')"


_BODY = ss("coalesce(t.body, '')")
_OCR = ss("coalesce(t.ocr, '')")
# OCR van een foto (X1) in een eigen kolom: die tekst is alleen van de eigenaar (Keuze 16, 23,
# 25a). Een vraag van een ander sluit de kolom uit met een kolomfilter, {name extra body ocr
# meta}: …; zo komt een treffer, zijn rang en zijn snippet nooit uit pocr (review Z0, veilig F3).
_POCR = ss("coalesce(po.text, '')")
_META = ss("coalesce(t.meta, '')")
_EXTRA = ss(
    "coalesce((SELECT group_concat(m.title, ' ') FROM media_item m WHERE m.node_id = n.id), '')"
)
# Ook albums staan erin: dan klopt de 'delete' in FTS altijd met wat erin staat. De vraag
# sluit ze uit (Keuze 4).
VIEW_SQL = (
    "CREATE VIEW search_src AS\n"
    "  SELECT n.num AS num, n.blob_id AS blob_id, n.id AS node_id, n.search_name AS name,\n"
    f"         {_EXTRA} AS extra,\n"
    f"         {_BODY} AS body,\n"
    f"         {_OCR} AS ocr,\n"
    f"         {_META} AS meta,\n"
    f"         {_POCR} AS pocr\n"
    "  FROM node n\n"
    "  LEFT JOIN blob_text t ON t.blob_sha = n.blob_id\n"
    "  LEFT JOIN photo_ocr po ON po.node_id = n.id"
)
FTS_TABLES = {
    "fts_node": (
        "CREATE VIRTUAL TABLE fts_node USING fts5(name, extra, body, ocr, meta, pocr, "
        "content='search_src', content_rowid='num', tokenize='unicode61 remove_diacritics 2')"
    ),
    "fts_name": (
        "CREATE VIRTUAL TABLE fts_name USING fts5(search_name, "
        "content='node', content_rowid='num', tokenize='trigram')"
    ),
}

# --- De macro's --------------------------------------------------------------------------

_CURSOR = "(SELECT cursor FROM search_state WHERE id = 1)"
_COLS = "name, extra, body, ocr, meta, pocr"


def in_(n: str) -> str:
    """IN(n): buiten een herbouw (cursor NULL) alles; tijdens een herbouw alleen wat al opnieuw
    gevuld is (Keuze 12)."""
    return f"({_CURSOR} IS NULL OR {n} <= {_CURSOR})"


def del_(w: str) -> str:
    """DEL(w): de rijen van de view die aan w voldoen uit fts_node, met hun huidige inhoud."""
    return (
        f"INSERT INTO fts_node(fts_node, rowid, {_COLS}) "
        f"SELECT 'delete', num, {_COLS} FROM search_src WHERE ({w}) AND {in_('num')};"
    )


def ins(w: str) -> str:
    """INS(w): de rijen van de view die aan w voldoen in fts_node."""
    return (
        f"INSERT INTO fts_node(rowid, {_COLS}) "
        f"SELECT num, {_COLS} FROM search_src WHERE ({w}) AND {in_('num')};"
    )


def ndel(n: str, s: str) -> str:
    """NDEL(n, s): de naam s met nummer n uit fts_name."""
    return (
        f"INSERT INTO fts_name(fts_name, rowid, search_name) SELECT 'delete', {n}, {s} "
        f"WHERE {in_(n)};"
    )


def nins(n: str, s: str) -> str:
    """NINS(n, s): de naam s met nummer n in fts_name."""
    return f"INSERT INTO fts_name(rowid, search_name) SELECT {n}, {s} WHERE {in_(n)};"


def end(p: str) -> str:
    """END(p): de eerste tekst na alle paden onder p ('/1/7/' → '/1/70'; '/' < '0')."""
    return f"substr({p}, 1, length({p}) - 1) || '0'"


def dead_subtree() -> str:
    """DEAD_SUBTREE: dead opnieuw voor de submap van NEW. CUR is het pad van nu; NEW.mpath is in
    de trigger nog het oude. Twee stappen, elk lineair: eerst de hele submap op de dead van de
    ouder, dan elke submap onder een node in de prullenbak op 1 (review Z0, veilig F2: één
    UPDATE met EXISTS per rij was O(submap × prullenbak), seconden bij 10.000 en 2.000). Dan
    photo_geo.dead van de submap, via de sleutel node_id en alleen waar hij verandert (een gewone
    verplaatsing schrijft daar niets; review Z0, haalbaar F2)."""
    cur = "(SELECT mpath FROM node WHERE id = NEW.id)"
    return (
        "UPDATE node SET dead =\n"
        "      coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)\n"
        f"   WHERE mpath >= {cur} AND mpath < {end(cur)};\n"
        "UPDATE node SET dead = 1\n"
        "  FROM (SELECT mpath AS tp FROM node WHERE deleted_at IS NOT NULL\n"
        f"          AND mpath >= {cur} AND mpath < {end(cur)}) AS t\n"
        f"   WHERE node.mpath >= t.tp AND node.mpath < {end('t.tp')};\n"
        "UPDATE photo_geo SET dead = (SELECT n.dead FROM node n WHERE n.id = photo_geo.node_id)\n"
        f"   WHERE node_id IN (SELECT id FROM node WHERE mpath >= {cur} AND mpath < {end(cur)})\n"
        "     AND dead IS NOT (SELECT n.dead FROM node n WHERE n.id = photo_geo.node_id);"
    )


# --- De triggers -------------------------------------------------------------------------

_NEW_NUM = "(SELECT num FROM node WHERE id = NEW.id)"
_TEXT_CHANGED = "(OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id)"


def _trigger(head: str, *body: str) -> str:
    lines = [f"CREATE TRIGGER {head} BEGIN"]
    lines += ["  " + line.replace("\n", "\n  ") for line in body if line]
    lines.append("END")
    return "\n".join(lines)


def triggers(fts: bool) -> dict[str, str]:
    """Alle triggers van 0010, op naam, in de volgorde waarin de migratie ze maakt. fts=False:
    de vorm zonder FTS5 (alleen nummer, pad, dead en de foto-rijen)."""

    def f(*sql: str) -> tuple[str, ...]:
        return sql if fts else ()

    t: dict[str, str] = {}
    # nieuw: nummer, pad, dead, beide FTS
    t["node_ai"] = _trigger(
        "node_ai AFTER INSERT ON node",
        "UPDATE search_seq SET next = next + 1 WHERE id = 1;",
        "UPDATE node SET num = (SELECT next - 1 FROM search_seq WHERE id = 1) WHERE id = NEW.id;",
        "UPDATE node SET\n"
        "  mpath = coalesce((SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id), '/')"
        " || num || '/',\n"
        "  dead = coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)"
        " OR NEW.deleted_at IS NOT NULL\n"
        "WHERE id = NEW.id;",
        *f(ins(f"num = {_NEW_NUM}"), nins(_NEW_NUM, "NEW.search_name")),
    )
    # naam of inhoud. Bij nieuwe inhoud eerst de foto-rijen van de oude inhoud weg (Keuze 25a);
    # de triggers van photo_ocr houden FTS daarbij gelijk. Dan de oude rij uit FTS.
    t["node_bu_text"] = _trigger(
        "node_bu_text BEFORE UPDATE OF search_name, blob_id ON node\n"
        f"WHEN OLD.num IS NOT NULL AND {_TEXT_CHANGED}",
        "DELETE FROM photo_ocr WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;",
        "DELETE FROM photo_geo WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;",
        *f(del_("num = OLD.num"), ndel("OLD.num", "OLD.search_name")),
    )
    if fts:
        t["node_au_text"] = _trigger(
            "node_au_text AFTER UPDATE OF search_name, blob_id ON node\n"
            f"WHEN NEW.num IS NOT NULL AND {_TEXT_CHANGED}",
            ins("num = NEW.num"),
            nins("NEW.num", "NEW.search_name"),
        )
    # een andere eigenaar: niets van de oude eigenaar blijft aan de node hangen (Keuze 25a)
    t["node_au_owner"] = _trigger(
        "node_au_owner AFTER UPDATE OF owner_id ON node\nWHEN OLD.owner_id IS NOT NEW.owner_id",
        "DELETE FROM photo_ocr WHERE node_id = NEW.id;",
        "DELETE FROM photo_geo WHERE node_id = NEW.id;",
        "DELETE FROM photo_person WHERE node_id = NEW.id;",
    )
    # wissen, ook via de FK-cascade (de cascade start deze trigger)
    if fts:
        t["node_bd"] = _trigger(
            "node_bd BEFORE DELETE ON node WHEN OLD.num IS NOT NULL",
            del_("num = OLD.num"),
            ndel("OLD.num", "OLD.search_name"),
        )
    # verplaatsen: de paden van de hele submap, dan dead. Zonder ouder (kan niet in de boom)
    # wordt het een eigen begin, zoals in node_ai.
    t["node_au_move"] = _trigger(
        "node_au_move AFTER UPDATE OF parent_id ON node\n"
        "WHEN OLD.parent_id IS NOT NEW.parent_id AND OLD.mpath IS NOT NULL",
        "UPDATE node SET mpath = coalesce((SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id),"
        " '/') || NEW.num || '/'\n"
        "                        || substr(mpath, length(OLD.mpath) + 1)\n"
        f" WHERE mpath >= OLD.mpath AND mpath < {end('OLD.mpath')};",
        dead_subtree(),
    )
    # prullenbak in of uit
    t["node_au_trash"] = _trigger(
        "node_au_trash AFTER UPDATE OF deleted_at ON node\n"
        "WHEN (OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL) AND OLD.mpath IS NOT NULL",
        dead_subtree(),
    )
    # De plek van een foto houdt de stand van zijn node bij: dead (via DEAD_SUBTREE hierboven)
    # en taken_at. Zo kan de kaart zonder join op node, en blijft hij toch precies own_live
    # (live.own_live_geo; review Z0, haalbaar F1 en F2). Een rij hoort bij een bestand van de
    # eigenaar van de node; node_id en owner_id veranderen nooit (een andere eigenaar: de rij
    # gaat weg, node_au_owner), en dead en taken_at zijn altijd die van de node.
    t["node_au_taken"] = _trigger(
        "node_au_taken AFTER UPDATE OF taken_at ON node\nWHEN OLD.taken_at IS NOT NEW.taken_at",
        "UPDATE photo_geo SET taken_at = NEW.taken_at WHERE node_id = NEW.id;",
    )
    t["geo_bi"] = _trigger(
        "geo_bi BEFORE INSERT ON photo_geo\n"
        "WHEN NOT EXISTS (SELECT 1 FROM node n WHERE n.id = NEW.node_id"
        " AND n.owner_id = NEW.owner_id AND n.kind = 'file')",
        "SELECT RAISE(ABORT, 'photo_geo: alleen bij een bestand, van de eigenaar van de node');",
    )
    t["geo_ai"] = _trigger(
        "geo_ai AFTER INSERT ON photo_geo",
        "UPDATE photo_geo SET (dead, taken_at) =\n"
        "  (SELECT n.dead, n.taken_at FROM node n WHERE n.id = NEW.node_id)\n"
        "WHERE node_id = NEW.node_id;",
    )
    t["geo_bu"] = _trigger(
        "geo_bu BEFORE UPDATE ON photo_geo\n"
        "WHEN NEW.node_id IS NOT OLD.node_id OR NEW.owner_id IS NOT OLD.owner_id\n"
        "  OR NEW.dead IS NOT (SELECT n.dead FROM node n WHERE n.id = NEW.node_id)\n"
        "  OR NEW.taken_at IS NOT (SELECT n.taken_at FROM node n WHERE n.id = NEW.node_id)",
        "SELECT RAISE(ABORT, 'photo_geo: node_id, owner_id, dead en taken_at volgen de node');",
    )
    if not fts:
        return t

    # De inhoud. Een media_item of photo_ocr waarvan node_id verandert, bestaat niet
    # (tests/search/test_search_triggers.py houdt dat vast).
    for table, short, key in (("blob_text", "bt", "blob_id"), ("photo_ocr", "po", "node_id")):
        col = "blob_sha" if table == "blob_text" else "node_id"
        for name, when, row, macro in (
            ("bi", "BEFORE INSERT", "NEW", del_),
            ("ai", "AFTER INSERT", "NEW", ins),
            ("bu", "BEFORE UPDATE", "OLD", del_),
            ("au", "AFTER UPDATE", "NEW", ins),
            ("bd", "BEFORE DELETE", "OLD", del_),
            ("ad", "AFTER DELETE", "OLD", ins),
        ):
            t[f"{short}_{name}"] = (
                f"CREATE TRIGGER {short}_{name} {when} ON {table} BEGIN "
                f"{macro(f'{key} = {row}.{col}')} END"
            )
    for name, when, row, macro in (
        ("bi", "BEFORE INSERT", "NEW", del_),
        ("ai", "AFTER INSERT", "NEW", ins),
        ("bu", "BEFORE UPDATE OF title", "OLD", del_),
        ("au", "AFTER UPDATE OF title", "NEW", ins),
        ("bd", "BEFORE DELETE", "OLD", del_),
        ("ad", "AFTER DELETE", "OLD", ins),
    ):
        t[f"mi_{name}"] = (
            f"CREATE TRIGGER mi_{name} {when} ON media_item BEGIN "
            f"{macro(f'num = (SELECT num FROM node WHERE id = {row}.node_id)')} END"
        )
    return t


# De bevroren delen per migratie: 0010 heeft er een kopie van (test_schema_triggers.py eist dat
# die gelijk zijn). TRIGGERS en TRIGGERS_NO_FTS zijn alle triggers die er na head zijn, en dat
# is precies wat in sqlite_master staat. De plek voor 0011 (gezichten) en 0012 (zoeken op beeld):
# A0 zet hier TRIGGERS_0011 en TRIGGERS_0012 neer, elk gelijk aan de kopie in zijn migratie, en
# voegt ze hieronder toe (bouwontwerp §3.1a, Keuze 55; review Z0b, contract F5). 0010 blijft dan
# zoals hij is.
TRIGGERS_0010 = triggers(fts=True)
TRIGGERS_NO_FTS_0010 = triggers(fts=False)
TRIGGERS = {**TRIGGERS_0010}
TRIGGERS_NO_FTS = {**TRIGGERS_NO_FTS_0010}

# --- Vullen in de migratie ---------------------------------------------------------------

FILL = (
    "UPDATE node SET num = rowid",
    "INSERT INTO search_seq VALUES (1, coalesce((SELECT max(num) FROM node), 0) + 1)",
    "INSERT INTO search_state VALUES (1, NULL, NULL, NULL)",
    "WITH RECURSIVE t(id, mpath, dead) AS (\n"
    "  SELECT id, '/' || num || '/', deleted_at IS NOT NULL FROM node WHERE parent_id IS NULL\n"
    "  UNION ALL\n"
    "  SELECT n.id, t.mpath || n.num || '/', t.dead OR n.deleted_at IS NOT NULL\n"
    "  FROM node n JOIN t ON n.parent_id = t.id)\n"
    "UPDATE node SET mpath = t.mpath, dead = t.dead FROM t WHERE node.id = t.id",
)
# Een rebuild in één statement mag alleen in de migratie (de app draait dan niet); daarna
# nooit meer (Keuze 12).
FTS_FILL = (
    "INSERT INTO fts_node(fts_node) VALUES ('rebuild')",
    "INSERT INTO fts_name(fts_name) VALUES ('rebuild')",
)

# De nieuwe tabellen van 0010, in de volgorde waarin de downgrade ze weghaalt (kinderen eerst)
NEW_TABLES = (
    "album_skip",
    "album_rule",
    "memory_hide",
    "photo_pref",
    "photo_ocr",
    "photo_person",
    "person",
    "dup_ignore",
    "dup_pair",
    "blob_hash",
    "photo_geo",
    "blob_meta",
    "analysis",
    "ocr_page",
    "blob_text",
    "search_state",
    "search_seq",
)
# Tabellen die SQLite zelf bij een FTS-tabel maakt; compare_metadata slaat ze over
FTS_SHADOW = tuple(
    f"{base}_{part}"
    for base in FTS_TABLES
    for part in ("data", "idx", "docsize", "config", "content")
)


def has_fts5(conn) -> bool:
    """Of deze SQLite FTS5 kent (een synchrone verbinding, zoals in een migratie)."""
    if conn.dialect.name != "sqlite":
        return False
    found = conn.exec_driver_sql("SELECT sqlite_compileoption_used('ENABLE_FTS5')").scalar()
    return bool(found)
