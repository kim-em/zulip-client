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
  content_markdown TEXT,
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

-- Track sync-mine progress (oldest message ID scanned)
CREATE TABLE IF NOT EXISTS sync_mine_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER UNIQUE NOT NULL REFERENCES sites(id),
  oldest_scanned_message_id INTEGER NOT NULL
);

-- Track sync-all progress (high water mark for incremental sync)
CREATE TABLE IF NOT EXISTS sync_all_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER UNIQUE NOT NULL REFERENCES sites(id),
  last_synced_message_id INTEGER NOT NULL,
  message_count INTEGER NOT NULL
);

-- AI-generated summaries
CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL REFERENCES topics(id) UNIQUE,
  summary_text TEXT NOT NULL,
  importance TEXT NOT NULL,
  urgency TEXT NOT NULL,
  key_points TEXT,
  action_items TEXT,
  participants TEXT,
  last_message_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_unread_site ON unread_messages(site_id);
CREATE INDEX IF NOT EXISTS idx_streams_site ON streams(site_id);
CREATE INDEX IF NOT EXISTS idx_topics_stream ON topics(stream_id);
CREATE INDEX IF NOT EXISTS idx_summaries_importance ON summaries(importance);
CREATE INDEX IF NOT EXISTS idx_summaries_urgency ON summaries(urgency);
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

    # Migration: add content_markdown column if missing
    cursor = _db.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in cursor.fetchall()}
    if "content_markdown" not in columns:
        _db.execute("ALTER TABLE messages ADD COLUMN content_markdown TEXT")
        _db.commit()

    # Migration: create FTS5 table if missing
    _migrate_fts(_db)

    return _db


def _migrate_fts(db: sqlite3.Connection) -> None:
    """Create FTS5 tables and populate from existing data if needed."""
    # Messages FTS
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    )
    if cursor.fetchone() is None:
        db.execute("""
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content_text,
                sender_name,
                content='messages',
                content_rowid='id'
            )
        """)
        db.execute("""
            INSERT INTO messages_fts(rowid, content_text, sender_name)
            SELECT id, content_text, sender_name FROM messages
        """)
        db.commit()

    # Summaries FTS
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='summaries_fts'"
    )
    if cursor.fetchone() is None:
        db.execute("""
            CREATE VIRTUAL TABLE summaries_fts USING fts5(
                summary_text,
                key_points,
                action_items,
                content='summaries',
                content_rowid='id'
            )
        """)
        db.execute("""
            INSERT INTO summaries_fts(rowid, summary_text, key_points, action_items)
            SELECT id, summary_text, COALESCE(key_points, ''), COALESCE(action_items, '')
            FROM summaries
        """)
        db.commit()


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
    """Insert messages, returning count of new messages.

    The API returns markdown content (apply_markdown=false). We store:
    - content: the raw markdown from the API
    - content_markdown: same as content (for new messages)
    - content_text: plain text version (markdown with formatting stripped)
    """
    db = get_database()
    inserted = 0

    for msg in messages:
        content = msg["content"]
        # For markdown content, content_text is just the content itself
        # (no HTML to strip since we request markdown)
        content_text = content
        sender_name = msg["sender_full_name"]
        try:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO messages
                (topic_id, message_id, sender_name, sender_email, content, content_text, content_markdown, timestamp, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    msg["id"],
                    sender_name,
                    msg["sender_email"],
                    content,
                    content_text,
                    content,  # content_markdown
                    msg["timestamp"],
                    str(msg),
                ),
            )
            if cursor.rowcount > 0:
                # Also insert into FTS index
                db.execute(
                    """
                    INSERT INTO messages_fts(rowid, content_text, sender_name)
                    VALUES (?, ?, ?)
                    """,
                    (cursor.lastrowid, content_text, sender_name),
                )
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


