# Contributing

Discuss focused changes in an issue with a minimal synthetic reproduction. Do not upload production logs or credentials. Install from source, run `python -m unittest discover -s tests -v`, `python -m ruff check .`, `python -m build`, and `python scripts/smoke_wheel.py` before proposing changes. Add behavioral tests for bug fixes and update documented limits. Keep runtime dependencies minimal and CLI exit codes stable. Contributions are under MIT; preserve third-party copyright if introducing external material.
