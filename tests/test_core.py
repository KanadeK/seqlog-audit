import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from seqlog_audit.core import InputError, MAX_SEQUENCE, audit


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def capture(self, records, name="events.jsonl"):
        path = self.root / name
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def seqs(self, values, **kwargs):
        path = self.capture([{"stream": "a", "seq": n} for n in values])
        return audit([path], **kwargs)

    def windows(self, items):
        path = self.root / "expected.json"
        path.write_text(json.dumps(items), encoding="utf-8")
        return path

    def test_late_arrival_fills_gap(self):
        result = self.seqs([0, 3, 1, 1, 4])
        self.assertEqual(result["totals"]["missing"], 1)
        self.assertEqual(result["totals"]["duplicates"], 1)
        self.assertEqual(result["totals"]["arrival_regressions"], 1)
        self.assertEqual(result["streams"][0]["gap_examples"], [{"first": 2, "last": 2}])
        self.assertEqual(result["duplicate_examples"][0]["first"]["line"], 3)

    def test_ordered_clean(self):
        self.assertFalse(self.seqs([17, 18, 19])["findings"])

    def test_observed_window_does_not_infer_zero_start(self):
        self.assertEqual(self.seqs([100, 101])["totals"]["missing"], 0)

    def test_expected_edges_and_absent_stream(self):
        config = self.windows(
            [
                {"stream": ["a"], "first": 0, "last": 5},
                {"stream": ["absent"], "first": 8, "last": 10},
            ]
        )
        result = self.seqs([2, 4], expected=config)
        self.assertEqual(result["totals"]["missing"], 7)
        self.assertIsNone(result["streams"][1]["first_seen"])

    def test_outside_and_unexpected(self):
        config = self.windows([{"stream": ["a"], "first": 2, "last": 3}])
        path = self.capture(
            [{"stream": "a", "seq": n} for n in [0, 2, 3, 5]] + [{"stream": "b", "seq": 2}]
        )
        result = audit([path], expected=config)
        self.assertEqual(result["totals"]["outside_window"], 2)
        self.assertEqual(result["totals"]["unexpected_streams"], 1)
        self.assertEqual(result["totals"]["missing"], 0)

    def test_huge_gap_constant_space(self):
        result = self.seqs([0, MAX_SEQUENCE])
        self.assertEqual(result["totals"]["missing"], MAX_SEQUENCE - 1)
        self.assertEqual(result["streams"][0]["gap_count"], 1)

    def test_composite_keys_and_pointer_escaping(self):
        path = self.capture(
            [{"a/b": ["x"], "~key": gen, "n": n} for gen in ["one", "two"] for n in [0, 1]]
        )
        result = audit([path], stream_pointers=["/a~1b/0", "/~0key"], sequence_pointer="/n")
        self.assertEqual(len(result["streams"]), 2)
        self.assertFalse(result["findings"])

    def test_numeric_and_string_streams_distinct(self):
        path = self.capture([{"stream": 1, "seq": 0}, {"stream": "1", "seq": 0}])
        result = audit([path], show_streams=True)
        self.assertEqual([s["components"] for s in result["streams"]], [[1], ["1"]])

    def test_payloads_and_streams_omitted(self):
        path = self.capture([{"stream": "private-stream", "seq": 1, "body": "private-payload"}])
        self.assertNotIn("private-", json.dumps(audit([path])))

    def test_argument_order_and_witnesses(self):
        first = self.capture([{"stream": "a", "seq": 8}], "first.jsonl")
        second = self.capture(
            [{"stream": "a", "seq": 7}, {"stream": "a", "seq": 8}], "second.jsonl"
        )
        result = audit([first, second])
        self.assertEqual(result["duplicate_examples"][0]["again"], {"input": 2, "line": 2})
        self.assertEqual(result["totals"]["arrival_regressions"], 1)

    def test_invalid_json_and_selectors(self):
        path = self.root / "bad.jsonl"
        for raw in [
            b"\n",
            b'{"stream":"x","seq":true}',
            b'{"stream":"x","seq":1.0}',
            b'{"stream":"x","seq":-1}',
            b'{"stream":"x","seq":NaN}',
            b'{"stream":"x","seq":1,"seq":2}',
            b"\xef\xbb\xbf{}",
            b"\xff",
            b'{"stream":null,"seq":1}',
            b'{"seq":1}',
            b'{"stream":"\\ud800","seq":1}',
            b'{"stream":"x","seq":9223372036854775808}',
        ]:
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaises(InputError):
                    audit([path])
        good = self.capture([{"stream": "x", "seq": 1}])
        for pointer in ["seq", "/~3", "/stream/01", "/missing"]:
            with self.assertRaises(InputError):
                audit([good], sequence_pointer=pointer)

    def test_budgets_fail_without_partial_report(self):
        path = self.capture([{"stream": "a", "seq": n} for n in range(3)])
        for limits in [{"max_line_bytes": 2}, {"max_bytes": 2}, {"max_records": 2}, {"samples": 0}]:
            with self.assertRaises(InputError):
                audit([path], **limits)
        path = self.capture([{"stream": str(n), "seq": 0} for n in range(2)])
        with self.assertRaises(InputError):
            audit([path], max_streams=1)

    def test_bad_windows(self):
        for data in [
            [],
            [{"stream": ["a"], "first": 3, "last": 1}],
            [{"stream": [], "first": 0, "last": 1}],
            [{"stream": ["a"], "first": True, "last": 1}],
            [{"stream": ["a"], "first": 0, "last": 1, "extra": 1}],
        ]:
            path = self.windows(data)
            if data:
                with self.assertRaises(InputError):
                    self.seqs([0], expected=path)
            else:
                self.assertEqual(self.seqs([0], expected=path)["totals"]["unexpected_streams"], 1)
        duplicate = {"stream": ["a"], "first": 0, "last": 1}
        with self.assertRaises(InputError):
            self.seqs([0], expected=self.windows([duplicate, duplicate]))

    def test_empty_capture_with_contract(self):
        path = self.capture([])
        with self.assertRaises(InputError):
            audit([path])
        expected = self.windows([{"stream": ["a"], "first": 0, "last": 2}])
        self.assertEqual(audit([path], expected=expected)["totals"]["missing"], 3)

    def test_examples_capped_totals_exact(self):
        result = self.seqs([0, 2, 4, 6, 0, 0, 0], samples=1)
        self.assertEqual(result["totals"]["missing"], 3)
        self.assertEqual(result["totals"]["duplicates"], 3)
        self.assertEqual(len(result["duplicate_examples"]), 1)
        self.assertEqual(result["streams"][0]["gap_count"], 3)
        self.assertEqual(len(result["streams"][0]["gap_examples"]), 1)

    def test_inputs_unchanged_scratch_removed(self):
        path = self.capture([{"stream": "a", "seq": 2}])
        original = path.read_bytes()
        audit([path], temp_dir=self.root)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.glob("seqlog-*")), [])
        with self.assertRaises(InputError):
            audit([path], temp_dir=self.root, max_bytes=1)
        self.assertEqual(list(self.root.glob("seqlog-*")), [])

    def test_randomized_set_oracle(self):
        rng = random.Random(71023)
        config = self.windows([{"stream": ["a"], "first": 0, "last": 49}])
        for _ in range(60):
            values = [rng.randrange(50) for _ in range(rng.randrange(1, 80))]
            result = self.seqs(values, expected=config)
            self.assertEqual(result["totals"]["missing"], 50 - len(set(values)))
            self.assertEqual(result["totals"]["duplicates"], len(values) - len(set(values)))
            high, seen, regressions = -1, set(), 0
            for n in values:
                regressions += n not in seen and n < high
                seen.add(n)
                high = max(high, n)
            self.assertEqual(result["totals"]["arrival_regressions"], regressions)

    def test_cli_exit_codes(self):
        path = self.capture([{"stream": "a", "seq": 0}])

        def run(*args):
            return subprocess.run(
                [sys.executable, "-m", "seqlog_audit", *map(str, args)],
                capture_output=True,
                text=True,
                timeout=15,
            )

        self.assertEqual(run(path).returncode, 0)
        self.capture([{"stream": "a", "seq": 0}] * 2)
        bad = run(path)
        self.assertEqual(bad.returncode, 1)
        self.assertTrue(json.loads(bad.stdout)["findings"])
        missing = run(self.root / "absent")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertNotIn("Traceback", missing.stderr)


if __name__ == "__main__":
    unittest.main()
