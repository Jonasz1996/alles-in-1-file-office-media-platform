"""PostgreSQL: de zoekindex (wat FTS5 op SQLite doet)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23 20:00:00.000000

Alleen PostgreSQL (release 0.8.2, pakket P5). Op SQLite doet deze migratie niets: daar zijn
fts_node en fts_name de FTS5-tabellen van 0010.

Op PostgreSQL:

- cf_fold(text): zoals app.text.fold(), voor elk schrift: NFKD, zonder de tekens op een letter
  (MARKS: elk teken met een combining class, zoals unicodedata.combining()), kleine letters, ß
  als ss, en wat casefold() anders doet dan lower() (FOLD_FROM en FOLD_TO: ς als σ, de kleine
  letters van Cherokee als hoofdletters, ...). Alles wat geen letter of cijfer is, wordt een
  spatie. De tabellen zijn bevroren (Python 3.13, Unicode 15.1, tegen lower() van PostgreSQL 16
  met C.UTF-8): elke letter en elk cijfer geeft dan hetzelfde als fold() (review van PR #6, F2).
- cf_tsv(text, "char"): to_tsvector('simple', cf_fold(t)) met dat gewicht.
- cf_add(tsvector, text, "char") en fts_doc(...): de tsvector van een node. Past die niet in een
  tsvector (1 MB aan woorden en plekken), dan houdt hij de langste reeks woorden vooraan die
  nog past, en is de rij capped (plan 0.8.2, vraag 8; review van PR #6, F3 en F10).
- fts_node(rowid, tsv, otsv, capped): rowid is node.num (dezelfde naam als in FTS5, zodat de
  vragen van app/search/engine.py dezelfde vorm houden). tsv: naam (A), titel uit de
  mediabibliotheek (B), tekst en OCR van de inhoud (C), meta (D). otsv: tsv met de OCR van een
  foto (C) erbij, alleen als die er is; die is alleen van de eigenaar (Keuze 16 en 25a). GIN op
  tsv en otsv. capped: de tekst was te groot en is ingekort (/admin/search telt ze).
- fts_rows(nums, cur): de rijen zoals ze uit de bron volgen, zonder te schrijven.
- fts_name: een view (rowid, search_name) op node; de stukken van namen zoeken met LIKE, zonder
  index. Geen extensie zoals pg_trgm: alleen wat PostgreSQL zelf heeft (plan 0.8.2, vraag 1).
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

# Wat casefold() anders doet dan lower(), na NFKD zonder MARKS (één teken voor één teken)
FOLD_FROM = "ςᏸᏹᏺᏻᏼᏽᲀᲁᲂᲃᲄᲅᲆᲇᲈꭰꭱꭲꭳꭴꭵꭶꭷꭸꭹꭺꭻꭼꭽꭾꭿꮀꮁꮂꮃꮄꮅꮆꮇꮈꮉꮊꮋꮌꮍꮎꮏꮐꮑꮒꮓꮔꮕꮖꮗꮘꮙꮚꮛꮜꮝꮞꮟꮠꮡꮢꮣꮤꮥꮦꮧꮨꮩꮪꮫꮬꮭꮮꮯꮰꮱꮲꮳꮴꮵꮶꮷꮸꮹꮺꮻꮼꮽꮾꮿ"  # noqa: E501
FOLD_TO = "σᏰᏱᏲᏳᏴᏵвдосттъѣꙋᎠᎡᎢᎣᎤᎥᎦᎧᎨᎩᎪᎫᎬᎭᎮᎯᎰᎱᎲᎳᎴᎵᎶᎷᎸᎹᎺᎻᎼᎽᎾᎿᏀᏁᏂᏃᏄᏅᏆᏇᏈᏉᏊᏋᏌᏍᏎᏏᏐᏑᏒᏓᏔᏕᏖᏗᏘᏙᏚᏛᏜᏝᏞᏟᏠᏡᏢᏣᏤᏥᏦᏧᏨᏩᏪᏫᏬᏭᏮᏯ"  # noqa: E501
# Elk teken met unicodedata.combining() > 0, als klasse voor een reguliere expressie
MARKS = r"\u0300-\u034e\u0350-\u036f\u0483-\u0487\u0591-\u05bd\u05bf\u05c1-\u05c2\u05c4-\u05c5\u05c7\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e4\u06e7-\u06e8\u06ea-\u06ed\u0711\u0730-\u074a\u07eb-\u07f3\u07fd\u0816-\u0819\u081b-\u0823\u0825-\u0827\u0829-\u082d\u0859-\u085b\u0898-\u089f\u08ca-\u08e1\u08e3-\u08ff\u093c\u094d\u0951-\u0954\u09bc\u09cd\u09fe\u0a3c\u0a4d\u0abc\u0acd\u0b3c\u0b4d\u0bcd\u0c3c\u0c4d\u0c55-\u0c56\u0cbc\u0ccd\u0d3b-\u0d3c\u0d4d\u0dca\u0e38-\u0e3a\u0e48-\u0e4b\u0eb8-\u0eba\u0ec8-\u0ecb\u0f18-\u0f19\u0f35\u0f37\u0f39\u0f71-\u0f72\u0f74\u0f7a-\u0f7d\u0f80\u0f82-\u0f84\u0f86-\u0f87\u0fc6\u1037\u1039-\u103a\u108d\u135d-\u135f\u1714-\u1715\u1734\u17d2\u17dd\u18a9\u1939-\u193b\u1a17-\u1a18\u1a60\u1a75-\u1a7c\u1a7f\u1ab0-\u1abd\u1abf-\u1ace\u1b34\u1b44\u1b6b-\u1b73\u1baa-\u1bab\u1be6\u1bf2-\u1bf3\u1c37\u1cd0-\u1cd2\u1cd4-\u1ce0\u1ce2-\u1ce8\u1ced\u1cf4\u1cf8-\u1cf9\u1dc0-\u1dff\u20d0-\u20dc\u20e1\u20e5-\u20f0\u2cef-\u2cf1\u2d7f\u2de0-\u2dff\u302a-\u302f\u3099-\u309a\ua66f\ua674-\ua67d\ua69e-\ua69f\ua6f0-\ua6f1\ua806\ua82c\ua8c4\ua8e0-\ua8f1\ua92b-\ua92d\ua953\ua9b3\ua9c0\uaab0\uaab2-\uaab4\uaab7-\uaab8\uaabe-\uaabf\uaac1\uaaf6\uabed\ufb1e\ufe20-\ufe2f\U000101fd\U000102e0\U00010376-\U0001037a\U00010a0d\U00010a0f\U00010a38-\U00010a3a\U00010a3f\U00010ae5-\U00010ae6\U00010d24-\U00010d27\U00010eab-\U00010eac\U00010efd-\U00010eff\U00010f46-\U00010f50\U00010f82-\U00010f85\U00011046\U00011070\U0001107f\U000110b9-\U000110ba\U00011100-\U00011102\U00011133-\U00011134\U00011173\U000111c0\U000111ca\U00011235-\U00011236\U000112e9-\U000112ea\U0001133b-\U0001133c\U0001134d\U00011366-\U0001136c\U00011370-\U00011374\U00011442\U00011446\U0001145e\U000114c2-\U000114c3\U000115bf-\U000115c0\U0001163f\U000116b6-\U000116b7\U0001172b\U00011839-\U0001183a\U0001193d-\U0001193e\U00011943\U000119e0\U00011a34\U00011a47\U00011a99\U00011c3f\U00011d42\U00011d44-\U00011d45\U00011d97\U00011f41-\U00011f42\U00016af0-\U00016af4\U00016b30-\U00016b36\U00016ff0-\U00016ff1\U0001bc9e\U0001d165-\U0001d169\U0001d16d-\U0001d172\U0001d17b-\U0001d182\U0001d185-\U0001d18b\U0001d1aa-\U0001d1ad\U0001d242-\U0001d244\U0001e000-\U0001e006\U0001e008-\U0001e018\U0001e01b-\U0001e021\U0001e023-\U0001e024\U0001e026-\U0001e02a\U0001e08f\U0001e130-\U0001e136\U0001e2ae\U0001e2ec-\U0001e2ef\U0001e4ec-\U0001e4ef\U0001e8d0-\U0001e8d6\U0001e944-\U0001e94a"  # noqa: E501

FUNCTION_SQL = {
    "cf_fold": f"""
