#!/usr/bin/env bash
# Start Pebble (de test-CA van Let's Encrypt) en een DNS-server die elk domein naar
# 127.0.0.1 laat wijzen, voor tests/test_acme_pebble.py. Pebble controleert de
# HTTP-01-challenge echt, op poort 5002 van 127.0.0.1.
#
#   eval "$(tests/pebble/start.sh)"   # zet PEBBLE_DIRECTORY en PEBBLE_CA
set -euo pipefail
version=v2.10.1
bin=$(go env GOPATH)/bin
src=$(go env GOMODCACHE)/github.com/letsencrypt/pebble/v2@$version
for tool in pebble pebble-challtestsrv; do
  [ -x "$bin/$tool" ] || go install "github.com/letsencrypt/pebble/v2/cmd/$tool@$version" >&2
done
out=${PEBBLE_OUT:-${TMPDIR:-/tmp}/pebble}
mkdir -p "$out"
cat > "$out/config.json" <<JSON
{
  "pebble": {
    "listenAddress": "127.0.0.1:14000",
    "managementListenAddress": "127.0.0.1:15000",
    "certificate": "$src/test/certs/localhost/cert.pem",
    "privateKey": "$src/test/certs/localhost/key.pem",
    "httpPort": 5002,
    "tlsPort": 5001,
    "ocspResponderURL": "",
    "externalAccountBindingRequired": false,
    "retryAfter": {"authz": 1, "order": 1},
    "keyAlgorithm": "ecdsa",
    "profiles": {"default": {"description": "test", "validityPeriod": 7776000}}
  }
}
JSON
"$bin/pebble-challtestsrv" -defaultIPv4 127.0.0.1 -defaultIPv6 "" -dnsserver 127.0.0.1:8053 \
  -http01 "" -https01 "" -tlsalpn01 "" -doh "" -management 127.0.0.1:8055 \
  > "$out/challtestsrv.log" 2>&1 &
PEBBLE_VA_NOSLEEP=1 "$bin/pebble" -config "$out/config.json" -dnsserver 127.0.0.1:8053 \
  > "$out/pebble.log" 2>&1 &
for _ in $(seq 1 50); do
  if curl -fs --cacert "$src/test/certs/pebble.minica.pem" -o /dev/null https://localhost:14000/dir; then
    echo "export PEBBLE_DIRECTORY=https://localhost:14000/dir"
    echo "export PEBBLE_CA=$src/test/certs/pebble.minica.pem"
    exit 0
  fi
  sleep 0.2
done
echo "Pebble start niet; zie $out/pebble.log" >&2
exit 1
