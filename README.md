# TP2 — Redes II: HTTP/1.1, HTTP/2 + TLS 1.3 e HTTP/3/QUIC

Este pacote contém o código e a infraestrutura do experimento. Os diretórios `results/`, `analysis/`, `results_QUICK/`, `analysis_QUICK/` e `data/` são entregues sem resultados nem arquivos de carga; a execução cria o necessário. O relatório e o vídeo não fazem parte deste pacote.

## Execução

No Windows, use **WSL2 com integração do Docker Desktop** e entre na pasta do projeto a partir do terminal Linux. Em Linux, use Bash. Pré-requisitos: Docker funcionando e o plugin `docker compose` (ou `docker-compose`), além de acesso à Internet na primeira compilação das imagens. Não é necessário instalar as bibliotecas Python no host: o runner constrói o cliente e instala as dependências dentro do contêiner.

```bash
bash ./run_experiments.sh
```

O comando acima executa a bateria completa, com 10 repetições para cada teste da Carga A, 10 para cada teste da Carga B e 30 conexões novas por protocolo/cenário nos ensaios cold. Para conferir a infraestrutura mais rapidamente, use `QUICK=1 bash ./run_experiments.sh`; os dados rápidos ficam separados em `results_QUICK/` e `analysis_QUICK/`, nunca misturados com os resultados completos.

O runner gera os arquivos de carga exatos (50.000.000 bytes e 10.000 bytes), valida os três servidores, executa os testes, salva JSON/CSV e logs de RTT, gera tabelas/gráficos e, ao final, remove os contêineres e tenta limpar as regras `tc netem`. Uma nova execução apaga somente os arquivos gerados no diretório de resultados correspondente ao modo selecionado. Mantenha o terminal aberto até o comando terminar.

## Matriz executada

A sequência da matriz é preservada: T01–T03 (Carga A, LAN, HTTP/1.1, HTTP/2, HTTP/3); T04–T06 (Carga A, WAN); T07–T09 (Carga B, LAN); T10–T12 (Carga B, WAN). Ensaios cold adicionais são identificados à parte e não substituem os 12 testes.

- **Carga A:** um arquivo de 50 MB decimais (50.000.000 bytes).
- **Carga B:** 100 arquivos de 10 KB decimais (10.000 bytes), concorrência 100.
- **LAN:** atraso de 0,5 ms na saída do cliente e dos servidores, equivalente a RTT nominal de 1 ms; sem perda.
- **WAN:** atraso de 25 ms na saída do cliente e dos servidores, equivalente a RTT nominal de 50 ms. Conforme os comandos de referência do enunciado, os 2% de perda são aplicados na saída dos servidores (sentido servidor→cliente), não na saída do cliente.

## Protocolos e conexões

- HTTP/1.1 é servido pelo primeiro Nginx em TCP/80 (porta publicada 8080).
- HTTP/2 é servido pelo segundo Nginx sobre TLS 1.3/ALPN `h2` em TCP/443 (porta publicada 8443).
- HTTP/3 é servido pelo Caddy/QUIC em UDP/443 (também publica TCP/443; porta externa 9443).
- HTTP/2 e HTTP/3 reutilizam uma conexão warm para a Carga B e multiplexam os 100 fluxos/requisições. HTTP/1.1 não multiplexa; para Carga B o pool pode abrir até 100 conexões paralelas dentro da janela medida, conforme o teste de overhead de múltiplos handshakes. Não interprete essa exceção como uma única conexão HTTP/1.1 aquecida.
- Nos testes cold cada solicitação usa uma conexão nova, encerrada antes da próxima. O tempo de handshake é registrado separadamente. As requisições desativam cache HTTP e compressão (`Cache-Control: no-cache`, `Pragma: no-cache`, `Accept-Encoding: identity`).

## Métricas e arquivos de saída

Cada requisição recebe `timestamp_utc` no início da medição. `ttfb_ms` é o intervalo entre o momento em que o parser HTTP disponibiliza os cabeçalhos completos e o primeiro byte do corpo. `request_to_first_body_ms` mede início da requisição até o primeiro byte do corpo (inclui handshake em cold); `handshake_ms` registra o estabelecimento da conexão; `total_time_ms` mede até o último byte. São gerados RPS, vazão, média, mediana, desvio-padrão, p50, p99 e IC95 nas tabelas, além de gráficos com barras de erro.

**Limite de instrumentação:** `httpx` e `aioquic` expõem o evento de cabeçalhos já decodificados, não o instante de chegada do primeiro byte bruto do cabeçalho. Assim, `ttfb_ms` usa o instante observável de cabeçalhos completos→primeiro byte do corpo; o relatório deve explicitar essa aproximação, em vez de afirmar que foi capturado o primeiro byte bruto do cabeçalho.

Resultados completos: `results/` (JSON individuais, `results.json`, `results.csv` e logs `rtt_validacao_*.txt`) e `analysis/` (tabelas e gráficos). Resultados de execução rápida: pastas terminadas em `_QUICK`.

## Componentes

- `docker-compose.yml`: três servidores, cliente de benchmark na rede Docker isolada e capacidades `NET_ADMIN`.
- `nginx/http2-tls.conf`, `Caddyfile`: configuração dos servidores HTTP/2 e HTTP/3.
- `scripts/netem_apply.sh`, `scripts/netem_clear.sh`: aplicação, validação e limpeza do `tc netem`.
- `benchmark_redes.py`: coleta de métricas HTTP/1.1, HTTP/2 e HTTP/3.
- `analisar_resultados.py`: consolidação CSV/JSON, tabelas e figuras.
- `gerar_cargas.py`: geração/validação dos arquivos de carga.
