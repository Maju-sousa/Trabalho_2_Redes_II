#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import ssl
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

LARGE_BYTES = 50_000_000  #50 MB decimais
SMALL_BYTES = 10_000      # 10 KB decimais
EXPECTED_HTTPX_VERSION = {"http1": "HTTP/1.1", "http2": "HTTP/2"}
REQUEST_HEADERS = {
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Accept-Encoding": "identity",
    "User-Agent": "redes2-benchmark/2.0",
}



def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ms(t0: float, t1: float) -> float:
    """Diferença em milissegundos com resolução de microssegundos."""
    return round((t1 - t0) * 1000.0, 3)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def record(*, protocol: str, mode: str, scenario: str, profile: str, repetition: int,
           request_id: int, url: str, status: int | None, nbytes: int, expected: int,
           ttfb: float | None, request_to_first_body: float | None,
           headers: float | None, handshake: float | None, elapsed: float,
           http_version: str | None, error: str | None,
           timestamp_utc: str | None = None) -> dict[str, Any]:
    return {
        "record_type": "request", "timestamp_utc": timestamp_utc or iso_now(), "protocol": protocol,
        "mode": mode, "scenario": scenario, "profile": profile,
        "repetition": repetition, "request_id": request_id, "url": url,
        "http_version": http_version, "status_code": status,
        "success": error is None, "bytes_received": nbytes, "expected_bytes": expected,
        "ttfb_ms": ttfb, "request_to_first_body_ms": request_to_first_body,
        "headers_ms": headers, "handshake_ms": handshake,
        "total_time_ms": elapsed, "error": error,
    }


def summarize(rows: list[dict[str, Any]], protocol: str, mode: str, scenario: str,
              profile: str, repetition: int, wall_seconds: float) -> dict[str, Any]:
    ok = [r for r in rows if r["success"]]

    def col(name: str) -> list[float]:
        return [float(r[name]) for r in ok if r.get(name) is not None]

    ttfbs, totals, hs = col("ttfb_ms"), col("total_time_ms"), col("handshake_ms")
    received = sum(int(r["bytes_received"]) for r in ok)

    def mean(v: list[float]) -> float | None:
        return statistics.mean(v) if v else None

    def std(v: list[float]) -> float | None:
        return statistics.stdev(v) if len(v) > 1 else (0.0 if v else None)

    return {
        "record_type": "summary", "timestamp_utc": iso_now(), "protocol": protocol,
        "mode": mode, "scenario": scenario, "profile": profile, "repetition": repetition,
        "requests_total": len(rows), "requests_success": len(ok),
        "requests_failed": len(rows) - len(ok), "window_seconds": wall_seconds,
        "rps": len(ok) / wall_seconds if wall_seconds > 0 else 0,
        "bytes_received": received,
        "throughput_mbps": received * 8 / wall_seconds / 1e6 if wall_seconds > 0 else 0,
        "ttfb_mean_ms": mean(ttfbs), "ttfb_std_ms": std(ttfbs),
        "ttfb_p50_ms": percentile(ttfbs, 50), "ttfb_p99_ms": percentile(ttfbs, 99),
        "total_mean_ms": mean(totals), "total_std_ms": std(totals),
        "total_p50_ms": percentile(totals, 50), "total_p99_ms": percentile(totals, 99),
        "handshake_mean_ms": mean(hs), "handshake_p50_ms": percentile(hs, 50),
        "handshake_p99_ms": percentile(hs, 99),
    }


# --------------------------------------------------------------------------- #
# HTTP/1.1 e HTTP/2 (httpx)
# --------------------------------------------------------------------------- #
def new_httpx_client(args: argparse.Namespace, concurrency: int, pooled: bool) -> httpx.AsyncClient:
    """Cria um cliente httpx sem cache e sem proxy do ambiente.

    pooled=False (modo cold): keep-alive desativado -> nenhuma conexão é reaproveitada.
    """
    limits = httpx.Limits(
        max_connections=max(concurrency, 1),
        max_keepalive_connections=max(concurrency, 1) if pooled else 0,
        keepalive_expiry=60.0 if pooled else 0.0,
    )
    return httpx.AsyncClient(
        http2=(args.protocol == "http2"), http1=True, verify=args.verify_tls,
        timeout=args.timeout, limits=limits, follow_redirects=False, trust_env=False,
    )


