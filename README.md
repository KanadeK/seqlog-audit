# seqlog-audit

Audit delivery of explicitly sequenced JSONL records without uploading logs. Find missing sequence ranges, repeated identities and out-of-order first arrivals, with file/line witnesses. SQLite holds the identity index on disk; log payloads are neither indexed nor printed.

This helps qualify a log shipper after retry, buffer or rotation changes. Aggregate processed counters can look healthy while individual records are missing. Unlike a generic log viewer or a custom SQL query, this tool supplies an expected-window contract and compact, reproducible evidence.

## Install and run

Python 3.10 or newer; no runtime dependencies. Source installation (not a PyPI release):

```sh
git clone https://github.com/KanadeK/seqlog-audit.git
cd seqlog-audit
python -m venv .venv
# Linux/macOS: . .venv/bin/activate
# PowerShell: .venv/Scripts/Activate.ps1
python -m pip install .
seqlog-audit examples/clean.jsonl
seqlog-audit examples/delivered.jsonl --expected examples/expected.json
```

The first command exits **0**. The second exits **1**, deliberately: 5 observations, 4 unique identities, 1 duplicate, 1 arrival regression and 2 missing positions (2 and 5). The examples are synthetic and executable.

`python -m seqlog_audit` is equivalent. Uninstall with `python -m pip uninstall seqlog-audit`; remove the dedicated virtual environment if no longer needed. Scratch directories are removed on normal completion and handled errors. An abrupt OS kill can leave a `seqlog-*` directory in your temp location; remove only the directory from that completed run.

## Input contract

Each nonblank UTF-8 JSONL line must contain a nonnegative integer sequence in `0..2^63-1` and stream components that are JSON strings or integers. Booleans, decimal/exponential sequences, duplicate JSON members, non-finite constants, missing selectors and a BOM are rejected. LF and CRLF are accepted; the last JSON record may lack LF.

Defaults are `/stream` and `/seq`. Repeat `--stream` for composite keys and include a boot/generation field when producers reset their counters:

```sh
seqlog-audit part1.jsonl part2.jsonl --stream /host --stream /boot --sequence /delivery/number
```

Selectors are RFC 6901 JSON Pointers (`~1` is `/`, `~0` is `~`). Files are read in argument order, then physical line order. No timestamp sorting occurs. A duplicate means the **same stream/sequence identity**, even if the payload differs. A regression means a previously unseen sequence below that stream's prior high-water mark; late duplicate deliveries are not counted a second time as regressions.

Without `--expected`, gaps are counted only between the minimum and maximum observed sequence of each stream. An empty capture is rejected. This mode cannot detect missing leading/trailing records or wholly missing streams, and a clean result does not prove complete delivery.

An expected file is an array with exactly these fields per stream:

```json
[{"stream":["api","boot-2"],"first":0,"last":999}]
```

The components must match the `--stream` order and types. Bounds are inclusive. With this file the audit catches missing edges, wholly absent streams, unique identities outside their window, and unexpected streams. It supports one contiguous expected interval per stream; intentional counter gaps require separate scope or a different tool.

## Reports and limits

JSON is written to stdout. Input numbers are one-based argument positions; line numbers are one-based. No input paths or raw stream values are included by default. `--show-streams` includes component values. Sequence values, counts and positions are always visible; this is data minimization, not anonymization.

Exit **0** = complete audit without findings; **1** = complete audit with delivery findings; **2** = invalid input, budget or storage failure, with no partial success report. The tool does not write to inputs. Redirect output to a different file if saving a report.

Defaults: `--max-line-bytes 1048576` (including terminator), `--max-bytes 1073741824` across captures, `--max-records 2000000`, `--max-streams 10000`, `--samples 20` examples per gap list and per global witness category. Limits can be raised explicitly. Counts remain exact when examples are capped. Expected JSON is bounded by the line-byte limit. `--temp-dir` selects the parent for a private scratch directory; allow disk space for O(unique identities). Runtime is dominated by indexed inserts and an ordered scan. Output memory is bounded by stream and example limits.

## Development

```sh
python -m unittest discover -s tests -v
python -m pip install ruff build
ruff check .
python -m build
```

Tests include a seeded independent set oracle, huge integer gaps, late arrival gap filling, expected boundaries/absent streams, composite selectors, malformed input, budgets, scratch cleanup, unchanged input bytes and CLI exit codes. CI runs on Windows and Linux with Python 3.10 and 3.13 and exercises a wheel outside the checkout. See [VALIDATION.md](VALIDATION.md).

## Limits and alternatives

Trustworthy contiguous producer sequences are required. Never combine process generations under one stream key. Sampling or intentional omission creates gaps that are not transport loss. A finite capture does not prove eventual delivery; wait for collection to finish. Logs changing while read are not snapshotted. There is no automatic repair, deduplication, live tailing or payload equality analysis.

[Fluent Bit's retry discussion](https://github.com/fluent/fluent-bit/issues/6369) motivates this workflow. [lnav](https://github.com/tstack/lnav) is a better interactive log explorer; SQL/jq are more general. This tool is a small offline qualification check, not a logging platform.

## 中文说明

离线核对 JSONL 日志中的“流标识 + 整数序号”，输出缺失区间、重复身份和首次到达的倒序记录。支持多个字段组成流身份，可加入重启代次；SQLite 临时索引避免把全部日志装入内存。默认不输出消息正文或流名称。提供期望区间文件后才能检查首尾缺失及整条流缺失。缺口不自动代表传输丢失，仍须排除采样、主动过滤和采集未完成。退出码 0/1/2 分别表示无发现、有发现、无法完成审计。
