#!/usr/bin/env bash
# De echte OnlyOffice Docs (DS) 10.0.0.1 voor de e2e met marker office_real (bouwontwerp
# fase 4, §4.5). De job office in CI gebruikt precies dit script; lokaal ook (docs/fase-4/e2e.md).
#
#   bash e2e/office_ds.sh start    image ophalen (op digest) en een nieuwe container starten
#   bash e2e/office_ds.sh wait     wachten tot DS gezond is en zijn font-herstart achter de rug heeft
#   bash e2e/office_ds.sh logs     de logs van DS naar $E2E_ARTIFACTS (bij een fout in CI)
#   bash e2e/office_ds.sh stop     de container weg (het image blijft)
#
# Instellingen (omgeving):
#   OFFICE_DS_NAME   naam van de container (standaard e2e-office-ds)
#   OFFICE_DS_PORT   poort op 127.0.0.1 (standaard 18080)
#   OFFICE_DS_DIR    map voor de geheimen (standaard $RUNNER_TEMP/office-ds of /tmp/office-ds);
#                    jwt.secret is daarna E2E_REAL_DS_SECRET_FILE voor de tests
#   OFFICE_DS_CONFIG optioneel: een local-production-linux.json voor DS (read-only gemount); na O6
#                    infra/single/office/local-production-linux.json (§4.5 stap 2). Zonder: de
#                    standaard van DS (onder meer 3 pogingen voor een callback in plaats van 8)
#
# De geheimen maakt het script zelf, per keer, met 600-rechten. Ze staan nooit op de
# opdrachtregel en nooit in een log; docker krijgt ze als omgeving.
set -euo pipefail

OFFICE_REPO=onlyoffice/documentserver
OFFICE_TAG=10.0.0.1
OFFICE_DIGEST=sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3

NAME=${OFFICE_DS_NAME:-e2e-office-ds}
PORT=${OFFICE_DS_PORT:-18080}
DIR=${OFFICE_DS_DIR:-${RUNNER_TEMP:-/tmp}/office-ds}
# Gezond binnen 6 minuten, en de herstart van docservice voor de fonts (2:37–2:53 na de start
# van een nieuwe container, spike extra bevinding 1) binnen 6 minuten na de start
WAIT_SECONDS=${OFFICE_DS_WAIT_SECONDS:-360}

hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }

# Op digest: eerst mirror.gcr.io, dan Docker Hub, drie rondes (§4.5 stap 1; Docker Hub geeft
# soms 429). Daarna de naam van Docker Hub erop (docker tag), zoals install.sh (Keuze 29 en 30).
# Geeft de naam waaronder het image er nu is. Een image dat er al is, wordt niet opnieuw gehaald.
image() {
  local hub="$OFFICE_REPO@$OFFICE_DIGEST" mirror="mirror.gcr.io/$OFFICE_REPO@$OFFICE_DIGEST" img
  for _ in 1 2 3; do
    for img in "$mirror" "$hub"; do
      if docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null 2>&1; then
        docker tag "$img" "$OFFICE_REPO:$OFFICE_TAG"
        printf '%s' "$img"
        return 0
      fi
    done
    sleep 5
  done
  return 1
}

start() {
  local img
  if ! img=$(image); then
    echo "Het image van DS ($OFFICE_REPO@$OFFICE_DIGEST) is niet op te halen, ook niet van mirror.gcr.io." >&2
    return 1
  fi
  echo "Image: $img"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  local mount=()
  if [ -n "${OFFICE_DS_CONFIG:-}" ]; then
    mount=(-v "$(realpath "$OFFICE_DS_CONFIG"):/etc/onlyoffice/documentserver/local-production-linux.json:ro")
  fi
  mkdir -p "$DIR"
  chmod 700 "$DIR"
  (umask 077 && hex >"$DIR/jwt.secret" && hex >"$DIR/link.secret")
  # Zoals §4.5 stap 2. De standaardbridge (met uitgang): het interne netwerk bewijst de
  # integratie (§4.6 punt 3). host.docker.internal is de host, voor de interne luisteraar van
  # de app en de bronnen van de tests.
  JWT_SECRET="$(cat "$DIR/jwt.secret")" SECURE_LINK_SECRET="$(cat "$DIR/link.secret")" \
    docker run -d --name "$NAME" -p "127.0.0.1:$PORT:80" \
    --add-host=host.docker.internal:host-gateway \
    -e JWT_ENABLED=true -e JWT_SECRET -e JWT_HEADER=Authorization -e SECURE_LINK_SECRET \
    -e WELCOME_PAGE_ENABLED=false -e PLUGINS_ENABLED=false \
    "${mount[@]}" "$img" >/dev/null
  date +%s >"$DIR/started"
  echo "DS start als $NAME op http://127.0.0.1:$PORT (geheim in $DIR/jwt.secret)"
}

healthy() {
  [ "$(curl -fsS -m 5 "http://127.0.0.1:$PORT/healthcheck" 2>/dev/null || true)" = true ]
}

# De font-herstart is voorbij als supervisord de converter een tweede keer startte
restarted() {
  docker exec "$NAME" grep -q "stopped: converter" /var/log/supervisor/supervisord.log 2>/dev/null
}

wait_ready() {
  local start now
  start=$(cat "$DIR/started" 2>/dev/null || date +%s)
  while :; do
    now=$(date +%s)
    if healthy && restarted; then
      echo "DS is gezond en de font-herstart is voorbij ($((now - start)) s na de start)"
      return 0
    fi
    if [ "$((now - start))" -ge "$WAIT_SECONDS" ]; then
      if healthy; then
        echo "Waarschuwing: DS is gezond, maar zijn font-herstart is na $WAIT_SECONDS s niet gezien." >&2
        return 0
      fi
      echo "DS werd niet gezond binnen $WAIT_SECONDS s." >&2
      docker logs --tail 50 "$NAME" >&2 || true
      return 1
    fi
    sleep 3
  done
}

logs() {
  local out=${E2E_ARTIFACTS:-.}
  mkdir -p "$out"
  docker logs "$NAME" >"$out/ds-docker.log" 2>&1 || true
  docker exec "$NAME" cat /var/log/supervisor/supervisord.log >"$out/ds-supervisord.log" 2>&1 || true
  docker exec "$NAME" sh -c 'cat /var/log/onlyoffice/documentserver/docservice/out.log' \
    >"$out/ds-docservice.log" 2>&1 || true
  echo "Logs van DS in $out"
}

stop() {
  docker rm -f -v "$NAME" >/dev/null 2>&1 || true
  rm -f "$DIR/jwt.secret" "$DIR/link.secret" "$DIR/started"
}

case "${1:-}" in
  start) start ;;
  wait) wait_ready ;;
  logs) logs ;;
  stop) stop ;;
  *)
    echo "Gebruik: $0 start|wait|logs|stop" >&2
    exit 2
    ;;
esac