async def httpx_request(client: httpx.AsyncClient, url: str, args: argparse.Namespace,
                        profile: str, repetition: int, request_id: int,
                        expected: int) -> dict[str, Any]:
    events: dict[str, float] = {}

    async def trace(name: str, info: dict) -> None:  # callback do httpcore
        events.setdefault(name, time.perf_counter())

    timestamp_utc = iso_now()
    start = time.perf_counter()
    first = headers_t = None
    size, status, error, http_version = 0, None, None, None
    try:
        async with client.stream("GET", url, headers=REQUEST_HEADERS,
                                 extensions={"trace": trace}) as response:
            headers_t = time.perf_counter()
            status, http_version = response.status_code, response.http_version
            async for chunk in response.aiter_bytes():
                if chunk and first is None:
                    first = time.perf_counter()
                size += len(chunk)
        if status < 200 or status >= 300:
            error = f"HTTP status {status}"
        elif size != expected:
            error = f"tamanho recebido {size}, esperado {expected}"
        elif http_version != EXPECTED_HTTPX_VERSION[args.protocol]:
            error = f"protocolo negociado {http_version}, esperado {EXPECTED_HTTPX_VERSION[args.protocol]}"
    except Exception as exc:  # noqa: BLE001 - registramos qualquer falha como erro da requisição
        error = f"{type(exc).__name__}: {exc}"
    end = time.perf_counter()

    # Handshake (TCP ou TCP+TLS) só existe se ESTA requisição abriu a conexão.
    handshake = None
    t_start = events.get("connection.connect_tcp.started")
    t_end = events.get("connection.start_tls.complete") or events.get("connection.connect_tcp.complete")
    if t_start is not None and t_end is not None:
        handshake = ms(t_start, t_end)

    return record(protocol=args.protocol, mode=args.mode, scenario=args.scenario,
                  profile=profile, repetition=repetition, request_id=request_id, url=url,
                  status=status, nbytes=size, expected=expected,
                  ttfb=ms(headers_t, first) if headers_t and first else None,
                  request_to_first_body=ms(start, first) if first else None,
                  headers=ms(start, headers_t) if headers_t else None,
                  handshake=handshake, elapsed=ms(start, end),
                  http_version=http_version, error=error, timestamp_utc=timestamp_utc)


# --------------------------------------------------------------------------- #
# HTTP/3 (aioquic)
# --------------------------------------------------------------------------- #
def make_quic_protocol_class():
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.h3.connection import H3Connection
    from aioquic.h3.events import DataReceived, HeadersReceived
    from aioquic.quic.events import ConnectionTerminated

    class Protocol(QuicConnectionProtocol):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self.h3 = H3Connection(self._quic)
            self.responses: dict[int, dict[str, Any]] = {}
            self.waiters: dict[int, asyncio.Future] = {}
            self.started: dict[int, float] = {}

        async def get(self, url: str, timeout: float) -> dict[str, Any]:
            parsed = urlsplit(url)
            path = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
            stream_id = self._quic.get_next_available_stream_id()
            self.started[stream_id] = time.perf_counter()
            self.responses[stream_id] = {"status": None, "bytes": 0, "ttfb": None, "headers": None, "first_body": None}
            future = asyncio.get_running_loop().create_future()
            self.waiters[stream_id] = future
            self.h3.send_headers(stream_id, [
                (b":method", b"GET"), (b":scheme", b"https"),
                (b":authority", parsed.netloc.encode()), (b":path", path.encode()),
                (b"user-agent", REQUEST_HEADERS["User-Agent"].encode()),
                (b"cache-control", b"no-cache"), (b"accept-encoding", b"identity"),
            ], end_stream=True)
            self.transmit()
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            finally:
                self.waiters.pop(stream_id, None)

        def quic_event_received(self, event: Any) -> None:
            if isinstance(event, ConnectionTerminated):
                for fut in self.waiters.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError(
                            f"QUIC encerrado: {event.error_code} {event.reason_phrase}"))
                return
            for http_event in self.h3.handle_event(event):
                self._handle_h3(http_event)

        def _handle_h3(self, event: Any) -> None:
            sid = getattr(event, "stream_id", None)
            if sid is None or sid not in self.started:
                return
            state = self.responses.setdefault(
                sid, {"status": None, "bytes": 0, "ttfb": None, "headers": None, "first_body": None})
            now = time.perf_counter()
            if isinstance(event, HeadersReceived):
                state["headers"] = now - self.started[sid]
                for key, value in event.headers:
                    if key == b":status":
                        try:
                            state["status"] = int(value)
                        except ValueError:
                            pass
            elif isinstance(event, DataReceived):
                if event.data and state["first_body"] is None:
                    state["first_body"] = now - self.started[sid]
                    if state["headers"] is not None:
                        state["ttfb"] = state["first_body"] - state["headers"]
                state["bytes"] += len(event.data)
            else:
                return
            if event.stream_ended:
                fut = self.waiters.get(sid)
                if fut is not None and not fut.done():
                    fut.set_result(dict(state))

    return Protocol


