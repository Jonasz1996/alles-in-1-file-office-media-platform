"""Een kort, leesbaar label voor een apparaat uit de User-Agent, voor het sessie-overzicht:
"Firefox op Windows", "Safari op iPhone". Geen bibliotheek: alleen de bekende browsers en
systemen, de rest heet "Onbekend apparaat"."""

UNKNOWN = "Onbekend apparaat"

# Volgorde telt: Edge, Opera en Samsung noemen zichzelf ook Chrome en Safari, Chrome noemt
# zich ook Safari, en op iOS heten Chrome en Firefox CriOS en FxiOS.
BROWSERS = [
    ("Edge", ("Edg/", "EdgA/", "EdgiOS/", "Edge/")),
    ("Opera", ("OPR/", "OPiOS/", "Opera")),
    ("Samsung Internet", ("SamsungBrowser/",)),
    ("Vivaldi", ("Vivaldi/",)),
    ("Firefox", ("Firefox/", "FxiOS/")),
    ("Chrome", ("CriOS/", "Chrome/", "Chromium/")),
    ("Safari", ("Safari/",)),
]
# Ook hier: een iPhone zegt "like Mac OS X", Android en ChromeOS zeggen ook Linux
SYSTEMS = [
    ("iPhone", ("iPhone",)),
    ("iPad", ("iPad",)),
    ("Android", ("Android",)),
    ("Windows", ("Windows",)),
    ("ChromeOS", ("CrOS",)),
    ("Mac", ("Macintosh", "Mac OS X")),
    ("Linux", ("Linux", "X11")),
]
# Vóór deze versie werd de hele User-Agent bewaard: van een browser, of van een bekend
# programma. Alleen die krijgen achteraf een label; een naam die een app zelf meegaf (ook
# een als "PlatformApp/1.0") blijft staan.
OLD_AGENTS = (
    "Mozilla/",
    "Opera/",
    "Dalvik/",
    "curl/",
    "Wget/",
    "python-httpx/",
    "python-requests/",
    "okhttp/",
)


def _first(user_agent: str, table: list[tuple[str, tuple[str, ...]]]) -> str | None:
    return next((name for name, marks in table if any(m in user_agent for m in marks)), None)


def device_label(user_agent: str | None) -> str:
    agent = (user_agent or "")[:1000]
    browser = _first(agent, BROWSERS)
    system = _first(agent, SYSTEMS)
    if browser and system:
        return f"{browser} op {system}"
    return browser or system or UNKNOWN


def shown_device(stored: str) -> str:
    """Wat er in het overzicht staat. Sessies van vóór deze versie bewaarden de hele
    User-Agent (of "setup"); die krijgen hier alsnog een label."""
    stored = stored.strip()
    if not stored or stored == "setup":
        return UNKNOWN
    return device_label(stored) if stored.startswith(OLD_AGENTS) else stored


def device_parts(stored: str) -> tuple[str | None, str | None]:
    """(browser, systeem) van een bewaard apparaat, zodat de web-app "Firefox op Windows" in
    zijn eigen taal kan zetten. Een label van deze module ("Firefox op Windows", "Firefox",
    "Android") en een oude, hele User-Agent worden ontleed; een naam die een app zelf meegaf,
    of een onbekend apparaat, geeft (None, None)."""
    stored = stored.strip()
    if not stored or stored in ("setup", UNKNOWN):
        return None, None
    if stored.startswith(OLD_AGENTS):
        agent = stored[:1000]
        return _first(agent, BROWSERS), _first(agent, SYSTEMS)
    browsers = {name for name, _ in BROWSERS}
    systems = {name for name, _ in SYSTEMS}
    browser, sep, system = stored.partition(" op ")
    if sep and browser in browsers and system in systems:
        return browser, system
    if stored in browsers:
        return stored, None
    if stored in systems:
        return None, stored
    return None, None
