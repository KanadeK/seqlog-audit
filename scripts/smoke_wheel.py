"""Install a built wheel into a fresh venv and run real examples away from source."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
NAME = ROOT.name
MODULE = NAME.replace("-", "_")


def main():
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("build exactly one wheel first")
    with tempfile.TemporaryDirectory(prefix="wheel-smoke-") as directory:
        work = Path(directory)
        venv.EnvBuilder(with_pip=True).create(work / "env")
        python = work / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[0])],
            check=True,
            cwd=work,
            timeout=120,
        )
        origin = subprocess.check_output(
            [str(python), "-I", "-c", f"import {MODULE}; print({MODULE}.__file__)"],
            cwd=work,
            text=True,
        )
        if str(work).lower() not in origin.lower():
            raise RuntimeError("import did not come from clean environment")
        cli = work / "env" / (f"Scripts/{NAME}.exe" if os.name == "nt" else f"bin/{NAME}")

        def run(args, code):
            result = subprocess.run(
                [str(cli), *map(str, args)], cwd=work, capture_output=True, text=True, timeout=30
            )
            if result.returncode != code:
                raise AssertionError((result.returncode, result.stdout, result.stderr))
            return json.loads(result.stdout)

        if NAME == "seqlog-audit":
            run([ROOT / "examples/clean.jsonl"], 0)
            data = run(
                [ROOT / "examples/delivered.jsonl", "--expected", ROOT / "examples/expected.json"],
                1,
            )
            assert data["totals"]["missing"] == 2
        elif NAME == "sse-wire-audit":
            data = run([ROOT / "examples/clean.sse"], 0)
            assert data["event_count"] == 2
            data = run([ROOT / "examples/incomplete.sse"], 1)
            assert data["event_count"] == 1
        elif NAME == "fault-streak":
            data = run(
                [ROOT / "examples/first.txt", ROOT / "examples/second.txt", "--fail-on-streak"], 1
            )
            assert data["streak_count"] == 1
        else:
            raise AssertionError(NAME)
        subprocess.run(
            [str(python), "-m", "pip", "uninstall", "-y", NAME], check=True, cwd=work, timeout=60
        )
        print("Fresh wheel install, import origin, CLI workflow and uninstall verified.")


if __name__ == "__main__":
    main()
