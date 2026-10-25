"""Inspect identities, not log payloads. Sequence numbers are signed-SQLite-safe."""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from contextlib import closing
from decimal import Decimal
from pathlib import Path

MAX_SEQUENCE = (1 << 63) - 1


class InputError(ValueError):
    """Malformed, unsupported, or over-budget input; no complete report exists."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON member")
        result[key] = value
    return result


def _constant(_value):
    raise InputError("non-finite JSON constant")


def load_json(data):
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=Decimal,
            parse_constant=_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InputError("invalid UTF-8 or strict JSON") from exc


def pointer_tokens(pointer):
    if pointer == "":
        return []
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise InputError("selectors must be RFC 6901 JSON Pointers")
    return [s.replace("~1", "/").replace("~0", "~") for s in pointer[1:].split("/")]


def select(obj, tokens):
    for token in tokens:
        if isinstance(obj, dict) and token in obj:
            obj = obj[token]
        elif isinstance(obj, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            if len(token) > 9 or int(token) >= len(obj):
                raise InputError("array selector out of range")
            obj = obj[int(token)]
        else:
            raise InputError("selector missing or wrong container type")
    return obj


def sequence(value):
    if type(value) is not int or not 0 <= value <= MAX_SEQUENCE:
        raise InputError("sequence bounds must be JSON integers in 0..2^63-1")
    return value


def stream_key(values):
    if any(type(v) not in (str, int) for v in values):
        raise InputError("stream components must be JSON strings or integers")
    for value in values:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise InputError("stream contains a lone Unicode surrogate") from exc
    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


def expected_windows(path, arity, max_bytes, max_streams):
    if path is None:
        return None
    with Path(path).open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise InputError("expected window file exceeds byte budget")
    items = load_json(raw)
    if not isinstance(items, list) or len(items) > max_streams:
        raise InputError("expected windows must be a bounded JSON array")
    result = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"stream", "first", "last"}:
            raise InputError("window needs exactly stream, first, last")
        if not isinstance(item["stream"], list) or len(item["stream"]) != arity:
            raise InputError("window stream length must match --stream selectors")
        key = stream_key(item["stream"])
        first, last = sequence(item["first"]), sequence(item["last"])
        if first > last or key in result:
            raise InputError("reversed or duplicate expected window")
        result[key] = (first, last)
    return result


def audit(
    paths,
    *,
    stream_pointers=("/stream",),
    sequence_pointer="/seq",
    expected=None,
    temp_dir=None,
    show_streams=False,
    samples=20,
    max_line_bytes=1048576,
    max_bytes=1073741824,
    max_records=2000000,
    max_streams=10000,
):
    """Audit files in argument/line order; each file is a complete JSONL capture.

    Missing ranges are computed only after all arrivals, so late arrivals fill gaps.
    Duplicate identities are counted regardless of whether their payloads differ.
    No input is changed. Disk use is O(unique identities); reports are bounded by
    max_streams and samples. A fresh private temporary directory is always removed.
    """
    for limit in (samples, max_line_bytes, max_bytes, max_records, max_streams):
        if type(limit) is not int or limit < 1:
            raise InputError("all limits must be positive integers")
    if not paths or not stream_pointers:
        raise InputError("at least one input and stream selector are required")
    streams = [pointer_tokens(p) for p in stream_pointers]
    seq_tokens = pointer_tokens(sequence_pointer)
    windows = expected_windows(expected, len(streams), max_line_bytes, max_streams)
    with tempfile.TemporaryDirectory(prefix="seqlog-", dir=temp_dir) as directory:
        with closing(sqlite3.connect(str(Path(directory) / "index.sqlite3"))) as db:
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            db.execute(
                "CREATE TABLE streams (id INTEGER PRIMARY KEY, key TEXT UNIQUE, "
                "file INTEGER, line INTEGER, n INTEGER DEFAULT 0, "
                "u INTEGER DEFAULT 0, d INTEGER DEFAULT 0, r INTEGER DEFAULT 0, "
                "hi INTEGER)"
            )
            db.execute(
                "CREATE TABLE seen (stream INTEGER, seq INTEGER, file INTEGER, "
                "line INTEGER, PRIMARY KEY(stream, seq)) WITHOUT ROWID"
            )
            if windows is not None:
                for key in windows:
                    db.execute("INSERT INTO streams(key) VALUES (?)", (key,))
            stream_count = len(windows or {})
            byte_count = 0
            record_count = 0
            duplicate_examples, regression_examples = [], []
            for file_no, path in enumerate(paths, 1):
                with Path(path).open("rb") as handle:
                    line_no = 0
                    while raw := handle.readline(max_line_bytes + 1):
                        line_no += 1
                        byte_count += len(raw)
                        record_count += 1
                        if len(raw) > max_line_bytes:
                            raise InputError(
                                f"input {file_no} line {line_no}: line byte budget exceeded"
                            )
                        if byte_count > max_bytes or record_count > max_records:
                            raise InputError("capture byte or record budget exceeded")
                        try:
                            obj = load_json(raw)
                            seq = sequence(select(obj, seq_tokens))
                            key = stream_key([select(obj, p) for p in streams])
                        except InputError as exc:
                            raise InputError(f"input {file_no} line {line_no}: {exc}") from exc
                        row = db.execute("SELECT id,hi FROM streams WHERE key=?", (key,)).fetchone()
                        if row is None:
                            if stream_count >= max_streams:
                                raise InputError("stream count budget exceeded")
                            stream_count += 1
                            sid = db.execute(
                                "INSERT INTO streams(key,file,line) VALUES (?,?,?)",
                                (key, file_no, line_no),
                            ).lastrowid
                            high = None
                        else:
                            sid, high = row
                        first = db.execute(
                            "SELECT file,line FROM seen WHERE stream=? AND seq=?", (sid, seq)
                        ).fetchone()
                        duplicate = first is not None
                        regression = not duplicate and high is not None and seq < high
                        if duplicate:
                            if len(duplicate_examples) < samples:
                                duplicate_examples.append(
                                    {
                                        "stream": sid,
                                        "sequence": seq,
                                        "first": {"input": first[0], "line": first[1]},
                                        "again": {"input": file_no, "line": line_no},
                                    }
                                )
                        else:
                            db.execute(
                                "INSERT INTO seen VALUES (?,?,?,?)", (sid, seq, file_no, line_no)
                            )
                        if regression and len(regression_examples) < samples:
                            regression_examples.append(
                                {
                                    "stream": sid,
                                    "sequence": seq,
                                    "previous_high": high,
                                    "input": file_no,
                                    "line": line_no,
                                }
                            )
                        new_high = seq if high is None else max(high, seq)
                        db.execute(
                            "UPDATE streams SET n=n+1,u=u+?,d=d+?,r=r+?,hi=?, "
                            "file=coalesce(file,?),line=coalesce(line,?) WHERE id=?",
                            (
                                int(not duplicate),
                                int(duplicate),
                                int(regression),
                                new_high,
                                file_no,
                                line_no,
                                sid,
                            ),
                        )
                        if record_count % 10000 == 0:
                            db.commit()
            db.commit()
            if record_count == 0 and windows is None:
                raise InputError("no records; provide expected windows to check an empty capture")
            report_streams = []
            totals = {
                "records": record_count,
                "unique": 0,
                "duplicates": 0,
                "arrival_regressions": 0,
                "missing": 0,
                "outside_window": 0,
                "unexpected_streams": 0,
            }
            for sid, key, source, line, n, u, d, r, high in db.execute(
                "SELECT * FROM streams ORDER BY id"
            ):
                low = db.execute("SELECT min(seq) FROM seen WHERE stream=?", (sid,)).fetchone()[0]
                window = windows.get(key) if windows is not None else None
                start, end = window if window is not None else (low, high)
                missing = gap_count = outside = 0
                gaps = []
                cursor = start
                for (seq,) in db.execute(
                    "SELECT seq FROM seen WHERE stream=? ORDER BY seq", (sid,)
                ):
                    if window is not None and (seq < start or seq > end):
                        outside += 1
                        continue
                    if seq > cursor:
                        missing += seq - cursor
                        gap_count += 1
                        if len(gaps) < samples:
                            gaps.append({"first": cursor, "last": seq - 1})
                    cursor = seq + 1
                if cursor is not None and cursor <= end:
                    missing += end - cursor + 1
                    gap_count += 1
                    if len(gaps) < samples:
                        gaps.append({"first": cursor, "last": end})
                unexpected = windows is not None and window is None
                entry = {
                    "stream": sid,
                    "first_seen": None if source is None else {"input": source, "line": line},
                    "records": n,
                    "unique": u,
                    "duplicates": d,
                    "arrival_regressions": r,
                    "observed_first": low,
                    "observed_last": high,
                    "window": {
                        "first": start,
                        "last": end,
                        "basis": "expected" if window is not None else "observed",
                    },
                    "unexpected_stream": unexpected,
                    "outside_window": outside,
                    "missing": missing,
                    "gap_count": gap_count,
                    "gap_examples": gaps,
                }
                if show_streams:
                    entry["components"] = json.loads(key)
                report_streams.append(entry)
                for name, value in (
                    ("unique", u),
                    ("duplicates", d),
                    ("arrival_regressions", r),
                    ("missing", missing),
                    ("outside_window", outside),
                    ("unexpected_streams", int(unexpected)),
                ):
                    totals[name] += value
            findings = any(
                totals[k]
                for k in (
                    "duplicates",
                    "arrival_regressions",
                    "missing",
                    "outside_window",
                    "unexpected_streams",
                )
            )
            return {
                "schema_version": 1,
                "complete": True,
                "findings": findings,
                "input_count": len(paths),
                "bytes_read": byte_count,
                "window_mode": "expected" if windows is not None else "observed_only",
                "totals": totals,
                "streams": report_streams,
                "duplicate_examples": duplicate_examples,
                "regression_examples": regression_examples,
                "example_limit": samples,
            }
