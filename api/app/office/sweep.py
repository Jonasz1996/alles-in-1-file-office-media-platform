"""De ronde (Keuze 20) en het tussentijds opslaan (Keuze 18). O0-stub; O2 maakt het af.

start(app): een eigen taak, elke office_sweep_seconds. checkpoint_all(wait): forcesave voor
alle open sessies en hooguit wait seconden wachten op de opslag; het aantal opgeslagen
documenten. Gebruikt door het stoppen van de app (wait 5), app.cli office-checkpoint en de
knop op /beheer. De stub geeft 0.
"""


async def start(app) -> None:
    return None


async def checkpoint_all(wait: float) -> int:
    return 0
