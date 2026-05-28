"""Trim tuning harness CLI.

python -m yoto_lib.tools.trim_tune sweep --corpus <dir> [--top N]
python -m yoto_lib.tools.trim_tune label --file <path> ...
python -m yoto_lib.tools.trim_tune diagnose <url> ...   # thin wrapper over Phase 0
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from yoto_lib.tools.trim_tune.analyzer import CachingAnalyzer
from yoto_lib.tools.trim_tune.corpus import load_corpus
from yoto_lib.tools.trim_tune.labeler import audition, upsert_label
from yoto_lib.tools.trim_tune.report import print_sweep
from yoto_lib.tools.trim_tune.scorer import predict_all, score_all
from yoto_lib.track_sources._trim import DirectAnalyzer, SilenceStrategy

_console = Console()

DEFAULT_TOLERANCES = [0.05, 0.10, 0.15, 0.20, 0.30]


def _cmd_sweep(args: argparse.Namespace) -> None:
    fixtures = load_corpus(Path(args.corpus))
    usable = [f for f in fixtures if f.audio_path is not None and f.hash_ok]
    if not usable:
        _console.print(f"No usable fixtures in {args.corpus} (audio absent or hash mismatch). Nothing to sweep.")
        return
    strategy = SilenceStrategy()
    analyzer = CachingAnalyzer()
    predictions, params_by_key = predict_all(strategy, fixtures, analyzer)
    configs = score_all(predictions, params_by_key, [f.label for f in fixtures])
    print_sweep(configs, DEFAULT_TOLERANCES, top_n=args.top, label_noise=args.label_noise)


def _cmd_label(args: argparse.Namespace) -> None:
    audio_path = Path(args.file)
    corpus_dir, filename = audio_path.parent, audio_path.name
    manual_fields = (args.category, args.start_max, args.end_min, args.start_min, args.end_max)
    if args.clean:
        if any(v is not None for v in manual_fields):
            _console.print(
                "[red]--clean conflicts with --category / --start-max / --end-min / --start-min / --end-max[/red]"
            )
            raise SystemExit(2)
        duration = DirectAnalyzer().duration(audio_path)
        category, start_max, end_min, start_min, end_max = "clean", 0.0, duration, 0.0, duration
    else:
        missing = [
            name
            for name, val in (
                ("--category", args.category),
                ("--start-max", args.start_max),
                ("--end-min", args.end_min),
            )
            if val is None
        ]
        if missing:
            _console.print(
                f"[red]Missing required: {', '.join(missing)} "
                "(or use --clean as a shortcut for full-track labels)[/red]"
            )
            raise SystemExit(2)
        category, start_max, end_min = args.category, args.start_max, args.end_min
        start_min, end_max = args.start_min, args.end_max
    upsert_label(
        corpus_dir,
        filename=filename,
        source_url=args.url,
        category=category,
        true_start_max=start_max,
        true_end_min=end_min,
        true_start_min=start_min,
        true_end_max=end_max,
    )


def _cmd_audition(args: argparse.Namespace) -> None:
    file = Path(args.file)
    at = max(0.0, DirectAnalyzer().duration(file) - args.at_end) if args.at_end is not None else args.at
    audition(file, at, args.window)


def _cmd_diagnose(args: argparse.Namespace) -> None:
    from validation.trim_diagnose import main as diagnose_main  # ty: ignore[unresolved-import]

    diagnose_main(args.urls, save_to=args.save_to)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trim_tune", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    sweep = sub.add_parser("sweep", help="sweep configs over the corpus and report")
    sweep.add_argument("--corpus", required=True)
    sweep.add_argument("--top", type=int, default=10)
    sweep.add_argument("--label-noise", type=float, default=0.05)
    sweep.set_defaults(func=_cmd_sweep)

    label = sub.add_parser("label", help="add/replace a corpus label")
    label.add_argument(
        "--file",
        required=True,
        help="path to the audio file; its parent dir is treated as the corpus (holds labels.json)",
    )
    label.add_argument("--url", required=True)
    label.add_argument(
        "--clean",
        action="store_true",
        help=(
            "Shortcut: label as 'clean' (whole track should be kept; don't trim). "
            "Auto-fills category, start-max, end-min from the file's duration."
        ),
    )
    label.add_argument(
        "--category",
        choices=["threshold", "wrong_gap", "no_gap", "clean"],
        default=None,
        help="(required unless --clean)",
    )
    label.add_argument(
        "--start-max",
        type=float,
        default=None,
        dest="start_max",
        help="(required unless --clean) latest acceptable trim-start, in seconds",
    )
    label.add_argument(
        "--end-min",
        type=float,
        default=None,
        dest="end_min",
        help="(required unless --clean) earliest acceptable trim-end, in seconds",
    )
    label.add_argument("--start-min", type=float, default=None, dest="start_min")
    label.add_argument("--end-max", type=float, default=None, dest="end_max")
    label.set_defaults(func=_cmd_label)

    diag = sub.add_parser("diagnose", help="Phase 0 diagnostic over URLs")
    diag.add_argument("urls", nargs="+")
    diag_save = diag.add_mutually_exclusive_group()
    diag_save.add_argument(
        "--save",
        dest="save_to",
        action="store_const",
        const="tests/fixtures/trim_corpus",
        help="Save downloaded audio to tests/fixtures/trim_corpus (the conventional labeling corpus dir).",
    )
    diag_save.add_argument(
        "--save-to",
        dest="save_to",
        metavar="DIR",
        help="Save downloaded audio to DIR (explicit path).",
    )
    diag.set_defaults(func=_cmd_diagnose, save_to=None)

    aud = sub.add_parser("audition", help="play a window around a boundary via ffplay")
    aud.add_argument("--file", required=True)
    aud_at = aud.add_mutually_exclusive_group(required=True)
    aud_at.add_argument(
        "--at",
        type=float,
        help="start time in seconds from the beginning of the file",
    )
    aud_at.add_argument(
        "--at-end",
        type=float,
        dest="at_end",
        help=(
            "start time in seconds before the END of the file (probes duration; useful when you don't know the length)"
        ),
    )
    aud.add_argument("--window", type=float, default=4.0)
    aud.set_defaults(func=_cmd_audition, at=None, at_end=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
