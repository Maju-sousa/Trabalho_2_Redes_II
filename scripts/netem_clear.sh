#!/usr/bin/env bash
set -uo pipefail
CONTAINERS=(server_http11 server_http2_tls server_http3_quic bench_client)
failed=0

for c in "${CONTAINERS[@]}"; do
  if ! docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null | grep -qx true; then
    echo "[netem] $c: contêiner parado/ausente (ignorado)"
    continue
  fi
  if ! current="$(docker exec "$c" tc qdisc show dev eth0 2>&1)"; then
    echo "[netem] ERRO lendo qdisc de $c: $current" >&2
    failed=1
    continue
  fi
  if grep -qE '^qdisc netem .* root' <<<"$current"; then
    if docker exec --privileged "$c" tc qdisc del dev eth0 root; then
      echo "[netem] $c: qdisc netem removida"
    else
      echo "[netem] ERRO removendo qdisc netem de $c" >&2
      failed=1
    fi
  else
    echo "[netem] $c: nenhuma qdisc netem ativa"
  fi
done

exit "$failed"
