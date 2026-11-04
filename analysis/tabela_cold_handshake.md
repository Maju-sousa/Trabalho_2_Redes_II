| Protocolo | Cenário | amostras | handshake_ms_media | handshake_ms_mediana | handshake_ms_desvio | handshake_ms_p50 | handshake_ms_p99 | handshake_ms_ic95 | cold_request_to_body_ms_media | cold_request_to_body_ms_mediana | cold_request_to_body_ms_desvio | cold_request_to_body_ms_p50 | cold_request_to_body_ms_p99 | cold_request_to_body_ms_ic95 | handshake_em_RTTs | cold_request_to_body_em_RTTs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HTTP/1.1 | LAN | 30 | 3.527 | 3.359 | 0.909 | 3.359 | 6.088 | 0.339 | 14.759 | 12.063 | 14.297 | 12.063 | 68.364 | 5.339 | 3.527 | 14.759 |
| HTTP/1.1 | WAN | 30 | 53.477 | 53.294 | 0.747 | 53.294 | 55.937 | 0.279 | 118.372 | 111.984 | 20.696 | 111.984 | 190.246 | 7.728 | 1.070 | 2.367 |
| HTTPS (HTTP/2) | LAN | 30 | 12.733 | 10.402 | 9.657 | 10.402 | 49.412 | 3.606 | 37.450 | 28.319 | 29.411 | 28.319 | 140.844 | 10.982 | 12.733 | 37.450 |
| HTTPS (HTTP/2) | WAN | 30 | 123.860 | 108.968 | 68.953 | 108.968 | 392.796 | 25.747 | 244.123 | 218.536 | 84.500 | 218.536 | 571.880 | 31.553 | 2.477 | 4.882 |
| HTTP/3 (QUIC) | LAN | 30 | 18.509 | 13.995 | 19.436 | 13.995 | 93.220 | 7.258 | 27.726 | 22.987 | 22.974 | 22.987 | 116.075 | 8.579 | 18.509 | 27.726 |
| HTTP/3 (QUIC) | WAN | 30 | 70.091 | 65.090 | 19.181 | 65.090 | 145.258 | 7.162 | 132.483 | 122.279 | 29.887 | 122.279 | 243.582 | 11.160 | 1.402 | 2.650 |