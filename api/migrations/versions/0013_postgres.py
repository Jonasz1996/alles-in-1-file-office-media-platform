"""PostgreSQL: wat de triggers van 0010-0012 op SQLite bijhouden, nu ook op PostgreSQL

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-17 12:00:00.000000

Alleen PostgreSQL (release 0.8.2, pakket P2; docs/fase-6/i3/pg-0013.md). Op SQLite doet deze
migratie niets, omhoog en omlaag: daar doen de triggers van 0010-0012 het werk al
(tests/test_migration_0013.py houdt dat vast).

Op PostgreSQL:

- node.mpath krijgt COLLATE "C": het bereik van een submap (mpath >= p AND mpath < mpath_end(p))
  vraagt de volgorde van de tekens zelf ('/' < '0'), welke locale de database ook heeft.
- num komt uit de reeks node_num_seq (geen rij search_seq die elke INSERT op één rij laat
  wachten).
- Triggers in plpgsql die precies doen wat de SQLite-triggers zonder FTS doen: num, mpath en dead
  bij een nieuwe node, een verplaatsing en de prullenbak; photo_geo volgt dead en taken_at van zijn
  node; nieuwe inhoud of een andere eigenaar ruimt foto-rijen en gezichten op; beeld_owner.gen gaat
  omhoog; een persoon wissen maakt zijn gezichten "niet tonen". TWINS zegt per SQLite-trigger welke
  het hier is. De FTS-triggers komen met 0014.
- Sloten: een verplaatsing en de prullenbak (UPDATE OF parent_id, deleted_at) pakken eerst één
  advisory lock voor de boom, vóór de UPDATE een rij vastzet (trigger node_bs_tree, per statement).
  Daarna zetten ze de rijen van de submap vast (FOR UPDATE, op mpath) en herhalen ze tot er niets
  meer verandert. Een nieuwe node leest zijn ouder met FOR KEY SHARE, net als de foreign key; die
  wacht dus op een verplaatsing die de ouder al heeft, of de verplaatsing wacht op hem en neemt hem
  in de volgende ronde mee. Een slot per eigenaar in de AFTER-trigger (plan P2, beleid a) gaf
  40P01: de rij staat dan al vast vóór het slot (pg-0013.md).
- fase6_fill(repair): rekent num, mpath, dead, photo_geo (dead, taken_at) en search_seq na en
  herstelt wat verschilt; geeft per soort hoeveel rijen verschilden. Twee keer na elkaar: de
  tweede keer nullen. Hij maakt geen rijen in search_seq en search_state (een lege database
  blijft leeg). db-copy en de wekelijkse controle gebruiken hem ook.
- photo_geo.place_id en playback_state.position_ms, duration_ms en seq worden BIGINT.
- face.seq: een identity-kolom in de volgorde van invoegen, voor de volgorde die SQLite met rowid
  heeft (people/faces/read.py), met de indexen daarvoor.
- ix_node_timeline: een gedeeltelijke index voor de fototijdlijn (ook zonder statistieken snel).

De downgrade (alleen PostgreSQL) haalt de triggers, functies, de reeks, ix_node_timeline en
face.seq weg, maakt de BIGINT-kolommen weer INTEGER (place_id boven int32 wordt NULL; voortgang
wordt begrensd). COLLATE "C" blijft: ook voor 0.8.1 is het bereik van een submap dan goed.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Per trigger van schema.TRIGGERS (0010-0012): de trigger op PostgreSQL die hetzelfde doet,
# "FTS -> 0014" (alleen FTS, komt met 0014), of "niet nodig" (met reden in pg-0013.md).
# tests/test_pg_triggers.py eist een regel voor elke naam, en dezelfde tabel in pg-0013.md.
TWINS = {
    "node_ai": "node_bi_tree",
    "node_bu_text": "node_au_content",
    "node_au_text": "FTS -> 0014",
    "node_au_owner": "node_au_content",
    "node_bd": "FTS -> 0014",
    "node_au_move": "node_au_move",
    "node_au_trash": "node_au_trash",
    "node_au_taken": "node_au_taken",
    "geo_bi": "geo_biu",
    "geo_ai": "geo_biu",
    "geo_bu": "geo_biu",
    **{
        f"{short}_{name}": "FTS -> 0014"
        for short in ("bt", "po", "mi")
        for name in ("bi", "ai", "bu", "au", "bd", "ad")
    },
    "node_au_owner_face": "node_au_content",
    "node_au_blob_face": "node_au_content",
    "person_bd_face": "person_bd_face",
    "node_ai_beeld": "node_ai_beeld",
    "node_au_beeld": "node_au_content",
    "node_ad_beeld": "node_ad_beeld",
}

# (naam, tabel) van de triggers hier, in de volgorde van maken
TRIGGERS = (
    ("node_bs_tree", "node"),
    ("node_bi_tree", "node"),
    ("node_au_move", "node"),
    ("node_au_trash", "node"),
    ("node_au_content", "node"),
    ("node_ai_beeld", "node"),
    ("node_ad_beeld", "node"),
    ("node_au_taken", "node"),
    ("geo_biu", "photo_geo"),
    ("person_bd_face", "person"),
)
# De functies (met hun argumenten, voor DROP FUNCTION), in de volgorde van maken
FUNCTIONS = (
    "mpath_end(text)",
    "node_lock_tree()",
    "node_dead_subtree(uuid)",
    "beeld_bump(uuid)",
    "fase6_fill(boolean)",
    "node_bs_tree()",
    "node_bi_tree()",
    "node_au_move()",
    "node_au_trash()",
    "node_au_content()",
    "node_ai_beeld()",
    "node_ad_beeld()",
    "node_au_taken()",
    "geo_biu()",
    "person_bd_face()",
)

# Het slot van de boom: één voor alle eigenaren. Per eigenaar kan niet vóór de rijsloten (een
# trigger per statement kent de rijen nog niet), en erna geeft het deadlocks.
TREE_LOCK = "pg_advisory_xact_lock(hashtextextended('platform.tree', 0))"

FUNCTION_SQL = {
    # Het einde van het bereik van een pad: '/1/7/' -> '/1/70' ('/' < '0' in COLLATE "C")
    "mpath_end": """
