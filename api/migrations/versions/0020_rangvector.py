"""rangvector: een kleine vector voor de rang van grote documenten (PostgreSQL)

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-26 20:00:00.000000

Alleen PostgreSQL (0.8.3, W4R-2; docs/fase-6/i3/pg-fts.md, "De rangvector"). Op SQLite doet deze
migratie niets: daar rekent bm25 met wat in de index van FTS5 staat.

Waarom: ronde A sorteert op ts_rank, en ts_rank leest de hele tsvector van elke treffer. Een vraag
die de grote documenten raakt (profiel groot: 200 van 1 MB) las zo tientallen MB per vraag; ronde
A haalde zijn 120 ms niet, en met 4 tegelijk ging §4.4 over de 300 ms (zoek-perf-pg rood sinds
0.8.2). De treffer zelf (tsv @@ q, via GIN) leest de vector niet.

Wat erbij komt in fts_node:

- rvec: voor een rij met meer dan RANK_MIN_LEN plekken, de woorden met hun plekken tot en met
  plek RANK_UPTO (cf_rank_head). De naam (A) en de titel (B) staan vooraan, dus altijd erin;
  van de tekst het begin. NULL voor de andere rijen: daar blijft de rang precies die van 0014.
- rlen: de lengte van tsv zoals ts_rank hem telt (cf_rank_len: elke plek, een woord zonder plek
  één keer), voor normalisatie 1. Ook alleen bij een grote rij.
- Een BEFORE-trigger op fts_node zet beide bij elke INSERT en bij een UPDATE van tsv. fts_refresh,
  fts_rows en check() van app/search/rebuild.py blijven zoals in 0014.

De rang van een grote rij is dan ts_rank(rvec, q, 0) / log2(rlen + 1): dezelfde normalisatie als
ts_rank(tsv, q, 1), met de plekken tot RANK_UPTO. Een woord dat alleen verder in de tekst staat,
geeft daar 0: het document is nog altijd een treffer, maar komt in ronde A achteraan. Bij een
kleine rij (de meeste) verandert niets.

De migratie vult rvec en rlen meteen voor de grote rijen. De downgrade haalt de trigger, de
functies en de twee kolommen weg; de rang is dan weer die van 0014.
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Een rij krijgt een rangvector als tsv meer plekken heeft dan dit (cf_rank_len; een telling, geen
# grootte in bytes, zodat een herbouw en de migratie hetzelfde beslissen). Daaronder leest
# ts_rank de vector snel genoeg (pg-fts.md: zonder de rijen boven 100 kB 2 tot 4 ms).
RANK_MIN_LEN = 2048
# De plekken die in de rangvector blijven: de naam, de titel en het begin van de tekst
RANK_UPTO = 1024

FUNCTION_SQL = {
    # De lengte van v zoals ts_rank hem telt bij normalisatie 1 (cnt_length in tsrank.c)
    "cf_rank_len": """
CREATE OR REPLACE FUNCTION cf_rank_len(v tsvector) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT coalesce(sum(coalesce(cardinality(u.positions), 1)), 0)::integer FROM unnest(v) AS u
$$
""",
    # v met alleen de plekken tot en met upto (en hun gewicht); een woord zonder zo'n plek valt
    # weg. De plekken van een woord staan oplopend, dus positions[1] beslist of het woord blijft.
    # De woorden komen uit cf_fold (letters en cijfers), toch met ' en \ zoals tsvector ze leest.
    "cf_rank_head": """
CREATE OR REPLACE FUNCTION cf_rank_head(v tsvector, upto integer) RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT coalesce(string_agg(
           '''' || replace(replace(u.lexeme, '\\', '\\\\'), '''', '''''') || ''':'
           || array_to_string(ARRAY(
                SELECT u.positions[i]::text || u.weights[i]
                FROM generate_subscripts(u.positions, 1) AS i
                WHERE u.positions[i] <= upto ORDER BY i), ','),
           ' '), '')::tsvector
  FROM unnest(v) AS u
  WHERE u.positions[1] <= upto
$$
""",
    "fts_rank_trg": f"""
CREATE OR REPLACE FUNCTION fts_rank_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.rlen := cf_rank_len(NEW.tsv);
  IF NEW.rlen > {RANK_MIN_LEN} THEN
    NEW.rvec := cf_rank_head(NEW.tsv, {RANK_UPTO});
  ELSE
    NEW.rlen := NULL;
    NEW.rvec := NULL;
  END IF;
  RETURN NEW;
END $$
""",
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE fts_node ADD COLUMN rvec tsvector, ADD COLUMN rlen integer")
    for sql in FUNCTION_SQL.values():
        op.execute(sql)
    op.execute(
        "CREATE TRIGGER fts_node_rank BEFORE INSERT OR UPDATE OF tsv ON fts_node "
        "FOR EACH ROW EXECUTE FUNCTION fts_rank_trg()"
    )
    # De grote rijen die er al zijn (UPDATE OF tsv laat de trigger lopen)
    op.execute(f"UPDATE fts_node SET tsv = tsv WHERE cf_rank_len(tsv) > {RANK_MIN_LEN}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS fts_node_rank ON fts_node")
    op.execute("DROP FUNCTION IF EXISTS fts_rank_trg()")
    op.execute("DROP FUNCTION IF EXISTS cf_rank_head(tsvector, integer)")
    op.execute("DROP FUNCTION IF EXISTS cf_rank_len(tsvector)")
    op.execute("ALTER TABLE fts_node DROP COLUMN IF EXISTS rvec, DROP COLUMN IF EXISTS rlen")
