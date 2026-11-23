"""PostgreSQL: de zoekindex (wat FTS5 op SQLite doet)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23 20:00:00.000000

Alleen PostgreSQL (release 0.8.2, pakket P5). Op SQLite doet deze migratie niets: daar zijn
fts_node en fts_name de FTS5-tabellen van 0010.

Op PostgreSQL:

- cf_fold(text): zoals app.text.fold() voor de tekens van Latijn (zonder accenten, kleine
  letters, ß als ss), en alles wat geen letter of cijfer is een spatie. De tabel van tekens is
  bevroren in FOLD_FROM en FOLD_TO (gemaakt met fold() per teken van U+00C0-U+024F en
  U+1E00-U+1EFF dat één ASCII-teken geeft).
- cf_tsv(text, "char"): to_tsvector('simple', cf_fold(t)) met dat gewicht. Een tekst die te
  groot is voor een tsvector (1 MB aan woorden en plekken) wordt ingekort tot hij past.
- fts_node(rowid, tsv, otsv): rowid is node.num (dezelfde naam als in FTS5, zodat de vragen van
  app/search/engine.py dezelfde vorm houden). tsv: naam (A), titel uit de mediabibliotheek (B),
  tekst en OCR van de inhoud (C), meta (D). otsv: tsv met de OCR van een foto (C) erbij, alleen
  als die er is; die is alleen van de eigenaar (Keuze 16 en 25a). GIN op tsv en otsv.
- fts_name: een view (rowid, search_name) op node; de stukken van namen zoeken met LIKE, met een
  trigram-index op node.search_name als pg_trgm er is (anders zonder index).
- fts_refresh(nums, force): de rijen van die nums opnieuw uit de bron, alleen buiten een herbouw
  of voor nums die de herbouw al deed (zoals schema.in_), of altijd met force (de herbouw zelf).
- Triggers: node (nieuw, naam of inhoud anders, weg), blob_text, photo_ocr en media_item houden
  fts_node gelijk met de bron.
- De index wordt meteen gevuld (de update loopt met de app uit).

De downgrade haalt alles weer weg; de zoekfunctie valt dan terug op LikeFallback, zoals in 0.8.1.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

FOLD_FROM = "àáâãäåçèéêëìíîïñòóôõöùúûüýÿāăąćĉċčďēĕėęěĝğġģĥĩīĭįĵķĺļľńņňōŏőŕŗřśŝşšţťũūŭůűųŵŷźżžſơưǎǐǒǔǖǘǚǜǟǡǧǩǫǭǰǵǹǻȁȃȅȇȉȋȍȏȑȓȕȗșțȟȧȩȫȭȯȱȳḁḃḅḇḉḋḍḏḑḓḕḗḙḛḝḟḡḣḥḧḩḫḭḯḱḳḵḷḹḻḽḿṁṃṅṇṉṋṍṏṑṓṕṗṙṛṝṟṡṣṥṧṩṫṭṯṱṳṵṷṹṻṽṿẁẃẅẇẉẋẍẏẑẓẕẖẗẘẙẛạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"  # noqa: E501
FOLD_TO = "aaaaaaceeeeiiiinooooouuuuyyaaaccccdeeeeegggghiiiijklllnnnooorrrssssttuuuuuuwyzzzsouaiouuuuuaagkoojgnaaaeeiioorruusthaeooooyabbbcdddddeeeeefghhhhhiikkkllllmmmnnnnoooopprrrrsssssttttuuuuuvvwwwwwxxyzzzhtwysaaaaaaaaaaaaeeeeeeeeiioooooooooooouuuuuuuyyyy"  # noqa: E501

FUNCTION_SQL = {
    "cf_fold": f"""
