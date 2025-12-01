"""Zulip API client."""

from __future__ import annotations

import base64
import json
import sys
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .credentials import get_site_credentials, SiteCredentials


class ZulipClient:
    """Client for interacting with the Zulip API."""

    def __init__(self, site_name: Optional[str] = None):
        self.site_name = site_name or "leanprover"
        self.credentials: SiteCredentials = get_site_credentials(site_name)

    @property
    def site_url(self) -> str:
        return self.credentials["site"]

    @property
    def site(self) -> str:
        return self.site_name

    @property
    def _auth_header(self) -> str:
        auth_string = f"{self.credentials['email']}:{self.credentials['api_key']}"
        encoded = base64.b64encode(auth_string.encode()).decode()
        return f"Basic {encoded}"

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an API request."""
        url = f"{self.credentials['site']}/api/v1{endpoint}"

        headers = {
            "Authorization": self._auth_header,
        }

        data = None
        if method == "GET" and params:
            url = f"{url}?{urlencode(params)}"
        elif method == "POST" and params:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urlencode(params).encode()

        request = Request(url, data=data, headers=headers, method=method)

        try:
            with urlopen(request) as response:
                return json.loads(response.read().decode())
        except HTTPError as e:
            error_body = e.read().decode()
            raise RuntimeError(f"Zulip API error {e.code}: {error_body}")

    def register(self) -> Dict[str, Any]:
        """Register an event queue and get initial state including unread messages."""
        response = self._request(
            "POST",
            "/register",
            {
                "fetch_event_types": json.dumps(["message", "subscription"]),
                "event_types": json.dumps([]),
                "apply_markdown": "false",
            },
        )

        if response.get("result") != "success":
            raise RuntimeError(f"Register failed: {response.get('msg')}")

        return {
            "unread_msgs": response.get("unread_msgs", {
                "pms": [],
                "streams": [],
                "huddles": [],
                "mentions": [],
                "count": 0,
            }),
            "subscriptions": response.get("subscriptions", []),
        }

    def get_messages(
        self,
        narrow: Optional[List[Dict[str, str]]] = None,
        anchor: Union[str, int] = "newest",
        num_before: int = 0,
        num_after: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get messages with optional filtering."""
        response = self._request(
            "GET",
            "/messages",
            {
                "narrow": json.dumps(narrow or []),
                "anchor": str(anchor),
                "num_before": str(num_before),
                "num_after": str(num_after),
            },
        )

        if response.get("result") != "success":
            raise RuntimeError(f"Get messages failed: {response.get('msg')}")

        return response.get("messages", [])

    def get_topic_messages(
        self,
        stream_name: str,
        topic_name: str,
        after_message_id: Optional[int] = None,
        verbose: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get all messages in a stream/topic, handling pagination."""
        narrow = [
            {"operator": "stream", "operand": stream_name},
            {"operator": "topic", "operand": topic_name},
        ]

        all_messages: List[Dict[str, Any]] = []
        anchor: Union[str, int] = after_message_id + 1 if after_message_id else "oldest"
        batch_size = 1000

        while True:
            response = self._request(
                "GET",
                "/messages",
                {
                    "narrow": json.dumps(narrow),
                    "anchor": str(anchor),
                    "num_before": "0",
                    "num_after": str(batch_size),
                },
            )

            if response.get("result") != "success":
                raise RuntimeError(f"Get messages failed: {response.get('msg')}")

            messages = response.get("messages", [])

            # Filter out anchor message if we used a specific ID
            if isinstance(anchor, int):
                messages = [m for m in messages if m["id"] > anchor]

            if not messages:
                break

            all_messages.extend(messages)

            if verbose:
                sys.stderr.write(f"  Fetched {len(all_messages)} messages...\r")
                sys.stderr.flush()

            if response.get("found_newest") or len(messages) < batch_size:
                break

            anchor = messages[-1]["id"]

        if verbose:
            sys.stderr.write("\n")

        return all_messages
