#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ "${QUICK:-0}" == "1" ]]; then
  REPS_A="${REPS_A:-1}"
  REPS_B="${REPS_B:-3}"
  COLD_COUNT="${COLD_COUNT:-5}"
  RESULTS_DIR="results_QUICK"
  ANALYSIS_DIR="analysis_QUICK"
else
  REPS_A="${REPS_A:-10}"
  REPS_B="${REPS_B:-10}"
  COLD_COUNT="${COLD_COUNT:-30}"
  RESULTS_DIR="results"
  ANALYSIS_DIR="analysis"
fi
WARMUP_REPS="${WARMUP_REPS:-1}"
FAILED=()
STACK_STARTED=0
URL_H1="http://172.20.0.10:80"
URL_H2="https://172.20.0.20:443"
URL_H3="https://172.20.0.30:443"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "ERRO: Docker Compose não foi encontrado." >&2
  exit 1
fi

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ "$STACK_STARTED" == "1" ]]; then
    echo
    echo ">>> Limpando regras tc e encerrando os contêineres..."
    bash scripts/netem_clear.sh || true
    "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$RESULTS_DIR" "$ANALYSIS_DIR" data
# Evita misturar execução nova com resultados antigos do mesmo modo.
find "$RESULTS_DIR" -mindepth 1 -maxdepth 1 ! -name .gitkeep -exec rm -rf -- {} +
find "$ANALYSIS_DIR" -mindepth 1 -maxdepth 1 ! -name .gitkeep -exec rm -rf -- {} +

if [[ ! -s certs/cert.pem || ! -s certs/key.pem ]]; then
  echo "ERRO: faltam certs/cert.pem e/ou certs/key.pem. Restaure os certificados locais do pacote." >&2
  exit 1
fi

wait_for() {  # protocolo, URL, rótulo
  local proto="$1" url="$2" label="$3"
  for _ in $(seq 1 30); do
    if docker exec -w /app bench_client python benchmark_redes.py \
      --protocol "$proto" --base-url "$url" --mode cold --cold-count 1 \
      --repetitions 1 --verify-tls false --output /tmp/ready.json --fail-on-error \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "ERRO: $label não respondeu corretamente em $url" >&2
  return 1
}

apply_netem() {  # lan|wan, identificador do log
  local scenario="$1" tag="$2"
  local lower
  lower="$(printf '%s' "$scenario" | tr '[:upper:]' '[:lower:]')"
  echo
  echo ">>> Aplicando cenário $scenario ($tag) e salvando a validação RTT..."
  bash scripts/netem_apply.sh "$lower" | tee "$RESULTS_DIR/rtt_validacao_${tag}.txt"
  sleep 2
}

clear_netem() {
  bash scripts/netem_clear.sh
}

run_bench() {  # id, protocolo, URL, cenário, modo, argumentos adicionais
  local id="$1" proto="$2" url="$3" scenario="$4" mode="$5"
  shift 5
  local out="$RESULTS_DIR/${id}.json"
  echo "---- $id: $proto | $scenario | $mode"
  if docker exec -w /app bench_client python benchmark_redes.py \
    --protocol "$proto" --base-url "$url" --scenario "$scenario" \
    --mode "$mode" --verify-tls false --output "$out" --fail-on-error \
    --warmup-reps "$WARMUP_REPS" "$@"; then
    :
  else
    echo "FALHA: $id (ver $out)" >&2
    FAILED+=("$id")
  fi
}

run_cold_scenario() {
  local scenario="$1" lower
  lower="$(printf '%s' "$scenario" | tr '[:upper:]' '[:lower:]')"
  apply_netem "$scenario" "cold_${lower}"
  run_bench "cold_http1_${lower}" http1 "$URL_H1" "$scenario" cold --cold-count "$COLD_COUNT" --repetitions 1
  run_bench "cold_http2_${lower}" http2 "$URL_H2" "$scenario" cold --cold-count "$COLD_COUNT" --repetitions 1
  run_bench "cold_http3_${lower}" http3 "$URL_H3" "$scenario" cold --cold-count "$COLD_COUNT" --repetitions 1
  clear_netem
}

# Pré-requisitos da rede e subida da bancada.
echo ">>> Iniciando os contêineres (dependências Python instaladas na imagem do cliente)..."
"${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
STACK_STARTED=1
"${DC[@]}" up -d --build

echo ">>> Gerando/validando os arquivos de carga dentro do cliente..."
docker exec -w /app bench_client python gerar_cargas.py

wait_for http1 "$URL_H1" "HTTP/1.1"
wait_for http2 "$URL_H2" "HTTPS/HTTP/2 + TLS 1.3"
wait_for http3 "$URL_H3" "HTTP/3/QUIC"

# Ensaios cold extras: uma conexão nova por requisição; não substituem T01–T12.
run_cold_scenario LAN
run_cold_scenario WAN

# Matriz exatamente na ordem numérica do enunciado.
apply_netem LAN "T01-T03_lan"
run_bench T01_http1_lan_a http1 "$URL_H1" LAN warm --profiles A --repetitions "$REPS_A"
run_bench T02_http2_lan_a http2 "$URL_H2" LAN warm --profiles A --repetitions "$REPS_A"
run_bench T03_http3_lan_a http3 "$URL_H3" LAN warm --profiles A --repetitions "$REPS_A"
clear_netem

apply_netem WAN "T04-T06_wan"
run_bench T04_http1_wan_a http1 "$URL_H1" WAN warm --profiles A --repetitions "$REPS_A"
run_bench T05_http2_wan_a http2 "$URL_H2" WAN warm --profiles A --repetitions "$REPS_A"
run_bench T06_http3_wan_a http3 "$URL_H3" WAN warm --profiles A --repetitions "$REPS_A"
clear_netem

apply_netem LAN "T07-T09_lan"
run_bench T07_http1_lan_b http1 "$URL_H1" LAN warm --profiles B --repetitions "$REPS_B" --concurrency 100
run_bench T08_http2_lan_b http2 "$URL_H2" LAN warm --profiles B --repetitions "$REPS_B" --concurrency 100
run_bench T09_http3_lan_b http3 "$URL_H3" LAN warm --profiles B --repetitions "$REPS_B" --concurrency 100
clear_netem

apply_netem WAN "T10-T12_wan"
run_bench T10_http1_wan_b http1 "$URL_H1" WAN warm --profiles B --repetitions "$REPS_B" --concurrency 100
run_bench T11_http2_wan_b http2 "$URL_H2" WAN warm --profiles B --repetitions "$REPS_B" --concurrency 100
run_bench T12_http3_wan_b http3 "$URL_H3" WAN warm --profiles B --repetitions "$REPS_B" --concurrency 100
clear_netem

if [[ "${SKIP_ANALYSIS:-0}" != "1" ]]; then
  echo
  echo ">>> Consolidando JSON/CSV e gerando tabelas/gráficos em $ANALYSIS_DIR/ ..."
  docker exec -w /app bench_client python analisar_resultados.py \
    --results "$RESULTS_DIR" --out "$ANALYSIS_DIR"
fi

if ((${#FAILED[@]})); then
  echo "ERRO: a bateria terminou com falhas nos testes: ${FAILED[*]}" >&2
  echo "Confira os campos 'error' nos arquivos JSON em $RESULTS_DIR/." >&2
  exit 1
fi

echo
echo ">>> Sucesso: T01–T12 e ensaios cold concluídos. Resultados: $RESULTS_DIR/; análise: $ANALYSIS_DIR/."
