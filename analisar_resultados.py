#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

try:
    from scipy import stats as sps
except ImportError:  # IC cai para aproximação normal
    sps = None

#Matriz experimental 
MATRIX = {}
_ORDER = [
    ("A", "http1", "LAN"), ("A", "http2", "LAN"), ("A", "http3", "LAN"),
    ("A", "http1", "WAN"), ("A", "http2", "WAN"), ("A", "http3", "WAN"),
    ("B", "http1", "LAN"), ("B", "http2", "LAN"), ("B", "http3", "LAN"),
    ("B", "http1", "WAN"), ("B", "http2", "WAN"), ("B", "http3", "WAN"),
]
for n, key in enumerate(_ORDER, start=1):
    MATRIX[key] = f"T{n:02d}"

PROTO_LABEL = {"http1": "HTTP/1.1", "http2": "HTTPS (HTTP/2)", "http3": "HTTP/3 (QUIC)"}
PROTO_COLOR = {"http1": "#4C72B0", "http2": "#DD8452", "http3": "#55A868"}
PROTOS = ["http1", "http2", "http3"]
SCENS = ["LAN", "WAN"]


def ci95(values: pd.Series) -> float:
    v = values.dropna().to_numpy(dtype=float)
    n = len(v)
    if n < 2:
        return 0.0
    crit = sps.t.ppf(0.975, n - 1) if sps else 1.96
    return float(crit * v.std(ddof=1) / math.sqrt(n))


def sd(values: pd.Series) -> float:
    v = values.dropna().to_numpy(dtype=float)
    return float(v.std(ddof=1)) if len(v) > 1 else 0.0


def pct(values: pd.Series, p: float) -> float:
    v = values.dropna().to_numpy(dtype=float)
    return float(np.percentile(v, p)) if len(v) else float("nan")