def mark_topic_as_read(site_id: int, stream_name: str, topic_name: str) -> int:
    """Mark all messages in a topic as read. Returns number of messages marked."""
    db = get_database()
    cursor = db.execute(
        "DELETE FROM unread_messages WHERE site_id = ? AND stream_name = ? AND topic_name = ?",
        (site_id, stream_name, topic_name),
    )
    db.commit()
    return cursor.rowcount


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


def get_channels_summary(site_id: int) -> List[Dict[str, Any]]:
    """Get summary of all channels (streams) with topic and message counts."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT
            s.name as stream_name,
            s.stream_id,
            COUNT(DISTINCT t.id) as topic_count,
            COUNT(m.id) as message_count,
            (SELECT COUNT(*) FROM unread_messages u
             WHERE u.site_id = ? AND u.stream_name = s.name) as unread_count
        FROM streams s
        LEFT JOIN topics t ON t.stream_id = s.id
        LEFT JOIN messages m ON m.topic_id = t.id
        WHERE s.site_id = ?
        GROUP BY s.id, s.name, s.stream_id
        ORDER BY s.name
        """,
        (site_id, site_id),
    )
    return [
        {
            "stream_name": row["stream_name"],
            "stream_id": row["stream_id"],
            "topic_count": row["topic_count"],
            "message_count": row["message_count"],
            "unread_count": row["unread_count"],
        }
        for row in cursor
    ]


def get_topics_summary(site_id: int, stream_name: str) -> List[Dict[str, Any]]:
    """Get summary of all topics in a stream with message counts."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT
            t.name as topic_name,
            COUNT(m.id) as message_count,
            (SELECT COUNT(*) FROM unread_messages u
             WHERE u.site_id = ? AND u.stream_name = ? AND u.topic_name = t.name) as unread_count,
            MAX(m.timestamp) as last_message_time
        FROM topics t
        JOIN streams s ON t.stream_id = s.id
        LEFT JOIN messages m ON m.topic_id = t.id
        WHERE s.site_id = ? AND s.name = ?
        GROUP BY t.id, t.name
        ORDER BY last_message_time DESC
        """,
        (site_id, stream_name, site_id, stream_name),
    )
    return [
        {
            "topic_name": row["topic_name"],
            "message_count": row["message_count"],
            "unread_count": row["unread_count"],
            "last_message_time": row["last_message_time"],
        }
        for row in cursor
    ]


def get_topic_messages_with_unread(
    site_id: int, stream_name: str, topic_name: str
) -> List[Dict[str, Any]]:
    """Get all messages for a topic with unread status."""
    db = get_database()

    # Get unread message IDs for this topic
    unread_cursor = db.execute(
        """
        SELECT message_id FROM unread_messages
        WHERE site_id = ? AND stream_name = ? AND topic_name = ?
        """,
        (site_id, stream_name, topic_name),
    )
    unread_ids = {row["message_id"] for row in unread_cursor}

    # Get all messages
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

    return [
        {
            **dict(row),
            "is_unread": row["message_id"] in unread_ids,
        }
        for row in cursor
    ]


def get_stream_by_name(site_id: int, stream_name: str) -> Optional[Dict[str, Any]]:
    """Get a stream by name."""
    db = get_database()
    cursor = db.execute(
        "SELECT id, name, stream_id FROM streams WHERE site_id = ? AND name = ?",
        (site_id, stream_name),
    )
    row = cursor.fetchone()
    if row:
        return {"id": row["id"], "name": row["name"], "stream_id": row["stream_id"]}
    return None


def topic_has_messages(site_id: int, stream_name: str, topic_name: str) -> bool:
    """Check if a topic already has messages stored locally."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT 1 FROM messages m
        JOIN topics t ON m.topic_id = t.id
        JOIN streams s ON t.stream_id = s.id
        WHERE s.site_id = ? AND s.name = ? AND t.name = ?
        LIMIT 1
        """,
        (site_id, stream_name, topic_name),
    )
    return cursor.fetchone() is not None