CREATE OR REPLACE FUNCTION cf_fold(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT regexp_replace(
    translate(lower(replace(replace(coalesce(t, ''), 'ß', 'ss'), 'ẞ', 'ss')),
              '{FOLD_FROM}', '{FOLD_TO}'),
    '[^[:alnum:]]+', ' ', 'g')
$$
""",
    "cf_tsv": """
CREATE OR REPLACE FUNCTION cf_tsv(t text, w "char") RETURNS tsvector
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
  n int := length(coalesce(t, ''));
BEGIN
  IF n = 0 THEN
    RETURN ''::tsvector;
  END IF;
  LOOP
    BEGIN
      RETURN setweight(to_tsvector('simple', cf_fold(left(t, n))), w);
    EXCEPTION WHEN program_limit_exceeded THEN
      n := n / 2;
      IF n < 1000 THEN
        RETURN ''::tsvector;
      END IF;
    END;
  END LOOP;
END $$
""",
    "fts_refresh": """
CREATE OR REPLACE FUNCTION fts_refresh(nums bigint[], force boolean DEFAULT false)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  cur bigint;
BEGIN
  IF nums IS NULL OR cardinality(nums) = 0 THEN
    RETURN;
  END IF;
  IF NOT force THEN
    SELECT cursor INTO cur FROM search_state WHERE id = 1;
  END IF;
  DELETE FROM fts_node WHERE rowid = ANY(nums) AND (cur IS NULL OR rowid <= cur);
  INSERT INTO fts_node (rowid, tsv, otsv)
  SELECT s.num, s.tsv, CASE WHEN s.pocr IS NOT NULL THEN s.tsv || s.pocr END
  FROM (
    SELECT n.num,
           cf_tsv(n.search_name, 'A')
           || cf_tsv((SELECT string_agg(m.title, ' ') FROM media_item m WHERE m.node_id = n.id),
                     'B')
           || cf_tsv(coalesce(t.body, '') || ' ' || coalesce(t.ocr, ''), 'C')
           || cf_tsv(t.meta, 'D') AS tsv,
           CASE WHEN po.node_id IS NOT NULL THEN cf_tsv(po.text, 'C') END AS pocr
    FROM node n
    LEFT JOIN blob_text t ON t.blob_sha = n.blob_id
    LEFT JOIN photo_ocr po ON po.node_id = n.id
    WHERE n.num = ANY(nums) AND (cur IS NULL OR n.num <= cur)
  ) s;
END $$
""",
    "fts_node_trg": """
CREATE OR REPLACE FUNCTION fts_node_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM fts_node WHERE rowid = OLD.num;
    RETURN NULL;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.num IS DISTINCT FROM NEW.num THEN
    DELETE FROM fts_node WHERE rowid = OLD.num;
  END IF;
  PERFORM fts_refresh(ARRAY[NEW.num]::bigint[]);
  RETURN NULL;
END $$
""",
    "fts_blob_trg": """
CREATE OR REPLACE FUNCTION fts_blob_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  sha text := CASE WHEN TG_OP = 'DELETE' THEN OLD.blob_sha ELSE NEW.blob_sha END;
BEGIN
  PERFORM fts_refresh(ARRAY(SELECT num FROM node WHERE blob_id = sha AND num IS NOT NULL));
  IF TG_OP = 'UPDATE' AND OLD.blob_sha IS DISTINCT FROM NEW.blob_sha THEN
    PERFORM fts_refresh(ARRAY(SELECT num FROM node WHERE blob_id = OLD.blob_sha
                                                    AND num IS NOT NULL));
  END IF;
  RETURN NULL;
END $$
""",
    "fts_by_node_trg": """
CREATE OR REPLACE FUNCTION fts_by_node_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    PERFORM fts_refresh(ARRAY(SELECT num FROM node WHERE id = OLD.node_id AND num IS NOT NULL));
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    PERFORM fts_refresh(ARRAY(SELECT num FROM node WHERE id = NEW.node_id AND num IS NOT NULL));
  END IF;
  RETURN NULL;
END $$
""",
}

# (naam, tabel, wanneer, functie)
TRIGGERS = (
    ("fts_node_ai", "node", "AFTER INSERT", "fts_node_trg"),
    (
        "fts_node_au",
        "node",
        "AFTER UPDATE OF search_name, blob_id, num",
        "fts_node_trg",
    ),
    ("fts_node_ad", "node", "AFTER DELETE", "fts_node_trg"),
    ("fts_bt_aiud", "blob_text", "AFTER INSERT OR UPDATE OR DELETE", "fts_blob_trg"),
    ("fts_po_aiud", "photo_ocr", "AFTER INSERT OR UPDATE OR DELETE", "fts_by_node_trg"),
    ("fts_mi_aiud", "media_item", "AFTER INSERT OR UPDATE OR DELETE", "fts_by_node_trg"),
)
WHEN = {
    "fts_node_au": (
        "WHEN (OLD.search_name IS DISTINCT FROM NEW.search_name"
        " OR OLD.blob_id IS DISTINCT FROM NEW.blob_id OR OLD.num IS DISTINCT FROM NEW.num)"
    ),
}

TRGM = """
DO $$
BEGIN
  BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'pg_trgm niet beschikbaar: de stukken van namen zoeken zonder index';
  END;
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
    EXECUTE 'CREATE INDEX IF NOT EXISTS ix_node_search_name_trgm ON node '
            'USING gin (search_name gin_trgm_ops)';
  END IF;
END $$
"""


# fase6_fill (0013) zette een cursor van search_state altijd terug op NULL: op PostgreSQL liep
# toen nooit een herbouw. Met de index van 0014 is een cursor een herbouw die loopt (search-
# triggers, db-copy, beheer); de weekcontrole mag die niet afbreken. Dezelfde functie, zonder
# dat stuk; de downgrade zet die van 0013 terug.
FILL_STATE_0013 = "SELECT count(*) INTO n_state FROM search_state WHERE cursor IS NOT NULL;"
FILL_STATE_0014 = "n_state := 0;  -- 0014: een cursor is een herbouw van de index die loopt"


def _fill_0013() -> str:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0013_postgres.py")
    spec = importlib.util.spec_from_file_location("m0013_voor_0014", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = module.FUNCTION_SQL["fase6_fill"]
    assert FILL_STATE_0013 in sql
    return sql


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_fill_0013().replace(FILL_STATE_0013, FILL_STATE_0014))
    op.execute(
        "INSERT INTO search_state (id, cursor, started_at, what) "
        "SELECT 1, NULL, NULL, NULL WHERE NOT EXISTS (SELECT 1 FROM search_state WHERE id = 1)"
    )
    op.execute(
        "CREATE TABLE fts_node (rowid bigint PRIMARY KEY, tsv tsvector NOT NULL, otsv tsvector)"
    )
    op.execute("CREATE INDEX ix_fts_node_tsv ON fts_node USING gin (tsv)")
    op.execute("CREATE INDEX ix_fts_node_otsv ON fts_node USING gin (otsv)")
    op.execute("CREATE VIEW fts_name AS SELECT num AS rowid, search_name FROM node")
    op.execute(TRGM)
    for sql in FUNCTION_SQL.values():
        op.execute(sql)
    for name, table, when, func in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH ROW "
            f"{WHEN.get(name, '')} EXECUTE FUNCTION {func}()"
        )
    op.execute(
        "SELECT fts_refresh(ARRAY(SELECT num FROM node WHERE num IS NOT NULL ORDER BY num), true)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, table, _when, _func in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    # Zonder index is er niets om te herbouwen; fase6_fill van 0013 terug
    op.execute("UPDATE search_state SET cursor = NULL WHERE cursor IS NOT NULL")
    op.execute(_fill_0013())
    for name in ("fts_by_node_trg", "fts_blob_trg", "fts_node_trg"):
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    op.execute("DROP FUNCTION IF EXISTS fts_refresh(bigint[], boolean)")
    op.execute('DROP FUNCTION IF EXISTS cf_tsv(text, "char")')
    op.execute("DROP FUNCTION IF EXISTS cf_fold(text)")
    op.execute("DROP VIEW IF EXISTS fts_name")
    op.execute("DROP TABLE IF EXISTS fts_node")
    op.execute("DROP INDEX IF EXISTS ix_node_search_name_trgm")
