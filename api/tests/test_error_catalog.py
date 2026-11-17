"""Elke foutcode van de API heeft een Engelse tekst in precies één catalogus van de web-app
(web/src/i18n/errors/*.ts), en fouten gaan alleen via app/errors.py.

De catalogi worden met een eenvoudige reguliere expressie gelezen: elk bestand is
`export default { code: { en: "..." }, ... } satisfies Record<string, { en: string }>;`
zonder imports.
"""

import re
from pathlib import Path

API = Path(__file__).resolve().parents[1]
APP = API / "app"
CATALOGS = API.parent / "web" / "src" / "i18n" / "errors"

# fail(404, "not_found", ...) en item_fail(id, "not_found", ...); ook een constante als
# {"message": ..., "code": "read_only"} (die gaat met **READ_ONLY naar fail)
FAIL = re.compile(r"\b(?:item_)?fail\(\s*[^,()]+,\s*\"([a-z0-9_]+)\"", re.S)
CODE = re.compile(r"\"code\":\s*\"([a-z0-9_]+)\"")
ENTRY = re.compile(r"([A-Za-z0-9_]+)\s*:\s*\{\s*en\s*:\s*(\"(?:[^\"\\]|\\.)*\"|`[^`]*`)", re.S)
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def used_codes() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        text = path.read_text()
        for m in [*FAIL.finditer(text), *CODE.finditer(text)]:
            found.setdefault(m.group(1), set()).add(str(path.relative_to(API)))
    return found


def catalogs() -> dict[str, dict[str, str]]:
    result = {}
    for path in sorted(CATALOGS.glob("*.ts")):
        text = COMMENT.sub("", path.read_text())
        result[path.stem] = {m.group(1): m.group(2)[1:-1] for m in ENTRY.finditer(text)}
    return result


def test_er_zijn_catalogi():
    found = catalogs()
    expected = {"common", "auth", "browser", "upload", "viewer", "photos", "admin", "sharing"}
    assert expected <= set(found)
    assert found["common"], "common.ts is leeg of niet te lezen"


def test_catalogi_zonder_imports_en_in_de_afgesproken_vorm():
    for path in CATALOGS.glob("*.ts"):
        text = COMMENT.sub("", path.read_text())
        assert not re.search(r"^\s*import\b", text, re.M), path.name
        assert text.strip().startswith("export default {"), path.name
        assert text.strip().endswith("} satisfies Record<string, { en: string }>;"), path.name


def test_elke_code_staat_in_precies_een_catalogus():
    seen: dict[str, list[str]] = {}
    for name, entries in catalogs().items():
        for code in entries:
            seen.setdefault(code, []).append(name)
    double = {code: names for code, names in seen.items() if len(names) > 1}
    assert not double, f"Codes in meer dan één catalogus: {double}"


def test_elke_code_van_de_api_heeft_een_engelse_tekst():
    known = {code: text for entries in catalogs().values() for code, text in entries.items()}
    missing = {code: sorted(paths) for code, paths in used_codes().items() if code not in known}
    assert not missing, f"Codes zonder Engelse tekst in web/src/i18n/errors: {missing}"
    empty = [code for code, text in known.items() if not text.strip()]
    assert not empty, f"Lege Engelse tekst: {empty}"


def test_contractcodes_staan_in_common():
    common = catalogs()["common"]
    contract = {
        # ItemResult
        "not_found",
        "read_only",
        "share_root",
        "cross_owner",
        "name_taken",
        "into_itself",
        "trashed",
        "too_many",
        "album",
        "not_own",
        "not_media",
        "already",
        # Overige vaste contracten
        "account_disabled",
        "invalid_input",
        "not_logged_in",
    }
    assert contract <= set(common), sorted(contract - set(common))


def test_geen_httpexception_buiten_errors_py():
    offenders = [
        f"{path.relative_to(API)}:{i}"
        for path in APP.rglob("*.py")
        if path.name != "errors.py" or path.parent != APP
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if "HTTPException(" in line
    ]
    assert not offenders, f"Gebruik errors.fail(...) in plaats van HTTPException(: {offenders}"
