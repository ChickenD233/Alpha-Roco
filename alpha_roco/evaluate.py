from __future__ import annotations

import argparse
from pathlib import Path

import torch

from .arena import load_model, play_match, wilson_interval
from .features import ObservationEncoder


def _print_tally(label: str, tally: dict[str, int]) -> None:
    games = sum(tally.values())
    if games == 0:
        print(f"{label} games=0")
        return
    low, high = wilson_interval(tally["win"], games)
    print(
        f"{label} games={games} wins={tally['win']} losses={tally['loss']} "
        f"both_lose={tally['both_lose']} win_rate={tally['win'] / games:.3f} "
        f"ci95=[{low:.3f},{high:.3f}]"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="固定种子评测Alpha Roco checkpoint")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--games", "--repeats", dest="games", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    if args.games < 2:
        raise ValueError("games至少为2")
    if args.workers < 1:
        raise ValueError("workers必须为正数")
    torch.set_num_threads(1)
    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    encoder = ObservationEncoder()
    candidate = load_model(args.candidate, encoder, device)
    reference = load_model(args.reference, encoder, device)
    tallies = play_match(
        candidate,
        reference,
        encoder,
        args.games,
        args.seed,
        device,
        args.sample,
        args.timeout,
        args.workers,
    )
    _print_tally("candidate_side=A vs reference_side=B", tallies["A"])
    _print_tally("candidate_side=B vs reference_side=A", tallies["B"])


if __name__ == "__main__":
    main()
