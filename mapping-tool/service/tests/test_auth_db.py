"""Login check (app/auth.py, shared with the DJI backend JWT) and migration files (app/db.py)."""
import time
import unittest
from types import SimpleNamespace

import jwt
from fastapi import HTTPException

from app import auth, db

SECRET = "test-secret-0123456789abcdef0123456789"
SETTINGS = SimpleNamespace(jwt_secret=SECRET)


def token(secret: str = SECRET, **claims) -> str:
    base = {"iss": "DJI", "exp": int(time.time()) + 600, "workspace_id": "ws-1", "username": "adminPC"}
    base.update(claims)
    return jwt.encode({k: v for k, v in base.items() if v is not None}, secret, algorithm="HS256")


class DecodeUserTest(unittest.TestCase):
    def test_valid(self):
        user = auth.decode_user(SETTINGS, token())
        self.assertEqual((user.workspace_id, user.username), ("ws-1", "adminPC"))

    def test_rejected(self):
        cases = {
            "missing": None,
            "garbage": "abc.def.ghi",
            "foreign secret": token(secret="another-secret-0123456789abcdef0123"),
            "expired": token(exp=int(time.time()) - 10),
            "no expiry": token(exp=None),
            "foreign issuer": token(iss="someone"),
            "no workspace": token(workspace_id=None),
            "alg none": jwt.encode({"iss": "DJI", "exp": int(time.time()) + 600, "workspace_id": "ws-1"},
                                   None, algorithm="none"),
        }
        for name, tok in cases.items():
            with self.subTest(name), self.assertRaises(HTTPException) as ctx:
                auth.decode_user(SETTINGS, tok)
            self.assertEqual(ctx.exception.status_code, 401)


class MigrationTest(unittest.TestCase):
    def test_files_are_numbered_without_gaps(self):
        names = sorted(p.name for p in db.MIGRATIONS_DIR.iterdir() if p.suffix == ".sql")
        self.assertTrue(names)
        for i, name in enumerate(names, 1):
            self.assertRegex(name, db._MIGRATION_RE)
            self.assertEqual(int(name[:4]), i, f"gap or duplicate at {name}")

    def test_split_statements_ignores_comments(self):
        sql = "-- comment; with semicolon\nCREATE TABLE a (x INT);\n\n-- more\nINSERT INTO a VALUES (1);\n"
        self.assertEqual(db._split_statements(sql), ["CREATE TABLE a (x INT)", "INSERT INTO a VALUES (1)"])

    def test_every_migration_splits(self):
        for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            with self.subTest(path.name):
                self.assertTrue(db._split_statements(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