CREATE OR REPLACE FUNCTION cf_fold(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT regexp_replace(
    translate(
      replace(lower(regexp_replace(normalize(coalesce(t, ''), NFKD), '[{MARKS}]', '', 'g')),
              'ß', 'ss'),
      '{FOLD_FROM}', '{FOLD_TO}'),
    '[^[:alnum:]]+', ' ', 'g')
$$
""",
    # Geen EXCEPTION meer: dan mag hij ook in een parallel plan (review van PR #6, F14)
    "cf_tsv": """
CREATE OR REPLACE FUNCTION cf_tsv(t text, w "char") RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT setweight(to_tsvector('simple', cf_fold(t)), w)
$$
""",
    # v met de woorden van t erachter; past dat niet in een tsvector (1 MB), dan de langste reeks
    # woorden vooraan die nog past (gezocht op woorden, niet gehalveerd; F10), en capped.
    # PARALLEL UNSAFE: een EXCEPTION-blok is een subtransactie, en die mag in geen enkel deel
    # van een parallel plan, ook niet in de leider (dus ook niet RESTRICTED; F14).
    "cf_add": """
CREATE OR REPLACE FUNCTION cf_add(v tsvector, t text, w "char",
                                  OUT tsv tsvector, OUT capped boolean)
LANGUAGE plpgsql IMMUTABLE PARALLEL UNSAFE AS $$
DECLARE
  words text[];
  lo int := 0;
  hi int;
  m int;
BEGIN
  capped := false;
  BEGIN
    tsv := v || cf_tsv(t, w);
    RETURN;
  EXCEPTION WHEN program_limit_exceeded THEN
    capped := true;
  END;
  -- Alle woorden passen niet, geen woord wel (v is al een tsvector)
  words := regexp_split_to_array(btrim(cf_fold(t)), ' ');
  hi := cardinality(words);
  WHILE hi - lo > 1 LOOP
    m := (lo + hi) / 2;
    BEGIN
      PERFORM v || setweight(to_tsvector('simple', array_to_string(words[1:m], ' ')), w);
      lo := m;
    EXCEPTION WHEN program_limit_exceeded THEN
      hi := m;
    END;
  END LOOP;
  tsv := v || setweight(to_tsvector('simple', array_to_string(words[1:lo], ' ')), w);
END $$
""",
    # De rij van fts_node voor één node. Meestal past alles in één keer. Anders deel voor deel
    # in dezelfde volgorde (naam, titel, tekst, meta, dan de OCR van de foto in otsv), elk met
    # cf_add: wat na de grens komt, is niet vindbaar (plan 0.8.2, vraag 8), en capped telt in
    # /admin/search. Ook de som van de delen is begrensd, niet elk deel apart (F3).
    "fts_doc": """
CREATE OR REPLACE FUNCTION fts_doc(name_t text, title_t text, body_t text, meta_t text,
                                   pocr_t text, OUT tsv tsvector, OUT otsv tsvector,
                                   OUT capped boolean)
LANGUAGE plpgsql IMMUTABLE PARALLEL UNSAFE AS $$
DECLARE
  r record;
  v tsvector := ''::tsvector;
  c boolean := false;
BEGIN
  capped := false;
  BEGIN
    tsv := cf_tsv(name_t, 'A') || cf_tsv(title_t, 'B') || cf_tsv(body_t, 'C')
           || cf_tsv(meta_t, 'D');
    otsv := CASE WHEN pocr_t IS NOT NULL THEN tsv || cf_tsv(pocr_t, 'C') END;
    RETURN;
  EXCEPTION WHEN program_limit_exceeded THEN
    NULL;
  END;
  SELECT * INTO r FROM cf_add(v, name_t, 'A');
  v := r.tsv; c := c OR r.capped;
  SELECT * INTO r FROM cf_add(v, title_t, 'B');
  v := r.tsv; c := c OR r.capped;
  SELECT * INTO r FROM cf_add(v, body_t, 'C');
  v := r.tsv; c := c OR r.capped;
  SELECT * INTO r FROM cf_add(v, meta_t, 'D');
  v := r.tsv; c := c OR r.capped;
  tsv := v;
  otsv := NULL;
  IF pocr_t IS NOT NULL THEN
    SELECT * INTO r FROM cf_add(v, pocr_t, 'C');
    otsv := r.tsv; c := c OR r.capped;
  END IF;
  capped := c;
END $$
""",
    # De rijen van fts_node zoals ze uit de bron volgen, voor de nums tot en met cur (NULL: alle),
    # zonder te schrijven; fts_refresh schrijft ze
    "fts_rows": """
CREATE OR REPLACE FUNCTION fts_rows(nums bigint[], cur bigint DEFAULT NULL)
RETURNS TABLE (rowid bigint, tsv tsvector, otsv tsvector, capped boolean)
LANGUAGE sql STABLE PARALLEL UNSAFE AS $$
  SELECT n.num, d.tsv, d.otsv, d.capped
  FROM node n
  LEFT JOIN blob_text t ON t.blob_sha = n.blob_id
  LEFT JOIN photo_ocr po ON po.node_id = n.id
  CROSS JOIN LATERAL fts_doc(
    n.search_name,
    (SELECT string_agg(m.title, ' ') FROM media_item m WHERE m.node_id = n.id),
    coalesce(t.body, '') || ' ' || coalesce(t.ocr, ''),
    t.meta,
    CASE WHEN po.node_id IS NOT NULL THEN coalesce(po.text, '') END
  ) AS d
  WHERE n.num = ANY(nums) AND (cur IS NULL OR n.num <= cur)
$$
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
  INSERT INTO fts_node (rowid, tsv, otsv, capped) SELECT * FROM fts_rows(nums, cur);
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
        "CREATE TABLE fts_node (rowid bigint PRIMARY KEY, tsv tsvector NOT NULL, otsv tsvector, "
        "capped boolean NOT NULL DEFAULT false)"
    )
    op.execute("CREATE INDEX ix_fts_node_tsv ON fts_node USING gin (tsv)")
    op.execute("CREATE INDEX ix_fts_node_otsv ON fts_node USING gin (otsv)")
    # Voor de telling in /admin/search: meestal geen enkele rij
    op.execute("CREATE INDEX ix_fts_node_capped ON fts_node (rowid) WHERE capped")
    op.execute("CREATE VIEW fts_name AS SELECT num AS rowid, search_name FROM node")
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
    op.execute("DROP FUNCTION IF EXISTS fts_rows(bigint[], bigint)")
    op.execute("DROP FUNCTION IF EXISTS fts_doc(text, text, text, text, text)")
    op.execute('DROP FUNCTION IF EXISTS cf_add(tsvector, text, "char")')
    op.execute('DROP FUNCTION IF EXISTS cf_tsv(text, "char")')
    op.execute("DROP FUNCTION IF EXISTS cf_fold(text)")
    op.execute("DROP VIEW IF EXISTS fts_name")
    op.execute("DROP TABLE IF EXISTS fts_node")
    # Een eerdere vorm van 0014 (PR #6, vóór de samenvoeging) maakte deze index met pg_trgm
    op.execute("DROP INDEX IF EXISTS ix_node_search_name_trgm")
