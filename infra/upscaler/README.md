# AI-upscaling op de RTX 3060 Ti (fase 11, stap 5c)

De upscaler vergroot gekozen films en afleveringen vooraf met AI (Real-ESRGAN) en zet het
resultaat als tweede versie `<naam> - AI 4K.mkv` naast het origineel. Hij gebruikt de kaart
alleen in het venster van /beheer → Media → AI-upscaling (standaard minuut 20 tot 40 van elk
uur). Zo gaan het AI-verslag (:00 tot :15) en de security-analyse (:45 tot :00) voor. Ontwerp:
`docs/fase-11/ai-upscaling.md`.

Alles hieronder gebeurt op **jbogaertmedia2** (192.168.10.147), op de Proxmox-host van die node.
Er komt **geen PCI-passthrough** aan te pas: de host krijgt de driver, en de LXC's krijgen alleen
de apparaten `/dev/nvidia*`. Zo delen de LXC van het AI-verslag en de LXC van het platform de
kaart.

## 1. De driver op de Proxmox-host

```bash
# Op de host (shell van de node in Proxmox), niet in een LXC
apt update && apt install -y pve-headers-$(uname -r) build-essential
# Download de driver van nvidia.com (Linux 64-bit, bv. 550.x). Onthoud de versie: elke LXC
# krijgt precies dezelfde.
sh NVIDIA-Linux-x86_64-<versie>.run --dkms
nvidia-smi                       # moet de RTX 3060 Ti tonen
ls -l /dev/nvidia*               # noteer de major-nummers (meestal 195 en 5xx)
```

Zorg dat de apparaten bij het opstarten bestaan (anders ziet een LXC ze niet na een herstart):

```bash
cat > /etc/udev/rules.d/70-nvidia.rules <<'R'
KERNEL=="nvidia", RUN+="/bin/bash -c '/usr/bin/nvidia-smi -L && /bin/chmod 666 /dev/nvidia*'"
KERNEL=="nvidia_uvm", RUN+="/bin/bash -c '/usr/bin/nvidia-modprobe -c0 -u && /bin/chmod 0666 /dev/nvidia-uvm*'"
R
echo -e "nvidia\nnvidia_uvm" >> /etc/modules-load.d/nvidia.conf
```

## 2. De kaart naar de LXC's

Doe dit voor **beide** LXC's: die van het AI-verslag en die van het platform op deze node. Zet in
`/etc/pve/lxc/<CTID>.conf` (de nummers 195 en 5xx van `ls -l /dev/nvidia*` hierboven):

```
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 5xx:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
```

Herstart de LXC. Installeer dan **in de LXC** dezelfde driverversie, maar zonder kernelmodule:

```bash
sh NVIDIA-Linux-x86_64-<versie>.run --no-kernel-module
nvidia-smi                       # dezelfde kaart als op de host
```

Draait het AI-verslag daarna nog zoals voorheen? Controleer dat eerst, vóór je verdergaat.

## 3. Docker en de NVIDIA container toolkit (alleen in de LXC van het platform)

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit.gpg] https://#' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt update && apt install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
# In een unprivileged LXC mag de toolkit geen cgroups instellen:
sed -i 's/^#\?no-cgroups = .*/no-cgroups = true/' /etc/nvidia-container-runtime/config.toml
systemctl restart docker
docker run --rm --gpus all debian:bookworm-slim nvidia-smi   # moet de kaart tonen
```

## 4. De upscaler starten

1. Op /beheer → Media → AI-upscaling: **Token voor de upscaler maken** en het token kopiëren.
   Zet daar ook **Aan** en kijk of het venster 20 tot 40 is.
2. In de LXC van het platform op jbogaertmedia2:

```bash
cd /opt/platform/src/infra/upscaler      # of waar de repo staat
cp .env.example .env && nano .env        # PLATFORM_URL en UPSCALER_TOKEN
docker compose up -d --build
docker compose logs -f                    # "het venster gaat dicht" of "klaar" zijn goed
```

3. Kies een film met de knop **AI-upscaling** op zijn pagina. Tussen :20 en :40 zie je hem op
   /beheer lopen (percentage). Na het venster wacht hij tot het volgende uur.

## Wat je kunt verwachten

- Ongeveer 1 film van 2 uur per dag (1080p → 2160p). Dit is een schatting die nog op de kaart
  gemeten moet worden.
- Tijdelijke ruimte in `work/`: het origineel, ~4 GB per lopend stuk, plus de klare stukken.
- Het videogeheugen blijft onder de grens van /beheer (standaard 4096 MB). Is er minder vrij,
  bijvoorbeeld omdat een andere AI-taak uitloopt, dan wacht de upscaler tot het volgende venster.
