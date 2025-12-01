"""Export functionality for messages."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).parent.parent / "data"
EXPORT_DIR = DATA_DIR / "export"


def sanitize_filename(name: str) -> str:
    """Convert a name to a safe filename."""
    # Replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', "-", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.lower()


def export_topic_to_json(
    site_name: str,
    stream_name: str,
    topic_name: str,
    messages: List[Dict[str, Any]],
    unread_message_ids: Optional[List[int]] = None,
) -> str:
    """Export a topic to a JSON file, returning the file path."""
    unread_message_ids = unread_message_ids or []

    site_dir = EXPORT_DIR / sanitize_filename(site_name)
    stream_dir = site_dir / sanitize_filename(stream_name)
    stream_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{sanitize_filename(topic_name)}.json"
    filepath = stream_dir / filename

    export_data = {
        "site": site_name,
        "stream": stream_name,
        "topic": topic_name,
        "messages": [
            {
                "id": m["message_id"],
                "sender": m["sender_name"],
                "sender_email": m["sender_email"],
                "timestamp": datetime.fromtimestamp(m["timestamp"]).isoformat(),
                "content": m.get("content_markdown") or m["content"],
                "content_text": m["content_text"],
            }
            for m in messages
        ],
        "unread_count": len(unread_message_ids),
        "unread_message_ids": unread_message_ids,
        "exported_at": datetime.now().isoformat(),
        "message_count": len(messages),
    }

    with open(filepath, "w") as f:
        json.dump(export_data, f, indent=2)

    return str(filepath)


def export_topic_to_markdown(
    site_name: str,
    stream_name: str,
    topic_name: str,
    messages: List[Dict[str, Any]],
    unread_message_ids: Optional[List[int]] = None,
) -> str:
    """Export a topic to a Markdown file, returning the file path."""
    unread_message_ids = unread_message_ids or []
    unread_set = set(unread_message_ids)

    site_dir = EXPORT_DIR / sanitize_filename(site_name)
    stream_dir = site_dir / sanitize_filename(stream_name)
    stream_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{sanitize_filename(topic_name)}.md"
    filepath = stream_dir / filename

    lines = [
        f"# {stream_name} > {topic_name}",
        "",
        f"Site: {site_name}",
        f"Exported: {datetime.now().isoformat()}",
        f"Messages: {len(messages)}",
        f"Unread: {len(unread_message_ids)}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        is_unread = msg["message_id"] in unread_set
        date_str = datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")

        lines.append(f"## {'[UNREAD] ' if is_unread else ''}{msg['sender_name']}")
        lines.append(f"*{date_str}*")
        lines.append("")
        # Prefer markdown content, fall back to stripped HTML for old messages
        content = msg.get("content_markdown") or msg["content_text"]
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(filepath, "w") as f:
        f.write("\n".join(lines))

    return str(filepath)
