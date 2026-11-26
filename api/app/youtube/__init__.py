"""De YouTube-module (fixlijst 26-11-2026, punt 7): zoeken, kijken (ook live) en een video als mp3
in een gekozen muziekmap bewaren.

- `helper.py`: de helper in zijn eigen container (`youtube`, hetzelfde image als de app, zonder
  /data, alleen met de map `<data>/import/youtube` als /out). Alleen hij start yt-dlp en ffmpeg
  op iets van het internet. Hij importeert niets van de rest van de app.
- `service.py`: de kant van de app (de client naar de helper en de taak `youtube.mp3`).
- `app/routers/youtube.py`: de API voor de web-app.
"""
