"""CLI; JSON goes to stdout, sanitized errors to stderr."""

import argparse
import json
import sqlite3
import sys

from . import __version__
from .core import InputError, audit


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit per-stream integer sequence delivery in local JSONL."
    )
    parser.add_argument("files", nargs="+", help="capture files, in arrival order")
    parser.add_argument(
        "--stream",
        action="append",
        help="JSON Pointer; repeat for composite identity (default /stream)",
    )
    parser.add_argument("--sequence", default="/seq", help="JSON Pointer (default /seq)")
    parser.add_argument(
        "--expected", help="JSON array of {stream: [components], first: integer, last: integer}"
    )
    parser.add_argument("--temp-dir", help="parent of private scratch SQLite directory")
    parser.add_argument(
        "--show-streams", action="store_true", help="include stream component values in report"
    )
    parser.add_argument("--samples", type=positive, default=20)
    parser.add_argument("--max-line-bytes", type=positive, default=1048576)
    parser.add_argument("--max-bytes", type=positive, default=1073741824)
    parser.add_argument("--max-records", type=positive, default=2000000)
    parser.add_argument("--max-streams", type=positive, default=10000)
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        result = audit(
            args.files,
            stream_pointers=args.stream or ["/stream"],
            sequence_pointer=args.sequence,
            expected=args.expected,
            temp_dir=args.temp_dir,
            show_streams=args.show_streams,
            samples=args.samples,
            max_line_bytes=args.max_line_bytes,
            max_bytes=args.max_bytes,
            max_records=args.max_records,
            max_streams=args.max_streams,
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return int(result["findings"])
    except InputError as exc:
        print(json.dumps({"error": str(exc), "complete": False}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(
            '{"error": "input or scratch storage unavailable", "complete": false}', file=sys.stderr
        )
        return 2
    except BrokenPipeError:
        return 2
