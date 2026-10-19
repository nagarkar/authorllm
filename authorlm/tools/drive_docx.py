"""Upload or refresh a DOCX on Google Drive as a Google Doc, using the Doc bridge's own credentials.
Usage: python3 tools/drive_docx.py <file.docx> --title "Name"      (create)
       python3 tools/drive_docx.py <file.docx> --update <fileId>    (replace content in place)
Run from the authorlm package directory (needs .env and config as the CLI does). Non-interactive: the
OAuth token must already exist (run 'authorlm doc auth' on the author's word if it does not)."""
import sys, tomllib
from authorlm import paths, gdocs
from googleapiclient.http import MediaFileUpload
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GDOC = "application/vnd.google-apps.document"
def main(argv):
    src = argv[1]; mode, arg = argv[2], argv[3]
    paths.load_env()
    cfg = tomllib.loads(paths.config_path().read_text()) if paths.config_path().exists() else {}
    svc = gdocs.get_service(cfg, None, interactive=False)
    media = MediaFileUpload(src, mimetype=DOCX, resumable=False)
    if mode == "--update":
        f = svc.files().update(fileId=arg, media_body=media, fields="id,webViewLink,modifiedTime").execute()
    else:
        f = svc.files().create(body={"name": arg, "mimeType": GDOC}, media_body=media, fields="id,webViewLink").execute()
    print(f["webViewLink"])
if __name__ == "__main__": main(sys.argv)
