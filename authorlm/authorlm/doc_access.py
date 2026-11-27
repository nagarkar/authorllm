"""Who can open a Drive file authorlm made, and sharing it with one reader.

`access` reports a file's sharing: its permissions (type, role, email), whether
commenters and viewers may download, print or copy it, and whether that is
what a confidential review copy needs (`ok`, `problems`): no link or domain
sharing, nobody but the owner able to edit, download/print/copy off for
everyone else, and, when a reader is named, that reader present as a
commenter. `share` makes a file so: it turns download/print/copy off, removes
link and domain sharing, gives the reader comment access without Google's
notification email (the author sends the link), then reports `access`. An
address with no Google account (Outlook, a work address) cannot be added
silently: Drive answers `invalidSharingRequest`, and `share` then lets Google
send its own invitation, so the reader can make a Google account on that
address and comment (owner, 2026-11-26: "let Google send its invitation to its
Outlook address. This should be the standard behavior."). The report's
`google_invited` says which happened.

The Drive token's scope is `drive.file`: both work on files authorlm created
(its uploads and export Docs), which is every review copy it makes.
"""
from __future__ import annotations

from .gdocs import _execute, parse_doc_id

PERMISSION_FIELDS = "permissions(id,type,role,emailAddress)"
FILE_FIELDS = "id,name,webViewLink,copyRequiresWriterPermission"
EDIT_ROLES = ("owner", "organizer", "fileOrganizer", "writer")


def _permissions(service, file_id: str) -> list[dict]:
    reply = _execute(service.permissions().list(
        fileId=file_id, fields=PERMISSION_FIELDS))
    return reply.get("permissions", [])


def access(service, ref: str, reader: str | None = None) -> dict:
    """The file's sharing and whether it is a confidential review copy."""
    file_id = parse_doc_id(ref)
    info = _execute(service.files().get(fileId=file_id, fields=FILE_FIELDS))
    perms = _permissions(service, file_id)
    rows = [{"type": p.get("type", ""), "role": p.get("role", ""),
             "email": (p.get("emailAddress") or "").lower()} for p in perms]
    problems: list[str] = []
    for row in rows:
        if row["type"] in ("anyone", "domain"):
            problems.append(f"link sharing is on ({row['type']}, "
                            f"{row['role']})")
        elif row["role"] in EDIT_ROLES and row["role"] != "owner":
            problems.append(f"{row['email'] or row['type']} can edit "
                            f"({row['role']})")
    if not info.get("copyRequiresWriterPermission"):
        problems.append("commenters and viewers can download, print and copy")
    if reader:
        want = reader.strip().lower()
        roles = [r["role"] for r in rows if r["email"] == want]
        if not roles:
            problems.append(f"{want} has no access")
        elif roles != ["commenter"]:
            problems.append(f"{want} is {', '.join(roles)}, not commenter")
    return {
        "file_id": file_id,
        "name": info.get("name", ""),
        "url": info.get("webViewLink")
        or f"https://docs.google.com/document/d/{file_id}/edit",
        "copy_requires_writer_permission":
            bool(info.get("copyRequiresWriterPermission")),
        "permissions": rows,
        "commenters": sorted(r["email"] for r in rows
                             if r["role"] == "commenter" and r["email"]),
        "ok": not problems,
        "problems": problems,
    }


def share(service, ref: str, reader: str) -> dict:
    """Give one reader comment access to a confidential review copy; the
    file's other sharing is closed down first. Returns `access`."""
    reader = (reader or "").strip().lower()
    if "@" not in reader:
        raise ValueError(f"not an email address: {reader!r}")
    file_id = parse_doc_id(ref)
    _execute(service.files().update(
        fileId=file_id, body={"copyRequiresWriterPermission": True},
        fields="id"))
    found = None
    for perm in _permissions(service, file_id):
        if perm.get("type") in ("anyone", "domain"):
            _execute(service.permissions().delete(
                fileId=file_id, permissionId=perm["id"]))
        elif (perm.get("emailAddress") or "").lower() == reader:
            found = perm
    invited = False
    if found is None:
        body = {"type": "user", "role": "commenter", "emailAddress": reader}
        try:
            _execute(service.permissions().create(
                fileId=file_id, sendNotificationEmail=False, body=body))
        except Exception as err:  # googleapiclient HttpError
            if "invalidSharingRequest" not in str(err) + str(
                    getattr(err, "content", b"")):
                raise
            _execute(service.permissions().create(
                fileId=file_id, sendNotificationEmail=True, body=body))
            invited = True
    elif found.get("role") not in ("commenter", "owner"):
        _execute(service.permissions().update(
            fileId=file_id, permissionId=found["id"],
            body={"role": "commenter"}))
    return {**access(service, file_id, reader=reader), "google_invited": invited}