CREATE OR REPLACE FUNCTION mpath_end(p text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT left(p, -1) || '0' $$
""",
    "node_lock_tree": f"""
CREATE OR REPLACE FUNCTION node_lock_tree() RETURNS void
LANGUAGE sql AS $$ SELECT {TREE_LOCK} $$
""",
    # DEAD_SUBTREE van 0010: dead opnieuw voor de submap van nid, van boven naar beneden (de dead
    # van de ouder, of 1 als de node zelf in de prullenbak ligt). Alleen de rijen die veranderen,
    # vastgezet op volgorde van mpath, en opnieuw tot er niets meer verandert: een nieuwe node die
    # intussen klaar kwam, gaat zo mee. Dan photo_geo.dead van de submap, waar hij verschilt.
    "node_dead_subtree": """
CREATE OR REPLACE FUNCTION node_dead_subtree(nid uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  cur text;
  ids uuid[];
  vals int[];
BEGIN
  LOOP
    SELECT array_agg(s.id ORDER BY s.mpath), array_agg(s.want ORDER BY s.mpath)
      INTO ids, vals
      FROM (
        WITH RECURSIVE t(id, want) AS (
          SELECT n.id,
                 CASE WHEN coalesce(p.dead, 0) = 1 OR n.deleted_at IS NOT NULL THEN 1 ELSE 0 END
            FROM node n LEFT JOIN node p ON p.id = n.parent_id
           WHERE n.id = nid
          UNION ALL
          SELECT c.id, CASE WHEN t.want = 1 OR c.deleted_at IS NOT NULL THEN 1 ELSE 0 END
            FROM node c JOIN t ON c.parent_id = t.id)
        SELECT n.id, n.mpath, t.want
          FROM node n JOIN t ON t.id = n.id
         WHERE n.dead <> t.want
         ORDER BY n.mpath
           FOR UPDATE OF n) s;
    EXIT WHEN ids IS NULL;
    UPDATE node SET dead = v.want
      FROM unnest(ids, vals) AS v(id, want)
     WHERE node.id = v.id;
  END LOOP;
  SELECT mpath INTO cur FROM node WHERE id = nid;
  UPDATE photo_geo g SET dead = n.dead
    FROM node n
   WHERE n.id = g.node_id AND n.mpath >= cur AND n.mpath < mpath_end(cur) AND g.dead <> n.dead;
END $$
""",
    # BUMP(u) van 0012 (Keuze 76): de rij van de eigenaar erbij als hij er nog niet is en de
    # gebruiker nog bestaat, dan gen één hoger. ON CONFLICT: twee eerste beelden tegelijk.
    "beeld_bump": """
CREATE OR REPLACE FUNCTION beeld_bump(owner uuid) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO beeld_owner (user_id, gen)
  SELECT owner, 0 WHERE EXISTS (SELECT 1 FROM "user" WHERE id = owner)
  ON CONFLICT (user_id) DO NOTHING;
  UPDATE beeld_owner SET gen = gen + 1 WHERE user_id = owner;
END $$
""",
    # Nalopen en herstellen (repair) of alleen tellen (repair => false). Per soort het aantal
    # rijen dat verschilde: num (nodes zonder num), tree (mpath of dead), photo_geo (dead of
    # taken_at), search_seq en search_state (hun ene rij, als die er is). De boom en photo_geo:
    # wat de triggers zouden geven, opnieuw vanaf de hoofdmappen (zoals schema.FILL).
    "fase6_fill": f"""
CREATE OR REPLACE FUNCTION fase6_fill(repair boolean DEFAULT true) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
  n_num bigint := 0;
  n_tree bigint := 0;
  n_geo bigint := 0;
  n_seq bigint := 0;
  n_state bigint := 0;
  c bigint;
  top bigint;
  seen bigint;
BEGIN
  IF repair THEN
    PERFORM {TREE_LOCK};
  END IF;
  -- num: een nummer voor elke node zonder, in de volgorde van created_at en id, na het hoogste
  SELECT count(*) INTO n_num FROM node WHERE num IS NULL;
  IF repair THEN
    SELECT coalesce(max(num), 0) INTO top FROM node;
    SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END INTO seen
      FROM node_num_seq;
    IF top > seen THEN
      PERFORM setval('node_num_seq', top);
    END IF;
    IF n_num > 0 THEN
      UPDATE node SET num = s.n
        FROM (SELECT o.id, nextval('node_num_seq') AS n
                FROM (SELECT id FROM node WHERE num IS NULL ORDER BY created_at, id) o) s
       WHERE node.id = s.id;
    END IF;
  END IF;
  -- mpath en dead. Herstellen zet de rijen eerst vast, en herhaalt tot er niets meer verschilt
  IF repair THEN
    LOOP
      WITH RECURSIVE t(id, mpath, dead) AS (
        SELECT id, '/' || num || '/', CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END
          FROM node WHERE parent_id IS NULL
        UNION ALL
        SELECT n.id, t.mpath || n.num || '/',
               CASE WHEN t.dead = 1 OR n.deleted_at IS NOT NULL THEN 1 ELSE 0 END
          FROM node n JOIN t ON n.parent_id = t.id),
      d AS (
        SELECT n.id FROM node n JOIN t ON t.id = n.id
         WHERE n.mpath IS DISTINCT FROM t.mpath OR n.dead <> t.dead
         ORDER BY n.id
           FOR UPDATE OF n)
      UPDATE node SET mpath = t.mpath, dead = t.dead
        FROM t
       WHERE node.id = t.id AND node.id IN (SELECT id FROM d)
         AND (node.mpath IS DISTINCT FROM t.mpath OR node.dead <> t.dead);
      GET DIAGNOSTICS c = ROW_COUNT;
      n_tree := n_tree + c;
      EXIT WHEN c = 0;
    END LOOP;
  ELSE
    WITH RECURSIVE t(id, mpath, dead) AS (
      SELECT id, '/' || num || '/', CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END
        FROM node WHERE parent_id IS NULL
      UNION ALL
      SELECT n.id, t.mpath || n.num || '/',
             CASE WHEN t.dead = 1 OR n.deleted_at IS NOT NULL THEN 1 ELSE 0 END
        FROM node n JOIN t ON n.parent_id = t.id)
    SELECT count(*) INTO n_tree
      FROM node n JOIN t ON t.id = n.id
     WHERE n.mpath IS DISTINCT FROM t.mpath OR n.dead <> t.dead;
  END IF;
  -- photo_geo volgt zijn node
  IF repair THEN
    UPDATE photo_geo g SET dead = n.dead, taken_at = n.taken_at
      FROM node n
     WHERE n.id = g.node_id AND (g.dead <> n.dead OR g.taken_at IS DISTINCT FROM n.taken_at);
    GET DIAGNOSTICS n_geo = ROW_COUNT;
  ELSE
    SELECT count(*) INTO n_geo
      FROM photo_geo g JOIN node n ON n.id = g.node_id
     WHERE g.dead <> n.dead OR g.taken_at IS DISTINCT FROM n.taken_at;
  END IF;
  -- search_seq.next achter het hoogste num, en geen herbouw van de index. Alleen een rij die er
  -- is (een kopie van SQLite heeft ze, een nieuwe database op PostgreSQL niet): een lege
  -- database blijft leeg, zodat db-copy erin kan
  SELECT coalesce(max(num), 0) + 1 INTO top FROM node;
  SELECT count(*) INTO n_seq FROM search_seq WHERE next <> top;
  SELECT count(*) INTO n_state FROM search_state WHERE cursor IS NOT NULL;
  IF repair AND n_seq > 0 THEN
    UPDATE search_seq SET next = top WHERE next <> top;
  END IF;
  IF repair AND n_state > 0 THEN
    UPDATE search_state SET cursor = NULL WHERE cursor IS NOT NULL;
  END IF;
  RETURN jsonb_build_object('num', n_num, 'tree', n_tree, 'photo_geo', n_geo,
                            'search_seq', n_seq, 'search_state', n_state);
END $$
""",
    # Het slot van de boom vóór een verplaatsing of de prullenbak een rij vastzet
    "node_bs_tree": """
CREATE OR REPLACE FUNCTION node_bs_tree() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM node_lock_tree();
  RETURN NULL;
END $$
""",
    # node_ai van 0010: nummer, pad en dead van een nieuwe node, uit zijn ouder. FOR KEY SHARE, net
    # als de foreign key: een verplaatsing of prullenbak die de ouder al vastzette (FOR UPDATE),
    # gaat voor; anders wacht die op deze node en neemt hem daarna mee.
    "node_bi_tree": """
CREATE OR REPLACE FUNCTION node_bi_tree() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  pm text;
  pd int;
BEGIN
  IF NEW.parent_id IS NOT NULL THEN
    SELECT mpath, dead INTO pm, pd FROM node WHERE id = NEW.parent_id FOR KEY SHARE;
  END IF;
  NEW.num := nextval('node_num_seq');
  NEW.mpath := coalesce(pm, '/') || NEW.num || '/';
  NEW.dead := CASE WHEN coalesce(pd, 0) = 1 OR NEW.deleted_at IS NOT NULL THEN 1 ELSE 0 END;
  RETURN NEW;
END $$
""",
    # node_au_move van 0010: de paden van de hele submap, dan dead. Het pad van nu (niet OLD: een
    # verplaatsing in hetzelfde statement kan het al veranderd hebben), op volgorde vastgezet, en
    # opnieuw tot er niets meer onder het oude pad staat. Zonder ouder een eigen begin.
    "node_au_move": """
CREATE OR REPLACE FUNCTION node_au_move() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  cur text;
  pm text;
  newp text;
BEGIN
  PERFORM node_lock_tree();
  SELECT mpath INTO cur FROM node WHERE id = NEW.id;
  SELECT mpath INTO pm FROM node WHERE id = NEW.parent_id;
  newp := coalesce(pm, '/') || NEW.num || '/';
  IF left(newp, length(cur)) = cur THEN
    RAISE EXCEPTION 'node: een map kan niet in zichzelf';
  END IF;
  LOOP
    UPDATE node SET mpath = newp || substr(mpath, length(cur) + 1)
     WHERE id IN (SELECT id FROM node
                   WHERE mpath >= cur AND mpath < mpath_end(cur)
                   ORDER BY mpath
                     FOR UPDATE);
    EXIT WHEN NOT FOUND;
  END LOOP;
  PERFORM node_dead_subtree(NEW.id);
  RETURN NULL;
END $$
""",
    # node_au_trash van 0010: in of uit de prullenbak
    "node_au_trash": """
CREATE OR REPLACE FUNCTION node_au_trash() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM node_lock_tree();
  PERFORM node_dead_subtree(NEW.id);
  RETURN NULL;
END $$
""",
    # Nieuwe inhoud of een andere eigenaar: node_bu_text (zonder FTS) en node_au_owner van 0010,
    # node_au_blob_face en node_au_owner_face van 0011, node_au_beeld van 0012. beeld_bump op een
    # vaste volgorde van eigenaar (twee keer dezelfde: twee keer, zoals op SQLite).
    "node_au_content": """
CREATE OR REPLACE FUNCTION node_au_content() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.blob_id IS DISTINCT FROM NEW.blob_id THEN
    IF OLD.num IS NOT NULL THEN
      DELETE FROM photo_ocr WHERE node_id = NEW.id;
      DELETE FROM photo_geo WHERE node_id = NEW.id;
    END IF;
    DELETE FROM face WHERE node_id = NEW.id;
    DELETE FROM face_scan WHERE node_id = NEW.id;
    UPDATE photo_person SET x = NULL, y = NULL, w = NULL, h = NULL WHERE node_id = NEW.id;
  END IF;
  IF OLD.owner_id IS DISTINCT FROM NEW.owner_id THEN
    DELETE FROM photo_ocr WHERE node_id = NEW.id;
    DELETE FROM photo_geo WHERE node_id = NEW.id;
    DELETE FROM photo_person WHERE node_id = NEW.id;
    DELETE FROM face WHERE node_id = NEW.id;
    DELETE FROM face_scan WHERE node_id = NEW.id;
  END IF;
  IF NEW.kind = 'file' AND (OLD.ftype = 'image' OR NEW.ftype = 'image') THEN
    PERFORM beeld_bump(least(OLD.owner_id, NEW.owner_id));
    PERFORM beeld_bump(greatest(OLD.owner_id, NEW.owner_id));
  END IF;
  RETURN NULL;
END $$
""",
    "node_ai_beeld": """
CREATE OR REPLACE FUNCTION node_ai_beeld() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM beeld_bump(NEW.owner_id);
  RETURN NULL;
END $$
""",
    "node_ad_beeld": """
CREATE OR REPLACE FUNCTION node_ad_beeld() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM beeld_bump(OLD.owner_id);
  RETURN NULL;
END $$
""",
    "node_au_taken": """
CREATE OR REPLACE FUNCTION node_au_taken() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE photo_geo SET taken_at = NEW.taken_at WHERE node_id = NEW.id;
  RETURN NULL;
END $$
""",
    # geo_bi, geo_ai en geo_bu van 0010. Nieuw: alleen bij een bestand van de eigenaar van de
    # node, en dead en taken_at zijn die van de node (FOR SHARE: een prullenbak of een nieuwe
    # taken_at wacht op deze rij, of deze rij op hen). Bijwerken: node_id en owner_id blijven, en
    # dead en taken_at moeten die van de node zijn, anders een fout (zonder slot: de prullenbak
    # zet eerst de node vast en dan photo_geo).
    "geo_biu": """
CREATE OR REPLACE FUNCTION geo_biu() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  nd int;
  nt timestamptz;
  hit boolean;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT n.dead, n.taken_at INTO nd, nt FROM node n
     WHERE n.id = NEW.node_id AND n.owner_id = NEW.owner_id AND n.kind = 'file'
       FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'photo_geo: alleen bij een bestand, van de eigenaar van de node';
    END IF;
    NEW.dead := nd;
    NEW.taken_at := nt;
    RETURN NEW;
  END IF;
  SELECT n.dead, n.taken_at INTO nd, nt FROM node n WHERE n.id = NEW.node_id;
  hit := FOUND;
  IF NEW.node_id IS DISTINCT FROM OLD.node_id OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
     OR NOT hit OR NEW.dead IS DISTINCT FROM nd OR NEW.taken_at IS DISTINCT FROM nt THEN
    RAISE EXCEPTION 'photo_geo: node_id, owner_id, dead en taken_at volgen de node';
  END IF;
  RETURN NEW;
END $$
""",
    # person_bd_face van 0011 (Keuze 64)
    "person_bd_face": """
CREATE OR REPLACE FUNCTION person_bd_face() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE face SET vec = NULL, hidden = 1, suggest_id = NULL, suggest_score = NULL,
                  suggest_why = NULL, cluster = NULL
   WHERE id IN (SELECT face_id FROM face_person WHERE person_id = OLD.id AND verdict = 'ja');
  RETURN OLD;
END $$
""",
}

TRIGGER_SQL = {
    "node_bs_tree": (
        "CREATE TRIGGER node_bs_tree BEFORE UPDATE OF parent_id, deleted_at ON node "
        "FOR EACH STATEMENT EXECUTE FUNCTION node_bs_tree()"
    ),
    "node_bi_tree": (
        "CREATE TRIGGER node_bi_tree BEFORE INSERT ON node "
        "FOR EACH ROW EXECUTE FUNCTION node_bi_tree()"
    ),
    "node_au_move": (
        "CREATE TRIGGER node_au_move AFTER UPDATE OF parent_id ON node FOR EACH ROW "
        "WHEN (OLD.parent_id IS DISTINCT FROM NEW.parent_id AND OLD.mpath IS NOT NULL) "
        "EXECUTE FUNCTION node_au_move()"
    ),
    "node_au_trash": (
        "CREATE TRIGGER node_au_trash AFTER UPDATE OF deleted_at ON node FOR EACH ROW "
        "WHEN ((OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL) AND OLD.mpath IS NOT NULL) "
        "EXECUTE FUNCTION node_au_trash()"
    ),
    "node_au_content": (
        "CREATE TRIGGER node_au_content AFTER UPDATE OF blob_id, owner_id ON node FOR EACH ROW "
        "WHEN (OLD.blob_id IS DISTINCT FROM NEW.blob_id "
        "OR OLD.owner_id IS DISTINCT FROM NEW.owner_id) "
        "EXECUTE FUNCTION node_au_content()"
    ),
    "node_ai_beeld": (
        "CREATE TRIGGER node_ai_beeld AFTER INSERT ON node FOR EACH ROW "
        "WHEN (NEW.kind = 'file' AND NEW.ftype = 'image') EXECUTE FUNCTION node_ai_beeld()"
    ),
    "node_ad_beeld": (
        "CREATE TRIGGER node_ad_beeld AFTER DELETE ON node FOR EACH ROW "
        "WHEN (OLD.kind = 'file' AND OLD.ftype = 'image') EXECUTE FUNCTION node_ad_beeld()"
    ),
    "node_au_taken": (
        "CREATE TRIGGER node_au_taken AFTER UPDATE OF taken_at ON node FOR EACH ROW "
        "WHEN (OLD.taken_at IS DISTINCT FROM NEW.taken_at) EXECUTE FUNCTION node_au_taken()"
    ),
    "geo_biu": (
        "CREATE TRIGGER geo_biu BEFORE INSERT OR UPDATE ON photo_geo "
        "FOR EACH ROW EXECUTE FUNCTION geo_biu()"
    ),
    "person_bd_face": (
        "CREATE TRIGGER person_bd_face BEFORE DELETE ON person "
        "FOR EACH ROW EXECUTE FUNCTION person_bd_face()"
    ),
}

# De volgorde van people/faces/read.py (op SQLite rowid; op PostgreSQL seq, P1)
FACE_INDEXES = {
    "ix_face_open_seq": (
        "CREATE INDEX ix_face_open_seq ON face (owner_id, suggest_id, suggest_score DESC, seq) "
        "WHERE suggest_id IS NOT NULL AND hidden = 0"
    ),
    "ix_face_cluster_seq": (
        "CREATE INDEX ix_face_cluster_seq ON face (owner_id, cluster, seq DESC) "
        "WHERE cluster IS NOT NULL AND hidden = 0"
    ),
}

# De tijdlijn (routers/photos.py, on_timeline + ordered): zonder statistieken (net na een grote
# import, voor autovacuum ANALYZE draait) koos de planner ix_node_parent_order per levende map en
# sorteerde dan alles (120 ms bij 50k foto's). Deze gedeeltelijke index heeft de voorwaarden en de
# volgorde van de tijdlijn zelf: 2 ms, met en zonder statistieken (pg-0013.md).
TIMELINE_INDEX = (
    "CREATE INDEX ix_node_timeline ON node (owner_id, taken_day DESC, taken_at DESC, id DESC) "
    "WHERE kind = 'file' AND deleted_at IS NULL AND taken_day IS NOT NULL"
)

# 64 bits op PostgreSQL (op SQLite is INTEGER dat al)
BIGINT = (
    ("photo_geo", "place_id"),
    ("playback_state", "position_ms"),
    ("playback_state", "duration_ms"),
    ("playback_state", "seq"),
)
INT32 = (-(2**31), 2**31 - 1)

# Na een downgrade naar 0012 en weer terug (review PG13-2): de downgrade maakte place_id boven
# int32 NULL, en dat is elke plaats (48 bits, app/geo/load.place_id). Het land bleef staan, en
# de job geo.places vult alleen rijen zonder plaats én land, één keer per bouw (Setting
# "geo.places"). Dus: land en provincie van zulke rijen leeg, en de Setting weg; de start zet
# de keten dan opnieuw klaar en zoekt ze op uit lat en lon. Een rij met een land en terecht
# geen plaats krijgt zo hetzelfde terug. Zonder zulke rijen verandert er niets.
PLACES_AGAIN = (
    "WITH lost AS (UPDATE photo_geo SET country = NULL, admin1 = NULL "
    "WHERE place_id IS NULL AND country IS NOT NULL RETURNING 1) "
    "DELETE FROM setting WHERE key = 'geo.places' AND EXISTS (SELECT 1 FROM lost)"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute('ALTER TABLE node ALTER COLUMN mpath TYPE text COLLATE "C"')
    op.execute("CREATE SEQUENCE node_num_seq AS integer OWNED BY node.num")
    for table, column in BIGINT:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE bigint")
    op.execute(PLACES_AGAIN)
    op.execute("ALTER TABLE face ADD COLUMN seq bigint GENERATED BY DEFAULT AS IDENTITY")
    for sql in FACE_INDEXES.values():
        op.execute(sql)
    op.execute(TIMELINE_INDEX)
    for sql in FUNCTION_SQL.values():
        op.execute(sql)
    for name, _table in TRIGGERS:
        op.execute(TRIGGER_SQL[name])
    op.execute("SELECT fase6_fill()")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, table in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP SEQUENCE IF EXISTS node_num_seq")
    op.execute("DROP INDEX IF EXISTS ix_node_timeline")
    op.execute("ALTER TABLE face DROP COLUMN IF EXISTS seq")
    low, high = INT32
    op.execute(f"UPDATE photo_geo SET place_id = NULL WHERE place_id NOT BETWEEN {low} AND {high}")
    for _table, column in BIGINT[1:]:
        op.execute(
            f"UPDATE playback_state SET {column} = least(greatest({column}, {low}), {high}) "
            f"WHERE {column} NOT BETWEEN {low} AND {high}"
        )
    for table, column in BIGINT:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE integer")