def quic_configuration(args: argparse.Namespace):
    from aioquic.quic.configuration import QuicConfiguration
    config = QuicConfiguration(is_client=True, alpn_protocols=["h3"])
    config.verify_mode = ssl.CERT_REQUIRED if args.verify_tls else ssl.CERT_NONE
    # Janelas de controle de fluxo maiores para não limitar artificialmente a Carga A.
    config.max_data = 64 * 1024 * 1024
    config.max_stream_data = 64 * 1024 * 1024
    return config


async def h3_request(protocol: Any, url: str, args: argparse.Namespace, profile: str,
                     repetition: int, request_id: int, expected: int,
                     handshake: float | None = None) -> dict[str, Any]:
    """Uma requisição HTTP/3. TTFB é cabeçalhos completos->primeiro byte do corpo.
    No cold, request_to_first_body_ms e total_time_ms incluem o handshake QUIC."""
    timestamp_utc = iso_now()
    start = time.perf_counter()
    status, size, ttfb, request_to_first_body, headers, error = None, 0, None, None, None, None
    try:
        result = await protocol.get(url, args.timeout)
        status, size = result["status"], result["bytes"]
        ttfb = None if result["ttfb"] is None else result["ttfb"] * 1000
        request_to_first_body = None if result["first_body"] is None else result["first_body"] * 1000
        headers = None if result["headers"] is None else result["headers"] * 1000
        if status is None or status < 200 or status >= 300:
            error = f"HTTP status {status}"
        elif size != expected:
            error = f"tamanho recebido {size}, esperado {expected}"
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - start) * 1000
    extra = handshake or 0.0
    return record(protocol="http3", mode=args.mode, scenario=args.scenario, profile=profile,
                  repetition=repetition, request_id=request_id, url=url, status=status,
                  nbytes=size, expected=expected,
                  ttfb=None if ttfb is None else round(ttfb, 3),
                  request_to_first_body=None if request_to_first_body is None else round(request_to_first_body + extra, 3),
                  headers=None if headers is None else round(headers + extra, 3),
                  handshake=None if handshake is None else round(handshake, 3),
                  elapsed=round(elapsed + extra, 3), http_version="HTTP/3", error=error,
                  timestamp_utc=timestamp_utc)


def target_of(args: argparse.Namespace, profile_is_large: bool) -> tuple[str, int]:
    path = args.large_path if profile_is_large else args.small_path
    expected = LARGE_BYTES if profile_is_large else SMALL_BYTES
    return args.base_url.rstrip("/") + "/" + path.lstrip("/"), expected


