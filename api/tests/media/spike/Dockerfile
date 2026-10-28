# Het afgeleide image voor S3 van de spike (bouwontwerp fase 5, §3.7): het platform-image met de
# drie regels van Keuze 47. M1 zet die regels later in het echte Dockerfile; tot dan bouwt de job
# media-spike dit image boven op platform:ci.
ARG BASE=platform:ci
FROM ${BASE}
USER root
RUN useradd --system --uid 10002 --no-create-home media
RUN install -d -o media -g platform -m 2750 /run/media
RUN chmod 0711 /usr/bin/ffmpeg /usr/bin/ffprobe
USER platform
