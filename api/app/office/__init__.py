"""Office (fase 4): kantoordocumenten bewerken en omzetten met een document server.

Hier staat bewust niets: het renderproces importeert app.office.reader (de lezer van B21) en
moet snel starten. Elke module importeert wat ze nodig heeft.

Kern en adapter (Keuze 3): alles hier kent alleen app/office/engine.py; de adapter voor
OnlyOffice staat in app/office/oo/, en buiten oo/ importeert alleen engine.py die
(tests/test_office_imports.py controleert het).

  engine.py    de naad: Engine, PathClass, CallbackEvent, ... en current()
  keys.py      document.key en de key van een conversie
  state.py     OfficeState op app.state.office, OfficeConfig en de sloten per bestand
  runtime.py   welke app en welke instellingen (bind in de lifespan)
  grants.py    tijdelijke bronnen voor een conversie
  urls.py      adressen voor DS, en adressen van DS herschrijven (SSRF, Keuze 13)
  inspect.py   de invoercontrole vóór elk bestand naar DS (Keuze 23)
  health.py    de enige gezondheidslus
  proxy.py     /office/ over http (kern O0, af in O1)
  tunnel.py    /office/ over WebSocket (kern O0, af in O1)
  gate.py      het poort-cookie (O1)
  sessions.py, policy.py, saving.py, sweep.py, recovery.py   bewerken en opslaan (O2)
  convert.py   PDF via conversie (O3)
  blank.py     lege documenten (O4)
  monitor.py   bewaking voor /beheer (O5)
  reader.py    de lezer van B21 (fase 3), zonder DS
"""
