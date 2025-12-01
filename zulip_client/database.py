"""SQLite database operations."""

from __future__ import annotations

import html
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Database location
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "zulip.db"

SCHEMA = """
-- Sites (leanprover, lean-fro)
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  url TEXT NOT NULL,
  last_sync TEXT
);

-- Streams/Channels
CREATE TABLE IF NOT EXISTS streams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id),
  stream_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  UNIQUE(site_id, stream_id)
);

-- Topics
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stream_id INTEGER NOT NULL REFERENCES streams(id),
  name TEXT NOT NULL,
  last_message_id INTEGER,
  UNIQUE(stream_id, name)
);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL REFERENCES topics(id),
  message_id INTEGER NOT NULL,
  sender_name TEXT NOT NULL,
  sender_email TEXT NOT NULL,
  content TEXT NOT NULL,
  content_text TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  raw_json TEXT,
  UNIQUE(topic_id, message_id)
);

-- Track current unread state (synced from API)
CREATE TABLE IF NOT EXISTS unread_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id),
  message_id INTEGER NOT NULL,
  stream_id INTEGER,
  stream_name TEXT,
  topic_name TEXT,
  UNIQUE(site_id, message_id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_unread_site ON unread_messages(site_id);
CREATE INDEX IF NOT EXISTS idx_streams_site ON streams(site_id);
CREATE INDEX IF NOT EXISTS idx_topics_stream ON topics(stream_id);
"""

_db: Optional[sqlite3.Connection] = None


def strip_html(html_content: str) -> str:
    """Convert HTML to plain text."""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", html_content)
    # Decode HTML entities
    text = html.unescape(text)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_database() -> sqlite3.Connection:
    """Get or create database connection."""
    global _db
    if _db is not None:
        return _db

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    _db = sqlite3.connect(DB_PATH)
    _db.row_factory = sqlite3.Row
    _db.executescript(SCHEMA)

    return _db


def close_database() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        _db.close()
        _db = None


# Site operations
def get_or_create_site(name: str, url: str) -> int:
    """Get or create a site, returning its ID."""
    db = get_database()
    cursor = db.execute("SELECT id FROM sites WHERE name = ?", (name,))
    row = cursor.fetchone()
    if row:
        return row["id"]

    cursor = db.execute("INSERT INTO sites (name, url) VALUES (?, ?)", (name, url))
    db.commit()
    return cursor.lastrowid  # type: ignore


def update_site_last_sync(site_id: int) -> None:
    """Update the last sync time for a site."""
    db = get_database()
    db.execute(
        "UPDATE sites SET last_sync = ? WHERE id = ?",
        (datetime.now().isoformat(), site_id),
    )
    db.commit()


# Stream operations
def get_or_create_stream(site_id: int, stream_id: int, name: str) -> int:
    """Get or create a stream, returning its database ID."""
    db = get_database()
    cursor = db.execute(
        "SELECT id FROM streams WHERE site_id = ? AND stream_id = ?",
        (site_id, stream_id),
    )
    row = cursor.fetchone()
    if row:
        # Update name if changed
        db.execute("UPDATE streams SET name = ? WHERE id = ?", (name, row["id"]))
        db.commit()
        return row["id"]

    cursor = db.execute(
        "INSERT INTO streams (site_id, stream_id, name) VALUES (?, ?, ?)",
        (site_id, stream_id, name),
    )
    db.commit()
    return cursor.lastrowid  # type: ignore


# Topic operations
def get_or_create_topic(stream_db_id: int, name: str) -> int:
    """Get or create a topic, returning its database ID."""
    db = get_database()
    cursor = db.execute(
        "SELECT id FROM topics WHERE stream_id = ? AND name = ?",
        (stream_db_id, name),
    )
    row = cursor.fetchone()
    if row:
        return row["id"]

    cursor = db.execute(
        "INSERT INTO topics (stream_id, name) VALUES (?, ?)",
        (stream_db_id, name),
    )
    db.commit()
    return cursor.lastrowid  # type: ignore


def get_topic_last_message_id(topic_id: int) -> Optional[int]:
    """Get the last message ID for a topic."""
    db = get_database()
    cursor = db.execute(
        "SELECT last_message_id FROM topics WHERE id = ?", (topic_id,)
    )
    row = cursor.fetchone()
    return row["last_message_id"] if row else None


def update_topic_last_message_id(topic_id: int, message_id: int) -> None:
    """Update the last message ID for a topic."""
    db = get_database()
    db.execute(
        "UPDATE topics SET last_message_id = ? WHERE id = ?",
        (message_id, topic_id),
    )
    db.commit()


