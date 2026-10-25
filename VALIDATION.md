# Validation

Initial local validation: 2026-10-25, Windows, CPython 3.13.16.

- 18 unittest tests passed, including 60 deterministic randomized comparisons against an independent in-memory set oracle.
- Ruff static checks passed (E4/E7/E9/F/B).
- Coverage measured 97% of core.py statements. Total package coverage was 78%; subprocess CLI tests are real but are not included in that coverage process.
- Built a wheel from the source distribution; installed it without runtime dependencies into a fresh virtual environment outside the checkout. Verified the import location, both README examples, expected exit statuses, and uninstall.
- Reviewed integer boundaries, absent streams, late arrivals, sample caps, scratch cleanup and privacy of default output. Input bytes are checked unchanged by tests. No input content is executed; no network API is present in the runtime package.

The GitHub Actions workflow provides independent Windows/Linux and Python 3.10/3.13 checks. Refer to the actual Actions run for current remote status; this file does not claim a future run passed. Runtime dependencies are empty. Build tools are development-only. No third-party implementation or fixtures were copied.

Limits are intentional: this is a finite observation audit, with explicit contiguous sequence contracts, not proof of eventual or exactly-once delivery. Synthetic examples are labeled as such.
