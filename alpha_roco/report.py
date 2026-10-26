from __future__ import annotations

import argparse
import json
from pathlib import Path


def write_table(input_path: Path, output_path: Path | None = None) -> str:
    rows = [
        json.loads(line)
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    lines = [
        "| 批次 | 对局数 | 胜率 | 对比的best批次 |",
        "|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['batch']} | {row['games']} | "
            f"{row['win_rate']:.3f} | {row['reference_batch']} |"
        )
    table = "\n".join(lines) + "\n"
    if output_path is not None:
        output_path.write_text(table, encoding="utf-8")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="生成逐批对best胜率表")
    parser.add_argument("--input", type=Path, default=Path("logs/az/eval.jsonl"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(write_table(args.input, args.output), end="")


if __name__ == "__main__":
    main()
