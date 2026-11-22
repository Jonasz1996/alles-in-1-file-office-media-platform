# Fase 11, stap 5c: AI-upscaling op de RTX 3060 Ti (ontwerp)

Stand: 22-11-2026, ontwerp, nog niet gebouwd.

## Wat vaststaat (Jonas)

- Alleen de **RTX 3060 Ti**, in node **jbogaertmedia2** (192.168.10.147). De UHD 630 (VM's)
  en de 3080 Ti (gamestream) nooit.
- De kaart wordt gedeeld met twee AI-taken van Jonas, allebei in een **LXC**:
  het AI-verslag elk uur van :00 tot ongeveer :15, en (komt nog) een security-analyse elk uur
  van :45 tot :00.
- De upscaling mag de kaart alleen gebruiken **van :20 tot :40** van elk uur. Dat venster is
  instelbaar op /beheer, met 20–40 als standaard.
- Transcoderen bij het kijken blijft op de CPU.
- Upscaling in de speler (stap 5a, WebGL) bestaat al. Deze stap maakt **vooraf** een beter
  bestand van een film of aflevering die de beheerder kiest.

## Opzet

1. **Delen zonder PCI-passthrough.** Op de Proxmox-host komt de NVIDIA-driver. De apparaten
   `/dev/nvidia*` gaan met `lxc.cgroup2.devices.allow` en `lxc.mount.entry` naar de LXC van het
   AI-verslag én naar de LXC van het platform op jbogaertmedia2. In die LXC's komt dezelfde
   driverversie, zonder kernelmodule. Docker in de platform-LXC krijgt de NVIDIA container
   toolkit. De stappen komen in een runbook met controles (`nvidia-smi` in elke LXC).
2. **Een eigen container `upscaler`**, alleen op jbogaertmedia2 (in ClusterForge vastgezet op
   die node). Hij haalt werk bij de app via de interne API met een eigen token. Het werk is:
   welk bestand, en vanaf welk stuk.
3. **In stukken van 30 s.** De container doet per stuk dit:
   - ffmpeg pakt de beelden uit op de CPU;
   - het AI-model vergroot ze ×2 op de GPU;
   - de beelden worden weer gecodeerd tot één stuk.
   Elk klaar stuk wordt bewaard. Om :40 stopt hij na het lopende stuk (een stuk duurt hooguit
   ~1 minuut, dus ruim voor :45). Het volgende uur gaat hij verder bij het volgende stuk.
4. **VRAM-grens.** De container kiest de tile-grootte zo dat hij onder een grens blijft (standaard
   4 GB van de 8 GB). Ziet `nvidia-smi` vóór de start een ander proces met veel geheugen, dan
   wacht hij tot het volgende venster.
5. **Klaar:** de stukken gaan samen in één mkv, met de audio en de ondertitels van het origineel.
   Die mkv komt als **tweede versie** naast het origineel, met de naam `<naam> - AI 4K.mkv`. De
   scanner van fase 5 ziet hem dan als editie. Het origineel blijft staan.
6. **Kiezen:** op de pagina van een film of aflevering komt een knop "AI-upscaling" (alleen voor
   beheerders), plus een lijst van wat in de rij staat. Er komt ook een regel "alles onder 1080p
   in bibliotheek X". Standaard staat die regel uit.

## Het model

Real-ESRGAN (`realesr-animevideov3` voor tekenfilm, `RealESRGAN_x4plus` (×2-variant) voor
film), met de open gewichten (BSD-3). Het draait via ncnn/Vulkan of via ONNX Runtime met CUDA.
De keuze tussen die twee volgt uit een meting op de 3060 Ti. Een eigen model trainen is geen
optie, maar de keten eromheen (venster, stukken, hervatten, VRAM, versie) bouwen we zelf.

Schatting: 1080p → 2160p haalt ongeveer 8–15 beelden/s. Een film van 2 uur (≈170.000 beelden)
is dan ongeveer 4–6 uur GPU. Met 20 minuten per uur is dat ruwweg 12–18 uur op de klok,
ongeveer één film per dag. 720p → 1440p gaat ongeveer twee keer zo snel.

## Beslist

- **Coderen met NVENC** op de 3060 Ti, alleen binnen het venster (Jonas, 22-11-2026, kaart).
  Voor de rest blijft de regel staan: transcoderen bij het kijken gebeurt op de CPU.