def load(results_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    reqs, sums = [], []
    for path in sorted(results_dir.glob("*.json")):
        if path.name == "results.json":      # arquivo consolidado gerado por este script
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            row["source_file"] = path.name
            (reqs if row["record_type"] == "request" else sums).append(row)
    if not reqs:
        raise SystemExit(f"Nenhum resultado encontrado em {results_dir}/")
    r, s = pd.DataFrame(reqs), pd.DataFrame(sums)
    for df in (r, s):
        df["scenario"] = df["scenario"].str.upper()
    return r, s


def write_consolidated(results_dir: Path, reqs: pd.DataFrame, sums: pd.DataFrame) -> None:
    """Grava os dados brutos de TODA a bateria em results.json e results.csv (exigência do enunciado)."""
    allrows = pd.concat([reqs, sums], ignore_index=True).drop(columns=["source_file"], errors="ignore")
    allrows = allrows.sort_values("timestamp_utc").reset_index(drop=True)
    allrows.to_csv(results_dir / "results.csv", index=False)
    (results_dir / "results.json").write_text(
        allrows.where(allrows.notna(), None).to_json(orient="records", force_ascii=False, indent=2),
        encoding="utf-8")


def describe(prefix: str, values: pd.Series) -> dict:
    return {
        f"{prefix}_media": values.mean(), f"{prefix}_mediana": values.median(),
        f"{prefix}_desvio": sd(values), f"{prefix}_p50": pct(values, 50),
        f"{prefix}_p99": pct(values, 99), f"{prefix}_ic95": ci95(values),
    }


def build_table(reqs: pd.DataFrame, sums: pd.DataFrame) -> pd.DataFrame:
    rows = []
    warm_r = reqs[reqs["mode"] == "warm"]
    warm_s = sums[sums["mode"] == "warm"]
    for (prof, proto, scen), tid in MATRIX.items():
        label = f"Carga {prof}"
        rr = warm_r[(warm_r.profile == label) & (warm_r.protocol == proto) & (warm_r.scenario == scen)]
        ss = warm_s[(warm_s.profile == label) & (warm_s.protocol == proto) & (warm_s.scenario == scen)]
        row = {"ID": tid, "Carga": label, "Protocolo": PROTO_LABEL[proto], "Cenário": scen,
               "repeticoes": len(ss), "req_ok": int(rr["success"].sum()),
               "req_falhas": int((~rr["success"]).sum())}
        ok = rr[rr["success"]]
        row.update(describe("ttfb_ms", ok["ttfb_ms"]))
        row.update(describe("total_ms", ok["total_time_ms"]))
        # RPS: uma amostra por repetição
        row.update(describe("rps", ss["rps"]))
        row.update(describe("vazao_mbps", ss["throughput_mbps"]))
        rows.append(row)
    return pd.DataFrame(rows)


def build_cold(reqs: pd.DataFrame, rtt: dict[str, float]) -> pd.DataFrame:
    rows = []
    cold = reqs[(reqs["mode"] == "cold") & reqs["success"]]
    for proto in PROTOS:
        for scen in SCENS:
            c = cold[(cold.protocol == proto) & (cold.scenario == scen)]
            if c.empty:
                continue
            row = {"Protocolo": PROTO_LABEL[proto], "Cenário": scen, "amostras": len(c)}
            row.update(describe("handshake_ms", c["handshake_ms"]))
            row.update(describe("cold_request_to_body_ms", c["request_to_first_body_ms"]))
            row["handshake_em_RTTs"] = row["handshake_ms_media"] / rtt[scen]
            row["cold_request_to_body_em_RTTs"] = row["cold_request_to_body_ms_media"] / rtt[scen]
            rows.append(row)
    return pd.DataFrame(rows)


def to_markdown(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = [f"{v:.3f}" if isinstance(v, float) else str(v) for v in r]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Gráficos (todos com barras de erro)
# --------------------------------------------------------------------------- #
def grouped_bars(ax, data: dict, ylabel: str, title: str, log: bool = False) -> None:
    """data[(proto, scen)] = (média, erro)."""
    width = 0.25
    x = np.arange(len(SCENS))
    for i, proto in enumerate(PROTOS):
        means = [data.get((proto, s), (np.nan, 0))[0] for s in SCENS]
        errs = [data.get((proto, s), (np.nan, 0))[1] for s in SCENS]
        bars = ax.bar(x + (i - 1) * width, means, width, yerr=errs, capsize=4,
                      label=PROTO_LABEL[proto], color=PROTO_COLOR[proto], edgecolor="black",
                      linewidth=0.6, error_kw={"elinewidth": 1.2})
        for b, m, e in zip(bars, means, errs):
            if not np.isnan(m):
                ax.annotate(f"{m:.1f}", (b.get_x() + b.get_width() / 2, m + (e if not np.isnan(e) else 0)),
                            ha="center", va="bottom", fontsize=7, xytext=(0, 4),
                            textcoords="offset points")
    ax.set_xticks(x)
    ax.set_xticklabels(["LAN (RTT 1 ms, 0% perda)", "WAN (RTT 50 ms, 2% perda)"])
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if log:
        ax.set_yscale("log")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, fontsize=8, frameon=False)


def per_rep(sums: pd.DataFrame, profile: str, col: str) -> dict:
    out = {}
    s = sums[(sums["mode"] == "warm") & (sums.profile == profile)]
    for proto in PROTOS:
        for scen in SCENS:
            v = s[(s.protocol == proto) & (s.scenario == scen)][col].dropna()
            if len(v):
                out[(proto, scen)] = (float(v.mean()), sd(v))
    return out


def make_figures(reqs: pd.DataFrame, sums: pd.DataFrame, cold: pd.DataFrame, out: Path) -> None:
    # 1) Carga A: vazão
    fig, ax = plt.subplots(figsize=(8, 5))
    grouped_bars(ax, per_rep(sums, "Carga A", "throughput_mbps"), "Vazão (Mbit/s)",
                 "Carga A (1 × 50 MB): vazão sustentada\n(média ± desvio-padrão entre repetições)", log=True)
    fig.tight_layout(); fig.savefig(out / "fig_cargaA_vazao.png", dpi=200); plt.close(fig)

    # 2) Carga A: tempo total
    fig, ax = plt.subplots(figsize=(8, 5))
    grouped_bars(ax, per_rep(sums, "Carga A", "total_mean_ms"), "Tempo total de carregamento (ms)",
                 "Carga A (1 × 50 MB): tempo total\n(média ± desvio-padrão entre repetições)", log=True)
    fig.tight_layout(); fig.savefig(out / "fig_cargaA_tempo_total.png", dpi=200); plt.close(fig)

    # 3) Carga B: RPS
    fig, ax = plt.subplots(figsize=(8, 5))
    grouped_bars(ax, per_rep(sums, "Carga B", "rps"), "Requisições por segundo (RPS)",
                 "Carga B (100 × 10 KB): RPS\n(média ± desvio-padrão entre repetições)", log=True)
    fig.tight_layout(); fig.savefig(out / "fig_cargaB_rps.png", dpi=200); plt.close(fig)

    # 4) Carga B: p50 e p99 de TTFB e Tempo total
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    for row_i, (metric, mlabel) in enumerate([("ttfb", "TTFB"), ("total", "Tempo total")]):
        for col_i, pc in enumerate(["p50", "p99"]):
            grouped_bars(axes[row_i][col_i], per_rep(sums, "Carga B", f"{metric}_{pc}_ms"),
                         f"{mlabel} {pc} (ms)", f"Carga B: {mlabel} {pc}", log=True)
    fig.suptitle("Carga B (100 × 10 KB): percentis de latência (média ± desvio-padrão entre repetições)")
    fig.tight_layout(); fig.savefig(out / "fig_cargaB_percentis.png", dpi=200); plt.close(fig)

    # 5) Cold: handshake e Cold TTFB
    if not cold.empty:
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        for ax, (key, title) in zip(axes, [("handshake_ms", "Handshake (TCP / TCP+TLS 1.3 / QUIC)"),
                                           ("cold_request_to_body_ms", "Cold: requisição até o 1º byte do corpo\n(inclui handshake)")]):
            data = {}
            for _, r in cold.iterrows():
                proto = next(k for k, v in PROTO_LABEL.items() if v == r["Protocolo"])
                data[(proto, r["Cenário"])] = (r[f"{key}_media"], r[f"{key}_ic95"])
            grouped_bars(ax, data, "Tempo (ms)", title + "\n(média ± IC95 das amostras)")
        fig.tight_layout(); fig.savefig(out / "fig_cold_handshake_ttfb.png", dpi=200); plt.close(fig)

    # 6) Distribuição do tempo total na Carga B (boxplot) - mostra cauda/outliers
    b = reqs[(reqs["mode"] == "warm") & (reqs.profile == "Carga B") & reqs["success"]]
    if not b.empty:
        fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
        for ax, scen in zip(axes, SCENS):
            groups = [b[(b.protocol == p) & (b.scenario == scen)]["total_time_ms"].to_numpy() for p in PROTOS]
            ax.boxplot([g if len(g) else [np.nan] for g in groups],
                       tick_labels=[PROTO_LABEL[p] for p in PROTOS], showfliers=True)
            ax.set_title(f"Carga B, {scen}"); ax.set_ylabel("Tempo total por requisição (ms)")
            ax.set_yscale("log"); ax.grid(axis="y", alpha=0.3)
        fig.tight_layout(); fig.savefig(out / "fig_cargaB_boxplot.png", dpi=200); plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="analysis")
    ap.add_argument("--rtt-lan", type=float, default=1.0)
    ap.add_argument("--rtt-wan", type=float, default=50.0,
                    help="RTT de referência (ms) para converter tempos em nº de RTTs; "
                         "use o RTT medido pelo ping de validação do netem_apply.sh")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    reqs, sums = load(Path(args.results))
    write_consolidated(Path(args.results), reqs, sums)
    table = build_table(reqs, sums)
    cold = build_cold(reqs, {"LAN": args.rtt_lan, "WAN": args.rtt_wan})

    table.to_csv(out / "tabela_12_testes.csv", index=False)
    (out / "tabela_12_testes.md").write_text(to_markdown(table), encoding="utf-8")
    cold.to_csv(out / "tabela_cold_handshake.csv", index=False)
    (out / "tabela_cold_handshake.md").write_text(to_markdown(cold) if not cold.empty else "", encoding="utf-8")
    make_figures(reqs, sums, cold, out)

    print(f"Tabelas e gráficos gravados em {out}/")
    missing = table[table["repeticoes"] == 0]["ID"].tolist()
    if missing:
        print(f"ATENÇÃO: sem dados para: {', '.join(missing)}")
    failed = table[table["req_falhas"] > 0][["ID", "req_falhas"]]
    if not failed.empty:
        print("ATENÇÃO: testes com requisições falhas:\n" + failed.to_string(index=False))
    if cold.empty:
        print("ATENÇÃO: nenhum resultado cold (handshake) encontrado.")


if __name__ == "__main__":
    main()