# Message operations
def insert_messages(topic_id: int, messages: List[Dict[str, Any]]) -> int:
    """Insert messages, returning count of new messages."""
    db = get_database()
    inserted = 0

    for msg in messages:
        content_text = strip_html(msg["content"])
        try:
            db.execute(
                """
                INSERT OR IGNORE INTO messages
                (topic_id, message_id, sender_name, sender_email, content, content_text, timestamp, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    msg["id"],
                    msg["sender_full_name"],
                    msg["sender_email"],
                    msg["content"],
                    content_text,
                    msg["timestamp"],
                    str(msg),
                ),
            )
            if db.total_changes > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass  # Already exists

    db.commit()
    return inserted


# Unread tracking
def clear_unread_messages(site_id: int) -> None:
    """Clear all unread messages for a site."""
    db = get_database()
    db.execute("DELETE FROM unread_messages WHERE site_id = ?", (site_id,))
    db.commit()


def insert_unread_messages(
    site_id: int,
    unreads: List[Dict[str, Any]],
    stream_map: Dict[int, str],
) -> None:
    """Insert unread message records."""
    db = get_database()

    for unread in unreads:
        stream_id = unread["stream_id"]
        stream_name = stream_map.get(stream_id, f"stream_{stream_id}")
        topic = unread["topic"]

        for msg_id in unread["unread_message_ids"]:
            db.execute(
                """
                INSERT OR IGNORE INTO unread_messages
                (site_id, message_id, stream_id, stream_name, topic_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (site_id, msg_id, stream_id, stream_name, topic),
            )

    db.commit()


# Query operations
def get_unread_summary(site_id: int) -> List[Dict[str, Any]]:
    """Get unread message summary grouped by stream and topic."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT stream_name, stream_id, topic_name, COUNT(*) as count
        FROM unread_messages
        WHERE site_id = ?
        GROUP BY stream_name, stream_id, topic_name
        ORDER BY stream_name, topic_name
        """,
        (site_id,),
    )

    summary: Dict[str, Dict[str, Any]] = {}
    for row in cursor:
        stream_name = row["stream_name"]
        if stream_name not in summary:
            summary[stream_name] = {
                "stream_name": stream_name,
                "stream_id": row["stream_id"],
                "topics": [],
                "total_count": 0,
            }
        summary[stream_name]["topics"].append({
            "topic_name": row["topic_name"],
            "count": row["count"],
        })
        summary[stream_name]["total_count"] += row["count"]

    return list(summary.values())


def get_total_unread_count(site_id: int) -> int:
    """Get total unread message count for a site."""
    db = get_database()
    cursor = db.execute(
        "SELECT COUNT(*) as count FROM unread_messages WHERE site_id = ?",
        (site_id,),
    )
    return cursor.fetchone()["count"]


def get_unread_topics(site_id: int) -> List[Dict[str, Any]]:
    """Get list of topics with unread messages."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT stream_id, stream_name, topic_name, GROUP_CONCAT(message_id) as message_ids
        FROM unread_messages
        WHERE site_id = ?
        GROUP BY stream_id, stream_name, topic_name
        """,
        (site_id,),
    )

    return [
        {
            "stream_id": row["stream_id"],
            "stream_name": row["stream_name"],
            "topic_name": row["topic_name"],
            "message_ids": [int(x) for x in row["message_ids"].split(",")],
        }
        for row in cursor
    ]


def get_topic_messages(
    site_id: int, stream_name: str, topic_name: str
) -> List[Dict[str, Any]]:
    """Get all stored messages for a topic."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT m.*
        FROM messages m
        JOIN topics t ON m.topic_id = t.id
        JOIN streams s ON t.stream_id = s.id
        WHERE s.site_id = ? AND s.name = ? AND t.name = ?
        ORDER BY m.timestamp ASC
        """,
        (site_id, stream_name, topic_name),
    )
    return [dict(row) for row in cursor]


def get_site_id(site_name: str) -> Optional[int]:
    """Get the database ID for a site by name."""
    db = get_database()
    cursor = db.execute("SELECT id FROM sites WHERE name = ?", (site_name,))
    row = cursor.fetchone()
    return row["id"] if row else None


def get_all_streams(site_id: int) -> List[Dict[str, Any]]:
    """Get all streams for a site."""
    db = get_database()
    cursor = db.execute(
        "SELECT name, stream_id FROM streams WHERE site_id = ?",
        (site_id,),
    )
    return [{"name": row["name"], "stream_id": row["stream_id"]} for row in cursor]


def get_topics_for_stream(stream_db_id: int) -> List[Dict[str, Any]]:
    """Get all topics for a stream."""
    db = get_database()
    cursor = db.execute(
        "SELECT name, id FROM topics WHERE stream_id = ?",
        (stream_db_id,),
    )
    return [{"name": row["name"], "id": row["id"]} for row in cursor]
