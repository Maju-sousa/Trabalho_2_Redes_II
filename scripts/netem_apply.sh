#!/usr/bin/env bash
set -euo pipefail

SCENARIO="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"
SERVERS=(server_http11 server_http2_tls server_http3_quic)
CLIENT=bench_client

case "$SCENARIO" in
  lan) DELAY="0.5ms"; LOSS="" ;;
  wan) DELAY="25ms";  LOSS=" loss 2%" ;;
  *) echo "Uso: $0 <lan|wan> [--no-validate]" >&2; exit 2 ;;
esac

ensure_tc() {  
  local c="$1"
  if ! docker exec "$c" sh -c 'tc -V 2>&1 | grep -qi iproute2'; then
    docker exec --privileged "$c" apk add --no-cache iproute2 >/dev/null \
      || { echo "ERRO: não foi possível instalar iproute2 em $c (precisa de Internet)" >&2; exit 1; }
  fi
}

for c in "${SERVERS[@]}"; do
  ensure_tc "$c"
  docker exec --privileged "$c" tc qdisc del dev eth0 root 2>/dev/null || true
  # shellcheck disable=SC2086
  docker exec --privileged "$c" tc qdisc add dev eth0 root netem delay ${DELAY}${LOSS}
  echo "[netem] $c -> netem delay ${DELAY}${LOSS}"
done

# Cliente (Debian slim; iproute2 já vem da imagem do Dockerfile.client)
docker exec --privileged "$CLIENT" tc qdisc del dev eth0 root 2>/dev/null || true
docker exec --privileged "$CLIENT" tc qdisc add dev eth0 root netem delay ${DELAY}
echo "[netem] $CLIENT -> netem delay ${DELAY}"

# Validação NO CAMINHO REAL do benchmark: ping cliente -> cada servidor.
if [[ "${2:-}" != "--no-validate" ]]; then
  echo "[netem] cenário ${SCENARIO}: RTT esperado = 2 x ${DELAY}  (perda esperada ~ ${LOSS:-0%})"
  for ip in 172.20.0.10 172.20.0.20 172.20.0.30; do
    echo "[netem] ping ${CLIENT} -> ${ip}:"
    docker exec "$CLIENT" ping -c 20 -i 0.2 -W 2 "$ip" 2>&1 | tail -n 3 || true
  done
fi
