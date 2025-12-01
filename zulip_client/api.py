"""Zulip API client."""

from __future__ import annotations

import base64
import json
import sys
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .credentials import get_site_credentials, SiteCredentials

# Rate limiting configuration
DEFAULT_REQUEST_DELAY = 0.1  # 100ms between requests to be gentle
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0  # Start with 1 second on rate limit


class ZulipClient:
    """Client for interacting with the Zulip API."""

    def __init__(self, site_name: Optional[str] = None):
        self.site_name = site_name or "leanprover"
        self.credentials: SiteCredentials = get_site_credentials(site_name)
        self._last_request_time: float = 0

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
        """Make an API request with rate limiting and retry logic."""
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

        # Ensure minimum delay between requests
        elapsed = time.time() - self._last_request_time
        if elapsed < DEFAULT_REQUEST_DELAY:
            time.sleep(DEFAULT_REQUEST_DELAY - elapsed)

        retry_delay = INITIAL_RETRY_DELAY
        for attempt in range(MAX_RETRIES + 1):
            try:
                self._last_request_time = time.time()
                with urlopen(request) as response:
                    return json.loads(response.read().decode())
            except HTTPError as e:
                if e.code == 429:
                    # Rate limited - get retry delay from header or use exponential backoff
                    retry_after = e.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_time = float(retry_after)
                        except ValueError:
                            wait_time = retry_delay
                    else:
                        wait_time = retry_delay

                    if attempt < MAX_RETRIES:
                        sys.stderr.write(
                            f"Rate limited, waiting {wait_time:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})...\n"
                        )
                        sys.stderr.flush()
                        time.sleep(wait_time)
                        retry_delay *= 2  # Exponential backoff
                        continue
                    else:
                        raise RuntimeError(
                            f"Rate limited after {MAX_RETRIES} retries. Try again later."
                        )
                else:
                    error_body = e.read().decode()
                    raise RuntimeError(f"Zulip API error {e.code}: {error_body}")

        raise RuntimeError("Unexpected error in request retry loop")

    def register(self) -> Dict[str, Any]:
        """Register an event queue and get initial state including unread messages."""
        # Note: We don't pass any filtering parameters (event_types, fetch_event_types)
        # because they filter out unread_msgs from the response.
        # Without filters, we get everything including unread_msgs and subscriptions.
        response = self._request("POST", "/register", None)

        if response.get("result") != "success":
            raise RuntimeError(f"Register failed: {response.get('msg')}")

        # Build set of muted streams
        muted_streams = {
            s["stream_id"]
            for s in response.get("subscriptions", [])
            if s.get("is_muted")
        }

        # Build set of muted topics (visibility_policy=1 means muted)
        muted_topics = {
            (t["stream_id"], t["topic_name"])
            for t in response.get("user_topics", [])
            if t.get("visibility_policy") == 1
        }

        # Filter unread_msgs to exclude muted streams and topics
        unread_msgs = response.get("unread_msgs", {
            "pms": [],
            "streams": [],
            "huddles": [],
            "mentions": [],
            "count": 0,
        })

        filtered_streams = [
            s for s in unread_msgs.get("streams", [])
            if s["stream_id"] not in muted_streams
            and (s["stream_id"], s["topic"]) not in muted_topics
        ]

        return {
            "unread_msgs": {
                **unread_msgs,
                "streams": filtered_streams,
            },
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
                "apply_markdown": "false",
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
                    "apply_markdown": "false",
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

    def get_stream_topics(self, stream_id: int) -> List[Dict[str, Any]]:
        """Get all topics in a stream.

        Returns list of dicts with: name, max_id
        """
        response = self._request(
            "GET",
            f"/users/me/{stream_id}/topics",
        )

        if response.get("result") != "success":
            raise RuntimeError(f"Get topics failed: {response.get('msg')}")

        return response.get("topics", [])

    def scan_my_topics(
        self,
        start_anchor: Union[str, int] = "newest",
        stop_at_message_id: Optional[int] = None,
        needed_callback: Optional[callable] = None,
        verbose: bool = False,
    ) -> tuple[List[Dict[str, Any]], Optional[int], bool]:
        """Scan messages to find topics where the current user has participated.

        Args:
            start_anchor: Where to start scanning ("newest" or a message ID)
            stop_at_message_id: Stop when we reach this message ID (exclusive)
            needed_callback: Called with each new topic; return False to stop early
            verbose: Show progress

        Returns:
            (topics, oldest_scanned_id, reached_end)
            - topics: list of dicts with stream_name, stream_id, topic_name
            - oldest_scanned_id: the oldest message ID we scanned (for resuming)
            - reached_end: True if we hit the oldest message (no more to scan)
        """
        narrow = [{"operator": "sender", "operand": self.credentials["email"]}]

        # Track unique topics (stream_id, topic_name) -> stream_name
        topics: Dict[tuple, str] = {}
        anchor: Union[str, int] = start_anchor
        batch_size = 1000
        oldest_scanned_id: Optional[int] = None
        reached_end = False
        stopped_early = False

        while True:
            response = self._request(
                "GET",
                "/messages",
                {
                    "narrow": json.dumps(narrow),
                    "anchor": str(anchor),
                    "num_before": str(batch_size),
                    "num_after": "0",
                    "apply_markdown": "false",
                },
            )

            if response.get("result") != "success":
                raise RuntimeError(f"Get messages failed: {response.get('msg')}")

            messages = response.get("messages", [])

            if not messages:
                reached_end = True
                break

            for msg in messages:
                # Stop if we've reached the boundary
                if stop_at_message_id and msg["id"] <= stop_at_message_id:
                    # Return what we have so far
                    return (
                        [
                            {"stream_id": sid, "stream_name": sname, "topic_name": tname}
                            for (sid, tname), sname in topics.items()
                        ],
                        oldest_scanned_id,
                        False,  # There's more history beyond the boundary
                    )

                oldest_scanned_id = msg["id"]

                if msg.get("type") == "stream":
                    key = (msg["stream_id"], msg["subject"])
                    if key not in topics:
                        topics[key] = msg["display_recipient"]
                        # Check if caller wants us to stop
                        if needed_callback:
                            topic_info = {
                                "stream_id": msg["stream_id"],
                                "stream_name": msg["display_recipient"],
                                "topic_name": msg["subject"],
                            }
                            if not needed_callback(topic_info):
                                stopped_early = True
                                break

            if stopped_early:
                break

            if verbose:
                sys.stderr.write(f"  Scanned messages, found {len(topics)} topics so far...\r")
                sys.stderr.flush()

            if response.get("found_oldest") or len(messages) < batch_size:
                reached_end = True
                break

            # Move anchor to oldest message in this batch minus 1
            anchor = messages[-1]["id"] - 1

        if verbose and not stopped_early:
            sys.stderr.write(f"  Found {len(topics)} topics.                              \n")
            sys.stderr.flush()

        return (
            [
                {"stream_id": sid, "stream_name": sname, "topic_name": tname}
                for (sid, tname), sname in topics.items()
            ],
            oldest_scanned_id,
            reached_end and not stopped_early,
        )
