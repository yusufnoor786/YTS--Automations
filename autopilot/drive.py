"""Google Drive: find niche folders, list videos, download, and move after posting."""
from __future__ import annotations

import io
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from .google_auth import DRIVE_SCOPES, credentials
from .util import log

FOLDER_MIME = "application/vnd.google-apps.folder"
SUBFOLDERS = ("_posted", "_duplicates", "_failed")
FILE_FIELDS = "id,name,md5Checksum,createdTime,size,mimeType,description,parents"


class Drive:
    def __init__(self):
        self.svc = build("drive", "v3", credentials=credentials("GDRIVE_REFRESH_TOKEN", DRIVE_SCOPES),
                         cache_discovery=False)
        self._folder_cache: dict[tuple[str, str], str] = {}

    # ── folders ──────────────────────────────────────────────
    def folder(self, parent_id: str, name: str, create: bool = True) -> str | None:
        key = (parent_id, name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        safe = name.replace("'", "\\'")
        q = f"'{parent_id}' in parents and name = '{safe}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        res = self.svc.files().list(q=q, fields="files(id,name)", pageSize=5,
                                    supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        if files:
            fid = files[0]["id"]
        elif create:
            fid = self.svc.files().create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                                          fields="id", supportsAllDrives=True).execute()["id"]
            log.info("Created Drive folder %s", name)
        else:
            return None
        self._folder_cache[key] = fid
        return fid

    def niche_folders(self, root_id: str, niche: str) -> dict[str, str]:
        """{'inbox': id, '_posted': id, '_duplicates': id, '_failed': id}"""
        inbox = self.folder(root_id, niche)
        out = {"inbox": inbox}
        for s in SUBFOLDERS:
            out[s] = self.folder(inbox, s)
        return out

    def root_name(self, root_id: str) -> str:
        return self.svc.files().get(fileId=root_id, fields="name", supportsAllDrives=True).execute()["name"]

    # ── files ────────────────────────────────────────────────
    def list_videos(self, folder_id: str) -> list[dict]:
        q = f"'{folder_id}' in parents and trashed = false and mimeType contains 'video/'"
        out, token = [], None
        while True:
            res = self.svc.files().list(q=q, fields=f"nextPageToken,files({FILE_FIELDS})", pageSize=200,
                                        orderBy="createdTime", pageToken=token,
                                        supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            out += res.get("files", [])
            token = res.get("nextPageToken")
            if not token:
                return out

    def download(self, file_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = self.svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(dest, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req, chunksize=32 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk(num_retries=3)
        return dest

    def move(self, file_id: str, to_folder: str) -> None:
        f = self.svc.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
        prev = ",".join(f.get("parents", []))
        if to_folder in f.get("parents", []):
            return
        self.svc.files().update(fileId=file_id, addParents=to_folder, removeParents=prev,
                                fields="id", supportsAllDrives=True).execute()
