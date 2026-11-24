"""Builds Google credentials from a refresh token stored as a GitHub secret."""
from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from .util import secret

TOKEN_URI = "https://oauth2.googleapis.com/token"

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def credentials(refresh_token_secret: str, scopes: list[str]) -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=secret(refresh_token_secret),
        token_uri=TOKEN_URI,
        client_id=secret("GOOGLE_CLIENT_ID"),
        client_secret=secret("GOOGLE_CLIENT_SECRET"),
        scopes=scopes,
    )
    creds.refresh(Request())
    return creds
