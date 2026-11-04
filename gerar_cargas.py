#!/usr/bin/env python3
"""Cria as cargas exatas do enunciado; mantém um arquivo se já tiver o tamanho certo."""
from pathlib import Path
import os

DATA_DIR = Path(__file__).resolve().parent / "data"
TARGETS = (("large.bin", 50_000_000), ("small.bin", 10_000))


def ensure_file(name: str, size: int) -> None:
    path = DATA_DIR / name
    if path.exists() and path.stat().st_size == size:
        print(f"{path.name}: já está correto ({size:,} bytes).")
        return
    print(f"Gerando {path.name} ({size:,} bytes)...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(os.urandom(size))
    if path.stat().st_size != size:
        raise OSError(f"Tamanho incorreto após gerar {path}: {path.stat().st_size}")


if __name__ == "__main__":
    for filename, byte_count in TARGETS:
        ensure_file(filename, byte_count)
    print("Cargas prontas em data/.")
