"""De mediaworker (Keuze 11, 12): het enige proces dat ffmpeg en ffprobe start.

Dit pakket importeert niets van de rest van de app (geen instellingen, geen database): de worker
draait in een eigen container zonder data. Alleen app.media.wire, de argumenten en de pure
lezers (zoals app.media.codecs).
"""
