"""Een kleine, geldige PDF met N pagina's (alleen de standaardbibliotheek), als uitvoer van de
conversie van de nep-DS (O3). Elke pagina heeft een gekleurd vlak en de tekst "Pagina n van N":
zo heeft een thumbnail echt iets te tonen en is elke pagina anders.
"""

A4 = (595, 842)
# Een andere kleur per pagina (RGB tussen 0 en 1)
COLORS = ((0.11, 0.37, 0.82), (0.85, 0.33, 0.1), (0.2, 0.6, 0.3), (0.55, 0.25, 0.7))


def _text(value: str) -> str:
    """Tekst voor een PDF-string: alleen ASCII, met \\, ( en ) ontsnapt."""
    safe = value.encode("ascii", "replace").decode()
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def page_content(n: int, total: int, title: str = "") -> bytes:
    r, g, b = COLORS[(n - 1) % len(COLORS)]
    lines = [
        f"{r} {g} {b} rg",
        "60 520 475 260 re f",
        "0 0 0 rg",
        "BT /F1 28 Tf 72 460 Td",
        f"(Pagina {n} van {total}) Tj",
        "ET",
    ]
    if title:
        lines += ["BT /F1 14 Tf 72 420 Td", f"({_text(title)}) Tj", "ET"]
    return "\n".join(lines).encode()


def make_pdf(pages: int, title: str = "") -> bytes:
    """Een PDF van pages pagina's (minstens 1), A4."""
    pages = max(1, int(pages))
    width, height = A4
    objects: list[bytes] = []
    # 1 de catalogus, 2 de pagina's, 3 het lettertype, dan per pagina een pagina en zijn inhoud
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i in range(pages):
        content = page_content(i + 1, pages, title)
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
            ).encode()
        )
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