def explain(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 97:
        text += (" | aioquic usa socket IPv6 dual-stack: habilite IPv6 no host "
                 "(sysctl net.ipv6.conf.all.disable_ipv6=0)")
    return text


def error_row(args: argparse.Namespace, profile: str, repetition: int, url: str,
              expected: int, started: float, exc: Exception) -> dict[str, Any]:
    return record(protocol=args.protocol, mode=args.mode, scenario=args.scenario,
                  profile=profile, repetition=repetition, request_id=0, url=url,
                  status=None, nbytes=0, expected=expected, ttfb=None,
                  request_to_first_body=None, headers=None,
                  handshake=None, elapsed=ms(started, time.perf_counter()),
                  http_version=None, error=explain(exc))



# MODO WARM: conexão persistente (Cargas A e B)
async def run_warm_profile(args: argparse.Namespace, profile: str, repetition: int):
    
    large = profile == "A"
    url, expected = target_of(args, large)
    count = 1 if large else args.small_count
    concurrency = 1 if large else args.concurrency
    label = f"Carga {profile}"
    rows: list[dict[str, Any]] = []
    preconnected = 0
    start_wall = time.perf_counter()

    if args.protocol == "http3":
        from aioquic.asyncio import connect
        parsed = urlsplit(args.base_url)
        factory = make_quic_protocol_class()
        try:
            async with connect(parsed.hostname or "localhost", parsed.port or 443,
                               configuration=quic_configuration(args),
                               create_protocol=factory, wait_connected=True) as protocol:
                preconnected = 1
                small_url, small_expected = target_of(args, False)
                await h3_request(protocol, small_url, args, "warmup", repetition, 0, small_expected)
                sem = asyncio.Semaphore(concurrency)

                async def one(i: int) -> dict[str, Any]:
                    async with sem:
                        return await h3_request(protocol, url, args, label, repetition, i + 1, expected)

                start_wall = time.perf_counter()          # janela medida começa AQUI
                rows = list(await asyncio.gather(*(one(i) for i in range(count))))
                wall = time.perf_counter() - start_wall
        except Exception as exc:  # noqa: BLE001
            rows = [error_row(args, label, repetition, url, expected, start_wall, exc)]
            wall = time.perf_counter() - start_wall
    else:
        client = new_httpx_client(args, concurrency, pooled=True)
        try:
            # Pré-conexão: Carga A (1 conexão) e HTTP/2 (1 conexão multiplexada).
            if large or args.protocol == "http2":
                small_url, small_expected = target_of(args, False)
                warm_row = await httpx_request(client, small_url, args, "warmup", repetition, 0,
                                               small_expected)
                if not warm_row["success"]:
                    raise RuntimeError(f"requisição de aquecimento falhou: {warm_row['error']}")
                preconnected = 1
            sem = asyncio.Semaphore(concurrency)

            async def one(i: int) -> dict[str, Any]:
                async with sem:
                    return await httpx_request(client, url, args, label, repetition, i + 1, expected)

            start_wall = time.perf_counter()              # janela medida começa AQUI
            rows = list(await asyncio.gather(*(one(i) for i in range(count))))
            wall = time.perf_counter() - start_wall
        except Exception as exc:  # noqa: BLE001
            rows = [error_row(args, label, repetition, url, expected, start_wall, exc)]
            wall = time.perf_counter() - start_wall
        finally:
            await client.aclose()

    summary = summarize(rows, args.protocol, args.mode, args.scenario, label, repetition, wall)
    # Conexões efetivamente usadas: pré-estabelecidas + abertas durante a janela (handshake != None).
    opened_in_window = sum(1 for r in rows if r.get("handshake_ms") is not None)
    summary["connections_preestablished"] = preconnected
    summary["connections_opened_in_window"] = opened_in_window
    summary["connections_total"] = preconnected + opened_in_window
    return rows, summary



# MODO COLD: uma conexão NOVA por requisição, fechada explicitamente
async def run_cold_profile(args: argparse.Namespace, repetition: int):
    url, expected = target_of(args, args.cold_large)
    label = "Cold TTFB"
    rows: list[dict[str, Any]] = []
    start_wall = time.perf_counter()

    if args.protocol == "http3":
        from aioquic.asyncio import connect
        parsed = urlsplit(args.base_url)
        factory = make_quic_protocol_class()
        for i in range(args.cold_count):
            t0 = time.perf_counter()
            try:
                async with connect(parsed.hostname or "localhost", parsed.port or 443,
                                   configuration=quic_configuration(args),
                                   create_protocol=factory, wait_connected=True) as protocol:
                    hs_ms = (time.perf_counter() - t0) * 1000
                    row = await h3_request(protocol, url, args, label, repetition, i + 1,
                                           expected, handshake=hs_ms)
                # Saída do `async with` = fechamento explícito da conexão QUIC.
            except Exception as exc:  # noqa: BLE001
                row = error_row(args, label, repetition, url, expected, t0, exc)
            rows.append(row)
    else:
        for i in range(args.cold_count):
            client = new_httpx_client(args, 1, pooled=False)   # cliente NOVO
            try:
                rows.append(await httpx_request(client, url, args, label, repetition, i + 1, expected))
            finally:
                await client.aclose()                           # fecha ANTES da próxima requisição
    wall = time.perf_counter() - start_wall
    return rows, summarize(rows, args.protocol, args.mode, args.scenario, label, repetition, wall)


# --------------------------------------------------------------------------- #
# Saída e CLI
# --------------------------------------------------------------------------- #
def write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    elif path.suffix.lower() == ".csv":
        fields: list[str] = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    else:
        raise ValueError("O arquivo de saída precisa terminar em .json ou .csv")


def parse_bool(value: str) -> bool:
    v = value.lower().strip()
    if v in {"true", "1", "yes", "sim"}:
        return True
    if v in {"false", "0", "no", "nao", "não"}:
        return False
    raise argparse.ArgumentTypeError("Informe true ou false")


async def main_async(args: argparse.Namespace) -> int:
    output: list[dict[str, Any]] = []
    failed = 0
    for rep in range(1, args.repetitions + 1):
        if args.mode == "cold":
            print(f"[{args.protocol}/{args.scenario}] COLD x{args.cold_count}, repetição {rep}", flush=True)
            jobs = [run_cold_profile(args, rep)]
        else:
            jobs = []
        if args.mode == "warm":
            for profile in args.profiles:
                if rep == 1 and profile == "B":
                    for w in range(args.warmup_reps):     # repetições de aquecimento: executadas e DESCARTADAS
                        print(f"[{args.protocol}/{args.scenario}] WARM Carga B, aquecimento descartado {w + 1}",
                              flush=True)
                        await run_warm_profile(args, profile, 0)
                print(f"[{args.protocol}/{args.scenario}] WARM Carga {profile}, repetição {rep}", flush=True)
                raw, summary = await run_warm_profile(args, profile, rep)
                output.extend(raw)
                output.append(summary)
                failed += summary["requests_failed"]
                print(json.dumps({k: summary[k] for k in (
                    "profile", "requests_success", "requests_failed", "rps",
                    "throughput_mbps", "ttfb_p50_ms", "total_p50_ms", "connections_total")},
                    ensure_ascii=False), flush=True)
        for job in jobs:
            raw, summary = await job
            output.extend(raw)
            output.append(summary)
            failed += summary["requests_failed"]
            print(json.dumps({k: summary[k] for k in (
                "requests_success", "requests_failed", "ttfb_p50_ms", "handshake_p50_ms")},
                ensure_ascii=False), flush=True)
    write_results(Path(args.output), output)
    print(f"Arquivo gravado: {args.output}  (falhas: {failed})")
    return 1 if (args.fail_on_error and failed) else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark Redes II: HTTP/1.1, HTTP/2 e HTTP/3")
    p.add_argument("--protocol", required=True, choices=("http1", "http2", "http3"))
    p.add_argument("--base-url", required=True)
    p.add_argument("--scenario", default="LAN")
    p.add_argument("--mode", choices=("warm", "cold"), default="warm")
    p.add_argument("--profiles", nargs="+", choices=("A", "B"), default=("A", "B"),
                   help="Perfis do modo warm: A = 1x50MB, B = 100x10KB")
    p.add_argument("--repetitions", type=int, default=5)
    p.add_argument("--warmup-reps", type=int, default=1,
                   help="Repetições de aquecimento (descartadas) antes da Carga B no modo warm")
    p.add_argument("--small-count", type=int, default=100, help="Nº de objetos da Carga B")
    p.add_argument("--concurrency", type=int, default=100,
                   help="Concorrência da Carga B (enunciado: 100 objetos concorrentes)")
    p.add_argument("--cold-count", type=int, default=30, help="Nº de requisições frias por repetição")
    p.add_argument("--cold-large", action="store_true", help="Cold com o objeto de 50 MB (padrão: 10 KB)")
    p.add_argument("--large-path", default="large.bin")
    p.add_argument("--small-path", default="small.bin")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--verify-tls", type=parse_bool, default=True)
    p.add_argument("--output", default="results.json")
    p.add_argument("--fail-on-error", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    if min(args.repetitions, args.small_count, args.concurrency, args.cold_count) < 1:
        print("repetitions, small-count, concurrency e cold-count devem ser >= 1", file=sys.stderr)
        return 2
    scheme = urlsplit(args.base_url).scheme
    if args.protocol == "http1" and scheme not in {"http", "https"}:
        print("base-url inválida", file=sys.stderr)
        return 2
    if args.protocol in {"http2", "http3"} and scheme != "https":
        print(f"{args.protocol} precisa de URL https://", file=sys.stderr)
        return 2
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("Interrompido.", file=sys.stderr)
        return 130
    except ImportError as exc:
        print(f"Dependência ausente: {exc}. Instale com: python -m pip install -r requirements.txt",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