def get_sync_mine_state(site_id: int) -> Optional[int]:
    """Get the oldest message ID we've scanned for sync-mine."""
    db = get_database()
    cursor = db.execute(
        "SELECT oldest_scanned_message_id FROM sync_mine_state WHERE site_id = ?",
        (site_id,),
    )
    row = cursor.fetchone()
    return row["oldest_scanned_message_id"] if row else None


def update_sync_mine_state(site_id: int, oldest_message_id: int) -> None:
    """Update the oldest message ID we've scanned for sync-mine."""
    db = get_database()
    db.execute(
        """
        INSERT INTO sync_mine_state (site_id, oldest_scanned_message_id)
        VALUES (?, ?)
        ON CONFLICT(site_id) DO UPDATE SET oldest_scanned_message_id = ?
        """,
        (site_id, oldest_message_id, oldest_message_id),
    )
    db.commit()


# Sync-all state operations
def get_sync_all_state(site_id: int) -> Optional[Dict[str, int]]:
    """Get the sync-all state for a site (high water mark and message count)."""
    db = get_database()
    cursor = db.execute(
        "SELECT last_synced_message_id, message_count FROM sync_all_state WHERE site_id = ?",
        (site_id,),
    )
    row = cursor.fetchone()
    if row:
        return {
            "last_synced_message_id": row["last_synced_message_id"],
            "message_count": row["message_count"],
        }
    return None


def update_sync_all_state(site_id: int, last_synced_message_id: int, message_count: int) -> None:
    """Update the sync-all state for a site."""
    db = get_database()
    db.execute(
        """
        INSERT INTO sync_all_state (site_id, last_synced_message_id, message_count)
        VALUES (?, ?, ?)
        ON CONFLICT(site_id) DO UPDATE SET
            last_synced_message_id = excluded.last_synced_message_id,
            message_count = excluded.message_count
        """,
        (site_id, last_synced_message_id, message_count),
    )
    db.commit()


def get_message_count_for_site(site_id: int, up_to_message_id: Optional[int] = None) -> int:
    """Get total message count for a site, optionally up to a specific message ID."""
    db = get_database()
    if up_to_message_id is not None:
        cursor = db.execute(
            """
            SELECT COUNT(*) as count
            FROM messages m
            JOIN topics t ON m.topic_id = t.id
            JOIN streams s ON t.stream_id = s.id
            WHERE s.site_id = ? AND m.message_id <= ?
            """,
            (site_id, up_to_message_id),
        )
    else:
        cursor = db.execute(
            """
            SELECT COUNT(*) as count
            FROM messages m
            JOIN topics t ON m.topic_id = t.id
            JOIN streams s ON t.stream_id = s.id
            WHERE s.site_id = ?
            """,
            (site_id,),
        )
    return cursor.fetchone()["count"]


def validate_sync_all_state(site_id: int) -> bool:
    """Validate that sync-all state is consistent with actual message count."""
    state = get_sync_all_state(site_id)
    if not state:
        return False

    actual_count = get_message_count_for_site(site_id, state["last_synced_message_id"])
    return actual_count == state["message_count"]


def get_max_message_id_for_site(site_id: int) -> Optional[int]:
    """Get the highest message ID stored for a site."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT MAX(m.message_id) as max_id
        FROM messages m
        JOIN topics t ON m.topic_id = t.id
        JOIN streams s ON t.stream_id = s.id
        WHERE s.site_id = ?
        """,
        (site_id,),
    )
    row = cursor.fetchone()
    return row["max_id"] if row and row["max_id"] else None


