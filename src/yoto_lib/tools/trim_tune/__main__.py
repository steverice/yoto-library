"""Trim tuning harness CLI.

python -m yoto_lib.tools.trim_tune sweep --corpus <dir> [--top N]
python -m yoto_lib.tools.trim_tune label --corpus <dir> --file <name> ...
python -m yoto_lib.tools.trim_tune diagnose <url> ...   # thin wrapper over Phase 0
"""

from __future__ import annotations

import argparse
from pathlib import Path

from yoto_lib.tools.trim_tune.analyzer import CachingAnalyzer
from yoto_lib.tools.trim_tune.corpus import load_corpus
from yoto_lib.tools.trim_tune.labeler import upsert_label
from yoto_lib.tools.trim_tune.report import print_sweep
from yoto_lib.tools.trim_tune.scorer import predict_all, score_all
from yoto_lib.track_sources._trim import SilenceStrategy

DEFAULT_TOLERANCES = [0.05, 0.10, 0.15, 0.20, 0.30]


def _cmd_sweep(args: argparse.Namespace) -> None:
    fixtures = load_corpus(Path(args.corpus))
    strategy = SilenceStrategy()
    analyzer = CachingAnalyzer()
    predictions, params_by_key = predict_all(strategy, fixtures, analyzer)
    configs = score_all(predictions, params_by_key, [f.label for f in fixtures])
    print_sweep(configs, DEFAULT_TOLERANCES, top_n=args.top, label_noise=args.label_noise)


def _cmd_label(args: argparse.Namespace) -> None:
    upsert_label(
        Path(args.corpus),
        filename=args.file,
        source_url=args.url,
        category=args.category,
        true_start_max=args.start_max,
        true_end_min=args.end_min,
        true_start_min=args.start_min,
        true_end_max=args.end_max,
    )


def _cmd_diagnose(args: argparse.Namespace) -> None:
    from validation.trim_diagnose import main as diagnose_main  # ty: ignore[unresolved-import]

    diagnose_main(args.urls)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trim_tune", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    sweep = sub.add_parser("sweep", help="sweep configs over the corpus and report")
    sweep.add_argument("--corpus", required=True)
    sweep.add_argument("--top", type=int, default=10)
    sweep.add_argument("--label-noise", type=float, default=0.05)
    sweep.set_defaults(func=_cmd_sweep)

    label = sub.add_parser("label", help="add/replace a corpus label")
    label.add_argument("--corpus", required=True)
    label.add_argument("--file", required=True)
    label.add_argument("--url", required=True)
    label.add_argument("--category", required=True, choices=["threshold", "wrong_gap", "no_gap", "clean"])
    label.add_argument("--start-max", type=float, required=True, dest="start_max")
    label.add_argument("--end-min", type=float, required=True, dest="end_min")
    label.add_argument("--start-min", type=float, default=None, dest="start_min")
    label.add_argument("--end-max", type=float, default=None, dest="end_max")
    label.set_defaults(func=_cmd_label)

    diag = sub.add_parser("diagnose", help="Phase 0 diagnostic over URLs")
    diag.add_argument("urls", nargs="+")
    diag.set_defaults(func=_cmd_diagnose)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
