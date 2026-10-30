from app.models import Node
from app.text import fold


def test_fold_zonder_hoofdletters_en_accenten():
    assert fold("Één Café") == "een cafe"
    assert fold("Straße") == "strasse"
    assert fold("ﬁets.PDF") == "fiets.pdf"


def test_zoeknaam_volgt_de_naam():
    node = Node(name="Vakantie Ålesund.JPG", kind="file")
    assert node.search_name == "vakantie alesund.jpg"
    node.name = "Crème brûlée"
    assert node.search_name == "creme brulee"
