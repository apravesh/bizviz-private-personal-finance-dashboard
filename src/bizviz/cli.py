"""The `bizviz` command line."""

import argparse
from collections.abc import Sequence

from bizviz import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bizviz")
    parser.add_argument("--env", choices=["production", "sandbox"], default="production")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the version")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
    return 0
