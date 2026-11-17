"""Media en streaming (fase 5): bibliotheken, afspelen in de browser, muziek.

Alles met ffmpeg draait in de mediaworker (app.media.worker), een eigen proces of container; de
app praat er alleen mee via app.media.client. Zonder PLATFORM_MEDIA_ENABLED staat media uit en
is de app precies 0.6.0 (runtime.py).

De contracten tussen de pakketten (bouwontwerp §3.3) staan in wire, client, state, runtime,
types, errors, hooks en analysis, en in worker/args. Ze zijn frozen: wijzigen alleen via de
hotfix-lane van M0.

Dit bestand importeert niets: de worker importeert app.media.worker zonder de rest van de app.
"""
