"""Credential management for Zulip API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, TypedDict


class SiteCredentials(TypedDict):
    email: str
    api_key: str
    site: str


class CredentialsFile(TypedDict):
    default: str
    sites: Dict[str, SiteCredentials]


CREDENTIALS_PATH = Path.home() / "metacortex" / ".credentials" / "zulip.json"

_cached_credentials: Optional[CredentialsFile] = None


def load_credentials() -> CredentialsFile:
    """Load credentials from the JSON file."""
    global _cached_credentials
    if _cached_credentials is not None:
        return _cached_credentials

    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(f"Credentials file not found at {CREDENTIALS_PATH}")

    with open(CREDENTIALS_PATH) as f:
        _cached_credentials = json.load(f)

    return _cached_credentials


def get_site_credentials(site_name: Optional[str] = None) -> SiteCredentials:
    """Get credentials for a specific site."""
    credentials = load_credentials()
    site = site_name or credentials["default"]

    if site not in credentials["sites"]:
        available = ", ".join(credentials["sites"].keys())
        raise ValueError(f"Site '{site}' not found. Available sites: {available}")

    return credentials["sites"][site]


def get_default_site() -> str:
    """Get the default site name."""
    return load_credentials()["default"]


def list_sites() -> List[str]:
    """List all configured site names."""
    return list(load_credentials()["sites"].keys())
