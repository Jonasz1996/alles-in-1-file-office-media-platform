# De upscaler (fase 11, stap 5c). Alleen voor de node met de RTX 3060 Ti (jbogaertmedia2).
# - ffmpeg van Jellyfin (jellyfin-ffmpeg 7): met NVENC voor het coderen van het resultaat
# - realesrgan-ncnn-vulkan (BSD-3, met de open modellen): het AI-vergroten via Vulkan
# - upscaler.py: de lus, alleen de standaardbibliotheek van Python
# De NVIDIA-driver komt van de host (NVIDIA container toolkit); zie README.md.
FROM debian:bookworm-slim

ARG REALESRGAN_VERSION=v0.2.5.0
ARG REALESRGAN_ZIP=realesrgan-ncnn-vulkan-20220424-ubuntu.zip

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg unzip python3 libvulkan1 libgomp1 \
 && curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key | gpg --dearmor -o /usr/share/keyrings/jellyfin.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/jellyfin.gpg] https://repo.jellyfin.org/debian bookworm main" \
      > /etc/apt/sources.list.d/jellyfin.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends jellyfin-ffmpeg7 \
 && ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
 && ln -s /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe \
 && curl -fsSL -o /tmp/r.zip \
      "https://github.com/xinntao/Real-ESRGAN/releases/download/${REALESRGAN_VERSION}/${REALESRGAN_ZIP}" \
 && mkdir -p /opt/realesrgan && unzip -q /tmp/r.zip -d /opt/realesrgan && rm /tmp/r.zip \
 && chmod +x /opt/realesrgan/realesrgan-ncnn-vulkan \
 && ln -s /opt/realesrgan/realesrgan-ncnn-vulkan /usr/local/bin/realesrgan-ncnn-vulkan \
 && apt-get purge -y gnupg unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# realesrgan-ncnn-vulkan zoekt zijn modellen in ./models
WORKDIR /opt/realesrgan
COPY upscaler.py /usr/local/bin/upscaler.py

ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility,graphics \
    UPSCALER_WORK=/work

RUN useradd -u 10001 -M -d /work upscaler && mkdir -p /work && chown 10001:10001 /work
USER 10001
CMD ["python3", "-u", "/usr/local/bin/upscaler.py"]
