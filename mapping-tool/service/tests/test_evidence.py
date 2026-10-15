"""Evidence register: canonical form, hash chain and verdicts (no database, no MinIO)."""
import hashlib
import unittest
from datetime import datetime

from app import evidence


def media(n: int) -> dict:
    return {"file_id": f"f{n}", "workspace_id": "ws-1", "file_name": f"DJI_{n:04d}_W.JPG",
            "object_key": f"wayline/DJI_{n:04d}_W.JPG", "drone": "1581F5", "payload": "M3E",
            "uploaded_at": datetime(2026, 10, 15, 8, 0, n, 123000)}


def chain(count: int) -> list[dict]:
    rows, previous = [], None
    for n in range(1, count + 1):
        data = f"photo-{n}".encode()
        row = evidence.next_record(previous, media(n), len(data), hashlib.sha256(data).hexdigest(),
                                   datetime(2026, 10, 15, 9, 0, n, 456789))
        rows.append(row)
        previous = row
    return rows


class CanonicalTest(unittest.TestCase):
    def test_deterministic_and_millisecond_round_trip(self):
        row = chain(1)[0]
        # MySQL DATETIME(3) returns milliseconds only; the sealed time is stored that way
        self.assertEqual(row["sealed_at"].microsecond, 456000)
        stored = {**row, "sealed_at": row["sealed_at"].replace(microsecond=456000)}
        self.assertEqual(evidence.record_hash(stored), row["record_hash"])

    def test_field_boundaries_are_unambiguous(self):
        row = chain(1)[0]
        a = {**row, "drone": "AB", "payload": "C"}
        b = {**row, "drone": "A", "payload": "BC"}
        self.assertNotEqual(evidence.canonical(a), evidence.canonical(b))

    def test_first_record_links_to_genesis(self):
        row = chain(1)[0]
        self.assertEqual((row["seq"], row["prev_hash"]), (1, evidence.GENESIS))


class ChainTest(unittest.TestCase):
    def test_valid_chain(self):
        rows = chain(5)
        result = evidence.verify_chain(rows)
        self.assertEqual(result, {"status": "ok", "records": 5, "head": rows[-1]["record_hash"]})
        self.assertEqual(evidence.verify_chain([])["status"], "empty")

    def test_modified_hash_detected(self):
        rows = chain(4)
        rows[1] = {**rows[1], "sha256": "0" * 64}
        result = evidence.verify_chain(rows)
        self.assertEqual((result["status"], result["broken_at"], result["reason"]), ("broken", 2, "record_modified"))

    def test_rewritten_row_breaks_the_next_link(self):
        rows = chain(4)
        forged = {**rows[1], "sha256": "0" * 64}
        forged["record_hash"] = evidence.record_hash(forged)  # attacker recomputes the row hash
        rows[1] = forged
        result = evidence.verify_chain(rows)
        self.assertEqual((result["broken_at"], result["reason"]), (3, "link_mismatch"))

    def test_deleted_row_detected(self):
        rows = chain(4)
        del rows[2]
        self.assertEqual(evidence.verify_chain(rows)["reason"], "sequence_gap")


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.rows = chain(3)
        self.row = self.rows[1]

    def test_intact(self):
        verdict = evidence.file_verdict(self.row, self.rows[0], (self.row["size_bytes"], self.row["sha256"]))
        self.assertEqual((verdict["file"], verdict["record"]), ("intact", "ok"))

    def test_modified_and_missing(self):
        self.assertEqual(evidence.file_verdict(self.row, self.rows[0], (5, "f" * 64))["file"], "modified")
        self.assertEqual(evidence.file_verdict(self.row, self.rows[0], None)["file"], "missing")

    def test_broken_record_or_link(self):
        tampered = {**self.row, "file_name": "other.jpg"}
        self.assertEqual(evidence.file_verdict(tampered, self.rows[0], None)["record"], "broken")
        self.assertEqual(evidence.file_verdict(self.row, None, None)["record"], "broken")  # seq 2 without predecessor


class HashStreamTest(unittest.TestCase):
    def test_chunks(self):
        size, digest = evidence.hash_stream([b"ab", b"", b"c"])
        self.assertEqual((size, digest), (3, hashlib.sha256(b"abc").hexdigest()))


if __name__ == "__main__":
    unittest.main()