# Summary operations
def get_topic_by_names(
    site_id: int, stream_name: str, topic_name: str
) -> Optional[Dict[str, Any]]:
    """Get topic info by site/stream/topic names."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT t.id, t.name, t.last_message_id, s.name as stream_name
        FROM topics t
        JOIN streams s ON t.stream_id = s.id
        WHERE s.site_id = ? AND s.name = ? AND t.name = ?
        """,
        (site_id, stream_name, topic_name),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_summary(topic_id: int) -> Optional[Dict[str, Any]]:
    """Get cached summary for a topic."""
    db = get_database()
    cursor = db.execute("SELECT * FROM summaries WHERE topic_id = ?", (topic_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def save_summary(
    topic_id: int,
    summary_text: str,
    importance: str,
    urgency: str,
    last_message_id: int,
    key_points: Optional[str] = None,
    action_items: Optional[str] = None,
    participants: Optional[str] = None,
) -> None:
    """Save or update a summary."""
    db = get_database()

    # Check if summary already exists (for FTS update)
    cursor = db.execute("SELECT id FROM summaries WHERE topic_id = ?", (topic_id,))
    existing = cursor.fetchone()

    cursor = db.execute(
        """
        INSERT INTO summaries
        (topic_id, summary_text, importance, urgency, last_message_id, key_points, action_items, participants, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(topic_id) DO UPDATE SET
            summary_text = excluded.summary_text,
            importance = excluded.importance,
            urgency = excluded.urgency,
            last_message_id = excluded.last_message_id,
            key_points = excluded.key_points,
            action_items = excluded.action_items,
            participants = excluded.participants,
            created_at = excluded.created_at
        """,
        (
            topic_id,
            summary_text,
            importance,
            urgency,
            last_message_id,
            key_points,
            action_items,
            participants,
            datetime.now().isoformat(),
        ),
    )

    # Update FTS index
    if existing:
        # Delete old FTS entry and insert new one
        db.execute(
            """
            INSERT INTO summaries_fts(summaries_fts, rowid, summary_text, key_points, action_items)
            VALUES ('delete', ?, ?, ?, ?)
            """,
            (existing["id"], summary_text, key_points or "", action_items or ""),
        )
    # Get the summary id (either new or existing)
    cursor = db.execute("SELECT id FROM summaries WHERE topic_id = ?", (topic_id,))
    summary_id = cursor.fetchone()["id"]
    db.execute(
        """
        INSERT INTO summaries_fts(rowid, summary_text, key_points, action_items)
        VALUES (?, ?, ?, ?)
        """,
        (summary_id, summary_text, key_points or "", action_items or ""),
    )

    db.commit()


def is_summary_stale(topic_id: int) -> bool:
    """Check if a summary needs regeneration."""
    db = get_database()
    cursor = db.execute(
        """
        SELECT s.last_message_id as summary_msg_id, t.last_message_id as topic_msg_id
        FROM summaries s
        JOIN topics t ON s.topic_id = t.id
        WHERE s.topic_id = ?
        """,
        (topic_id,),
    )
    row = cursor.fetchone()
    if not row:
        return True  # No summary exists
    return row["summary_msg_id"] != row["topic_msg_id"]


def get_topics_for_triage(
    site_id: int, unread_only: bool = True
) -> List[Dict[str, Any]]:
    """Get topics with summary data for triage filtering."""
    db = get_database()

    if unread_only:
        cursor = db.execute(
            """
            SELECT DISTINCT
                site.url as site_url,
                s.stream_id as stream_id,
                s.name as stream_name,
                t.name as topic_name,
                t.id as topic_id,
                t.last_message_id as topic_last_msg,
                sum.summary_text,
                sum.importance,
                sum.urgency,
                sum.key_points,
                sum.action_items,
                sum.participants,
                sum.last_message_id as summary_last_msg,
                sum.created_at as summary_created_at,
                COUNT(u.id) as unread_count
            FROM unread_messages u
            JOIN streams s ON u.stream_name = s.name AND s.site_id = ?
            JOIN sites site ON site.id = s.site_id
            JOIN topics t ON t.stream_id = s.id AND t.name = u.topic_name
            LEFT JOIN summaries sum ON sum.topic_id = t.id
            WHERE u.site_id = ?
            GROUP BY t.id
            ORDER BY sum.importance DESC, sum.urgency DESC, t.last_message_id DESC
            """,
            (site_id, site_id),
        )
    else:
        cursor = db.execute(
            """
            SELECT
                site.url as site_url,
                s.stream_id as stream_id,
                s.name as stream_name,
                t.name as topic_name,
                t.id as topic_id,
                t.last_message_id as topic_last_msg,
                sum.summary_text,
                sum.importance,
                sum.urgency,
                sum.key_points,
                sum.action_items,
                sum.participants,
                sum.last_message_id as summary_last_msg,
                sum.created_at as summary_created_at,
                (SELECT COUNT(*) FROM unread_messages u
                 WHERE u.site_id = ? AND u.stream_name = s.name AND u.topic_name = t.name) as unread_count
            FROM topics t
            JOIN streams s ON t.stream_id = s.id
            JOIN sites site ON site.id = s.site_id
            LEFT JOIN summaries sum ON sum.topic_id = t.id
            WHERE s.site_id = ?
            ORDER BY sum.importance DESC, sum.urgency DESC, t.last_message_id DESC
            """,
            (site_id, site_id),
        )

    return [dict(row) for row in cursor]


# Full-text search operations
def search_threads(
    query: str,
    site_id: Optional[int] = None,
    stream_name: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Full-text search returning complete threads.

    Searches both message content AND AI-generated summaries.

    Args:
        query: FTS5 query string (supports AND, OR, NOT, phrases, prefix*)
        site_id: Optional filter by site
        stream_name: Optional filter by stream
        limit: Max number of threads to return

    Returns:
        List of thread dicts, each containing:
        - site_name, site_url, stream_name, stream_id, topic_name
        - url (Zulip link)
        - messages (all messages in thread, ordered by timestamp)
        - matched_message_ids (which messages hit the query)
        - matched_in_summary (True if summary matched)
        - summary/importance/urgency (if available)
    """
    from urllib.parse import quote

    db = get_database()

    # Build the WHERE clause for filters
    where_parts = []
    params: List[Any] = []

    if site_id is not None:
        where_parts.append("s.site_id = ?")
        params.append(site_id)

    if stream_name is not None:
        where_parts.append("s.name = ?")
        params.append(stream_name)

    where_clause = " AND ".join(where_parts) if where_parts else "1=1"

    # Search both messages and summaries, union the results
    matching_query = f"""
        WITH
        -- Messages matching the query
        msg_fts_matches AS (
            SELECT rowid as message_db_id
            FROM messages_fts
            WHERE messages_fts MATCH ?
        ),
        msg_matching AS (
            SELECT
                m.id as message_id,
                m.topic_id,
                0 as from_summary
            FROM msg_fts_matches fm
            JOIN messages m ON m.id = fm.message_db_id
            JOIN topics t ON t.id = m.topic_id
            JOIN streams s ON s.id = t.stream_id
            WHERE {where_clause}
        ),
        -- Summaries matching the query
        sum_fts_matches AS (
            SELECT rowid as summary_db_id
            FROM summaries_fts
            WHERE summaries_fts MATCH ?
        ),
        sum_matching AS (
            SELECT
                NULL as message_id,
                sum.topic_id,
                1 as from_summary
            FROM sum_fts_matches sfm
            JOIN summaries sum ON sum.id = sfm.summary_db_id
            JOIN topics t ON t.id = sum.topic_id
            JOIN streams s ON s.id = t.stream_id
            WHERE {where_clause}
        ),
        -- Combined matches
        all_matches AS (
            SELECT message_id, topic_id, from_summary FROM msg_matching
            UNION ALL
            SELECT message_id, topic_id, from_summary FROM sum_matching
        ),
        -- Aggregate by topic
        matching_topics AS (
            SELECT
                topic_id,
                COUNT(CASE WHEN message_id IS NOT NULL THEN 1 END) as msg_match_count,
                MAX(from_summary) as matched_summary,
                GROUP_CONCAT(CASE WHEN message_id IS NOT NULL THEN message_id END) as matched_ids
            FROM all_matches
            GROUP BY topic_id
            ORDER BY msg_match_count DESC, matched_summary DESC
            LIMIT ?
        )
        SELECT
            site.name as site_name,
            site.url as site_url,
            s.stream_id,
            s.name as stream_name,
            t.id as topic_id,
            t.name as topic_name,
            mt.matched_ids,
            mt.msg_match_count,
            mt.matched_summary,
            sum.summary_text,
            sum.importance,
            sum.urgency
        FROM matching_topics mt
        JOIN topics t ON t.id = mt.topic_id
        JOIN streams s ON s.id = t.stream_id
        JOIN sites site ON site.id = s.site_id
        LEFT JOIN summaries sum ON sum.topic_id = t.id
        ORDER BY mt.msg_match_count DESC, mt.matched_summary DESC
    """

    # Query uses the search term twice (messages + summaries) plus params twice
    cursor = db.execute(matching_query, [query] + params + [query] + params + [limit])
    topic_rows = list(cursor)

    if not topic_rows:
        return []

    # Phase 2: Get all messages for matching topics
    results = []
    for row in topic_rows:
        topic_id = row["topic_id"]
        matched_ids_str = row["matched_ids"]
        matched_ids = set(int(x) for x in matched_ids_str.split(",")) if matched_ids_str else set()

        # Get all messages in this thread
        msg_cursor = db.execute(
            """
            SELECT
                message_id,
                sender_name,
                sender_email,
                content_text,
                content_markdown,
                timestamp
            FROM messages
            WHERE topic_id = ?
            ORDER BY timestamp ASC
            """,
            (topic_id,),
        )

        messages = []
        for msg in msg_cursor:
            messages.append({
                "id": msg["message_id"],
                "sender": msg["sender_name"],
                "sender_email": msg["sender_email"],
                "content": msg["content_markdown"] or msg["content_text"],
                "timestamp": msg["timestamp"],
                "matched": msg["message_id"] in matched_ids,
            })

        # Build Zulip URL
        stream_encoded = quote(row["stream_name"], safe="")
        topic_encoded = quote(row["topic_name"], safe="")
        url = f"{row['site_url']}/#narrow/stream/{row['stream_id']}-{stream_encoded}/topic/{topic_encoded}"

        results.append({
            "site": row["site_name"],
            "site_url": row["site_url"],
            "stream": row["stream_name"],
            "stream_id": row["stream_id"],
            "topic": row["topic_name"],
            "url": url,
            "message_count": len(messages),
            "matched_message_ids": list(matched_ids),
            "matched_in_summary": bool(row["matched_summary"]),
            "summary": row["summary_text"],
            "importance": row["importance"],
            "urgency": row["urgency"],
            "messages": messages,
        })

    return results


def rebuild_fts_index() -> Dict[str, int]:
    """Rebuild FTS indexes from scratch. Returns counts of indexed items."""
    db = get_database()

    # Rebuild messages FTS
    db.execute("DROP TABLE IF EXISTS messages_fts")
    db.execute("""
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content_text,
            sender_name,
            content='messages',
            content_rowid='id'
        )
    """)
    db.execute("""
        INSERT INTO messages_fts(rowid, content_text, sender_name)
        SELECT id, content_text, sender_name FROM messages
    """)

    # Rebuild summaries FTS
    db.execute("DROP TABLE IF EXISTS summaries_fts")
    db.execute("""
        CREATE VIRTUAL TABLE summaries_fts USING fts5(
            summary_text,
            key_points,
            action_items,
            content='summaries',
            content_rowid='id'
        )
    """)
    db.execute("""
        INSERT INTO summaries_fts(rowid, summary_text, key_points, action_items)
        SELECT id, summary_text, COALESCE(key_points, ''), COALESCE(action_items, '')
        FROM summaries
    """)

    db.commit()

    # Return counts
    msg_count = db.execute("SELECT COUNT(*) as count FROM messages_fts").fetchone()["count"]
    sum_count = db.execute("SELECT COUNT(*) as count FROM summaries_fts").fetchone()["count"]
    return {"messages": msg_count, "summaries": sum_count}
