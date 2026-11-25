"""`doc access` and `doc share` (authorlm.doc_access): a review copy's sharing
read back, and one reader given comment access with everything else closed.

Hermetic: a fake Drive service, no network. Run: python3 tests/test_doc_access.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_passes  # noqa: E402,F401  (pins config lookups away from the real ones)

from authorlm import doc_access  # noqa: E402


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeDrive:
    """One file's metadata and permissions, as Drive v3 keeps them."""

    def __init__(self, copy_locked=False, perms=None):
        self.meta = {"id": "DOC1", "name": "Beta copy for Ann",
                     "webViewLink": "https://docs.google.com/document/d/DOC1/edit",
                     "copyRequiresWriterPermission": copy_locked}
        self.perms = list(perms or [{"id": "o", "type": "user", "role": "owner",
                                     "emailAddress": "author@example.com"}])
        self.sent: list[dict] = []

    def files(self):
        drive = self

        class Files:
            def get(self, fileId, fields):
                return _Call(lambda: dict(drive.meta))

            def update(self, fileId, body, fields):
                def run():
                    drive.meta.update(body)
                    return {"id": fileId}
                return _Call(run)
        return Files()

    def permissions(self):
        drive = self

        class Perms:
            def list(self, fileId, fields):
                return _Call(lambda: {"permissions": [dict(p) for p in drive.perms]})

            def delete(self, fileId, permissionId):
                def run():
                    drive.perms = [p for p in drive.perms if p["id"] != permissionId]
                return _Call(run)

            def create(self, fileId, body, sendNotificationEmail=True):
                def run():
                    drive.sent.append({"notify": sendNotificationEmail, **body})
                    drive.perms.append({"id": f"p{len(drive.perms)}", **{
                        "type": body["type"], "role": body["role"],
                        "emailAddress": body["emailAddress"]}})
                return _Call(run)

            def update(self, fileId, permissionId, body):
                def run():
                    for p in drive.perms:
                        if p["id"] == permissionId:
                            p.update(body)
                return _Call(run)
        return Perms()


class AccessTest(unittest.TestCase):
    def test_a_fresh_upload_is_not_yet_confidential(self):
        report = doc_access.access(FakeDrive(), "DOC1")
        self.assertFalse(report["ok"])
        self.assertIn("commenters and viewers can download, print and copy",
                      report["problems"])

    def test_link_sharing_and_editors_are_named(self):
        drive = FakeDrive(copy_locked=True, perms=[
            {"id": "o", "type": "user", "role": "owner", "emailAddress": "a@x.com"},
            {"id": "l", "type": "anyone", "role": "reader"},
            {"id": "w", "type": "user", "role": "writer", "emailAddress": "w@x.com"},
        ])
        report = doc_access.access(drive, "https://docs.google.com/document/d/DOC1/edit")
        self.assertEqual(report["file_id"], "DOC1")
        self.assertEqual(len(report["problems"]), 2)
        self.assertTrue(any("link sharing" in p for p in report["problems"]))
        self.assertTrue(any("w@x.com can edit" in p for p in report["problems"]))

    def test_a_named_reader_must_be_a_commenter(self):
        drive = FakeDrive(copy_locked=True)
        report = doc_access.access(drive, "DOC1", reader="ann@example.com")
        self.assertEqual(report["problems"], ["ann@example.com has no access"])


class ShareTest(unittest.TestCase):
    def test_share_closes_the_file_and_adds_one_silent_commenter(self):
        drive = FakeDrive(perms=[
            {"id": "o", "type": "user", "role": "owner", "emailAddress": "a@x.com"},
            {"id": "l", "type": "anyone", "role": "reader"},
        ])
        report = doc_access.share(drive, "DOC1", "Ann@Example.com")
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["commenters"], ["ann@example.com"])
        self.assertTrue(drive.meta["copyRequiresWriterPermission"])
        self.assertEqual(drive.sent, [{"notify": False, "type": "user",
                                       "role": "commenter",
                                       "emailAddress": "ann@example.com"}])
        self.assertNotIn("anyone", [p["type"] for p in drive.perms])

    def test_sharing_again_adds_nobody_and_demotes_an_editor(self):
        drive = FakeDrive(copy_locked=True, perms=[
            {"id": "o", "type": "user", "role": "owner", "emailAddress": "a@x.com"},
            {"id": "r", "type": "user", "role": "writer", "emailAddress": "ann@example.com"},
        ])
        report = doc_access.share(drive, "DOC1", "ann@example.com")
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(drive.sent, [])
        self.assertEqual(report["commenters"], ["ann@example.com"])

    def test_share_needs_an_email(self):
        with self.assertRaises(ValueError):
            doc_access.share(FakeDrive(), "DOC1", "Ann")


if __name__ == "__main__":
    unittest.main()
