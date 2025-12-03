#!/usr/bin/env python3
"""Command-line interface for zulip-client."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, NoReturn, Optional, Tuple

from .api import ZulipClient
from .credentials import get_default_site, list_sites
from .database import (
    close_database,
    get_or_create_site,
    clear_unread_messages,
    insert_unread_messages,
    get_unread_summary,
    get_total_unread_count,
    get_unread_topics,
    get_or_create_stream,
    get_or_create_topic,
    get_topic_last_message_id,
    update_topic_last_message_id,
    insert_messages,
    update_site_last_sync,
    get_topic_messages,
    get_site_id,
    get_all_streams,
    get_topics_for_stream,
    get_database,
    get_channels_summary,
    get_topics_summary,
    get_topic_messages_with_unread,
    get_stream_by_name,
    topic_has_messages,
    get_sync_mine_state,
    update_sync_mine_state,
    get_sync_all_state,
    update_sync_all_state,
    validate_sync_all_state,
    get_message_count_for_site,
    get_max_message_id_for_site,
    get_topic_by_names,
    get_summary,
    save_summary,
    is_summary_stale,
    get_topics_for_triage,
    mark_topic_as_read,
    search_threads,
    rebuild_fts_index,
)
from .export import export_topic_to_json, export_topic_to_markdown
from .summarize import generate_summary, DEFAULT_MODEL

# Path to quota-checking script
CLAUDE_AVAILABLE_MODEL_SCRIPT = Path.home() / ".claude/skills/claude-usage/claude-available-model"


def _get_available_model() -> Optional[str]:
    """Get available Claude model based on quota, or None if exhausted."""
    import subprocess
    if not CLAUDE_AVAILABLE_MODEL_SCRIPT.exists():
        return None
    result = subprocess.run(
        [str(CLAUDE_AVAILABLE_MODEL_SCRIPT), "--verbose"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def cmd_unread(args: argparse.Namespace) -> None:
    """Show unread message summary."""
    sites = list_sites() if args.all else [args.site or get_default_site()]

    for site_name in sites:
        _show_unread_for_site(site_name)
        if len(sites) > 1:
            print()


def _show_unread_for_site(site_name: str) -> None:
    """Show unread messages for a single site."""
    client = ZulipClient(site_name)

    print(f"Fetching unread messages from {client.site_url}...")

    data = client.register()
    unread_msgs = data["unread_msgs"]
    subscriptions = data["subscriptions"]

    # Build stream ID to name map
    stream_map = {sub["stream_id"]: sub["name"] for sub in subscriptions}

    # Store site and unread state in database
    site_id = get_or_create_site(site_name, client.site_url)

    # Update unread messages in database
    clear_unread_messages(site_id)
    insert_unread_messages(site_id, unread_msgs.get("streams", []), stream_map)

    # Display summary
    total_count = get_total_unread_count(site_id)
    summary = get_unread_summary(site_id)

    print()
    print(f"{client.site_url} - {total_count} unread messages")
    print()

    if not summary:
        print("  No unread messages in streams.")
        return

    for stream in summary:
        print(f"#{stream['stream_name']} ({stream['total_count']} unread)")
        for topic in stream["topics"]:
            print(f"  └─ {topic['topic_name']} ({topic['count']})")

    # Also mention DMs and mentions if present
    pms = unread_msgs.get("pms", [])
    if pms:
        pm_count = sum(len(pm.get("unread_message_ids", [])) for pm in pms)
        print()
        print(f"Direct messages: {pm_count} unread")

    mentions = unread_msgs.get("mentions", [])
    if mentions:
        print()
        print(f"Mentions: {len(mentions)} unread")


def cmd_sync(args: argparse.Namespace) -> None:
    """Download threads based on mode flags."""
    do_all = getattr(args, 'sync_all', False)
    do_unread = args.unread
    do_mine = args.mine
    do_full = getattr(args, 'full', False)

    # Default to --all if no mode specified
    if not do_all and not do_unread and not do_mine:
        do_all = True

    sites = list_sites() if args.all_sites else [args.site or get_default_site()]

    for site_name in sites:
        if do_all:
            _sync_all(site_name, args.verbose, args.limit, force_full=do_full)
        else:
            if do_unread:
                _sync_unread(site_name, args.verbose, args.limit)
            if do_mine:
                if do_unread:
                    print()  # Separator between modes
                _sync_mine(site_name, args.verbose, args.limit)
        if len(sites) > 1:
            print()


def _sync_all(site_name: str, verbose: bool, limit: Optional[int] = None, force_full: bool = False) -> None:
    """Sync all topics from all subscribed streams.

    Uses incremental mode when possible:
    - If we have a valid sync state (high water mark + message count), fetch only new messages
    - Otherwise (or if --full), do a full stream-by-stream sync
    """
    client = ZulipClient(site_name)

    print(f"Syncing all from {client.site_url}...", flush=True)

    # Check for incremental mode FIRST (before expensive register() call)
    site_id = get_site_id(site_name)
    if site_id and not force_full and not limit:
        sync_state = get_sync_all_state(site_id)
        if sync_state and validate_sync_all_state(site_id):
            # Fast incremental mode - skip register() entirely
            total_new = _sync_all_incremental(
                client, site_id, sync_state["last_synced_message_id"], verbose
            )
            update_site_last_sync(site_id)

            # Update sync state with new high water mark
            new_max_id = get_max_message_id_for_site(site_id)
            if new_max_id:
                new_count = get_message_count_for_site(site_id, new_max_id)
                update_sync_all_state(site_id, new_max_id, new_count)

            print(f"Incremental sync complete. {total_new} new messages.", flush=True)
            return
        elif sync_state:
            # Validation failed - warn and fall through to full sync
            print("Warning: Message count mismatch detected. Performing full resync.", flush=True)

    # Full sync mode - need register() for subscriptions and unread state
    data = client.register()
    subscriptions = data["subscriptions"]

    # Filter to non-muted streams
    streams = [s for s in subscriptions if not s.get("is_muted")]

    if not streams:
        print("No subscribed streams found.")
        return

    # Store site (may already exist)
    site_id = get_or_create_site(site_name, client.site_url)

    # Update unread state
    unread_msgs = data["unread_msgs"]
    stream_map = {sub["stream_id"]: sub["name"] for sub in subscriptions}
    clear_unread_messages(site_id)
    insert_unread_messages(site_id, unread_msgs.get("streams", []), stream_map)

    # Full sync mode
    print(f"Found {len(streams)} subscribed streams.", flush=True)

    total_new_messages = 0
    topics_synced = 0
    topic_count = 0

    for stream in streams:
        stream_name = stream["name"]
        stream_id = stream["stream_id"]

        if verbose:
            print(f"\n#{stream_name}...", flush=True)

        # Get all topics in this stream
        try:
            topics = client.get_stream_topics(stream_id)
        except Exception as e:
            if verbose:
                print(f"  Error getting topics: {e}", flush=True)
            continue

        if not topics:
            continue

        topic_count += len(topics)

        if limit and topics_synced >= limit:
            break

        # Get or create stream in database
        stream_db_id = get_or_create_stream(site_id, stream_id, stream_name)

        for topic in topics:
            if limit and topics_synced >= limit:
                break

            topic_name = topic["name"]

            # Get or create topic in database
            topic_db_id = get_or_create_topic(stream_db_id, topic_name)

            # Get the last message ID we have for incremental sync
            last_message_id = get_topic_last_message_id(topic_db_id)

            # Short-circuit: if topic's max_id matches what we have, skip entirely
            # topic["max_id"] is the newest message ID in this topic from Zulip
            if last_message_id and topic.get("max_id") and last_message_id >= topic["max_id"]:
                continue  # Already up to date, skip silently

            topics_synced += 1

            if verbose:
                print(f"  [{topics_synced}] {topic_name}...", end=" ", flush=True)

            # Fetch messages from Zulip
            messages = client.get_topic_messages(
                stream_name,
                topic_name,
                after_message_id=last_message_id,
                verbose=False,
            )

            if messages:
                # Insert messages into database
                inserted = insert_messages(topic_db_id, messages)
                total_new_messages += inserted

                # Update last message ID
                max_message_id = max(m["id"] for m in messages)
                update_topic_last_message_id(topic_db_id, max_message_id)

                if verbose:
                    print(f"{inserted} new", flush=True)
            elif verbose:
                print("up to date", flush=True)

    update_site_last_sync(site_id)

    # Update sync state for future incremental syncs
    max_id = get_max_message_id_for_site(site_id)
    if max_id:
        count = get_message_count_for_site(site_id, max_id)
        update_sync_all_state(site_id, max_id, count)
        if verbose:
            print(f"Sync state saved: message_id={max_id}, count={count}", flush=True)

    print(flush=True)
    print(f"Full sync complete. {total_new_messages} new messages from {topics_synced} topics.", flush=True)


def _sync_all_incremental(
    client: ZulipClient,
    site_id: int,
    after_message_id: int,
    verbose: bool,
) -> int:
    """Incremental sync: fetch all new messages across all streams in one call.

    Returns count of new messages inserted.
    """
    # Quick check: is there anything new at all?
    newest_id = client.get_newest_message_id()
    if newest_id is None or newest_id <= after_message_id:
        if verbose:
            print("No new messages.", flush=True)
        return 0

    if verbose:
        print(f"Incremental sync from message ID {after_message_id}...", flush=True)

    # Fetch all new stream messages in one paginated call
    messages = client.get_all_messages_after(after_message_id, verbose=verbose)

    if not messages:
        return 0

    # Group messages by stream/topic for insertion
    # Each message has: stream_id, display_recipient (stream name), subject (topic name)
    by_topic: Dict[Tuple[int, str, str], List[Dict]] = {}
    for msg in messages:
        if msg.get("type") != "stream":
            continue
        key = (msg["stream_id"], msg["display_recipient"], msg["subject"])
        if key not in by_topic:
            by_topic[key] = []
        by_topic[key].append(msg)

    total_inserted = 0

    for (stream_id, stream_name, topic_name), topic_messages in by_topic.items():
        # Get or create stream and topic
        stream_db_id = get_or_create_stream(site_id, stream_id, stream_name)
        topic_db_id = get_or_create_topic(stream_db_id, topic_name)

        # Insert messages
        inserted = insert_messages(topic_db_id, topic_messages)
        total_inserted += inserted

        # Update topic's last_message_id
        max_msg_id = max(m["id"] for m in topic_messages)
        current_last = get_topic_last_message_id(topic_db_id)
        if current_last is None or max_msg_id > current_last:
            update_topic_last_message_id(topic_db_id, max_msg_id)

        if verbose and inserted > 0:
            print(f"  #{stream_name} > {topic_name}: {inserted} new", flush=True)

    return total_inserted


def _sync_topics(
    client: ZulipClient,
    site_id: int,
    site_name: str,
    topics: List[Dict],
    *,
    incremental: bool = True,
    export_json: bool = False,
    unread_ids_by_topic: Optional[Dict[str, List[int]]] = None,
    verbose: bool = False,
) -> Tuple[int, int]:
    """Sync a list of topics from Zulip to the database.

    Args:
        client: ZulipClient instance
        site_id: Database site ID
        site_name: Site name for export paths
        topics: List of topic dicts with stream_id, stream_name, topic_name
        incremental: If True, only fetch messages after last_message_id
        export_json: If True, export synced topics to JSON
        unread_ids_by_topic: Dict mapping "stream:topic" to unread message IDs (for export)
        verbose: Show progress output

    Returns:
        (topics_synced, total_new_messages)
    """
    total_new_messages = 0
    topics_synced = 0

    for topic in topics:
        stream_name = topic["stream_name"]
        topic_name = topic["topic_name"]
        topics_synced += 1

        # Get or create stream and topic in database
        stream_db_id = get_or_create_stream(site_id, topic["stream_id"], stream_name)
        topic_db_id = get_or_create_topic(stream_db_id, topic_name)

        # Get the last message ID for incremental sync
        last_message_id = get_topic_last_message_id(topic_db_id) if incremental else None

        # Short-circuit for incremental sync: skip if already up to date
        if incremental and last_message_id:
            # Check if we have unread IDs to compare against
            message_ids = topic.get("message_ids", [])
            if message_ids:
                max_unread_id = max(message_ids)
                if max_unread_id <= last_message_id:
                    if verbose:
                        print(f"[{topics_synced}/{len(topics)}] #{stream_name} > {topic_name}... up to date", flush=True)
                    continue

        if verbose:
            print(f"[{topics_synced}/{len(topics)}] #{stream_name} > {topic_name}...", flush=True)

        # Fetch messages from Zulip
        messages = client.get_topic_messages(
            stream_name,
            topic_name,
            after_message_id=last_message_id,
            verbose=verbose,
        )

        if messages:
            # Insert messages into database
            inserted = insert_messages(topic_db_id, messages)
            total_new_messages += inserted

            # Update last message ID
            max_message_id = max(m["id"] for m in messages)
            update_topic_last_message_id(topic_db_id, max_message_id)

            if verbose:
                print(f"  Stored {inserted} new messages ({len(messages)} fetched)", flush=True)

            # Export to JSON if requested
            if export_json:
                all_stored_messages = get_topic_messages(site_id, stream_name, topic_name)
                key = f"{stream_name}:{topic_name}"
                unread_ids = unread_ids_by_topic.get(key, []) if unread_ids_by_topic else []
                export_path = export_topic_to_json(
                    site_name,
                    stream_name,
                    topic_name,
                    all_stored_messages,
                    unread_ids,
                )
                if verbose:
                    print(f"  Exported to {export_path}", flush=True)
        elif verbose:
            print("  No new messages", flush=True)

    return topics_synced, total_new_messages


def _sync_unread(site_name: str, verbose: bool, limit: Optional[int] = None) -> None:
    """Sync threads with unread messages."""
    client = ZulipClient(site_name)

    print(f"Syncing unread from {client.site_url}...", flush=True)

    data = client.register()
    unread_msgs = data["unread_msgs"]
    subscriptions = data["subscriptions"]

    # Build stream ID to name map
    stream_map = {sub["stream_id"]: sub["name"] for sub in subscriptions}

    # Store site and unread state in database
    site_id = get_or_create_site(site_name, client.site_url)

    # Update unread messages in database
    clear_unread_messages(site_id)
    insert_unread_messages(site_id, unread_msgs.get("streams", []), stream_map)

    # Get unique topics with unread messages
    unread_topics = get_unread_topics(site_id)

    if not unread_topics:
        print("No unread messages to sync.")
        update_site_last_sync(site_id)
        return

    if limit:
        print(f"Found {len(unread_topics)} topics with unread messages (limiting to {limit}).", flush=True)
        unread_topics = unread_topics[:limit]
    else:
        print(f"Found {len(unread_topics)} topics with unread messages.", flush=True)
    print(flush=True)

    # Build unread IDs lookup for export
    unread_ids_by_topic = {
        f"{t['stream_name']}:{t['topic_name']}": t["message_ids"]
        for t in unread_topics
    }

    topics_synced, total_new_messages = _sync_topics(
        client,
        site_id,
        site_name,
        unread_topics,
        incremental=True,
        export_json=True,
        unread_ids_by_topic=unread_ids_by_topic,
        verbose=verbose,
    )

    update_site_last_sync(site_id)

    print(flush=True)
    print(f"Unread sync complete. {total_new_messages} new messages stored.", flush=True)


def _sync_mine(site_name: str, verbose: bool, limit: Optional[int] = None) -> None:
    """Sync topics I've participated in.

    Uses incremental scanning with early stopping:
    1. Scan from saved checkpoint (or newest), stop when we have enough un-synced topics
    2. Save new checkpoint so next run continues from where we left off
    """
    client = ZulipClient(site_name)

    print(f"Syncing my topics from {client.site_url}...", flush=True)

    # Get site ID (create if needed)
    site_id = get_or_create_site(site_name, client.site_url)

    # Get saved scan state
    saved_oldest_id = get_sync_mine_state(site_id)

    topics_to_sync: List[Dict] = []

    # Callback that collects un-synced topics and stops when we have enough
    def check_topic(topic_info: Dict) -> bool:
        """Return True to continue scanning, False to stop."""
        if not topic_has_messages(site_id, topic_info["stream_name"], topic_info["topic_name"]):
            topics_to_sync.append(topic_info)
            if verbose:
                print(f"  Found: #{topic_info['stream_name']} > {topic_info['topic_name']}", flush=True)
            if limit and len(topics_to_sync) >= limit:
                return False  # Stop scanning
        return True  # Continue

    if verbose:
        if saved_oldest_id:
            print(f"Continuing scan from previous checkpoint...", flush=True)
        else:
            print(f"Scanning your message history...", flush=True)

    # Scan from saved checkpoint going older (or from newest if first run)
    start = saved_oldest_id - 1 if saved_oldest_id else "newest"
    _, oldest_scanned, reached_end = client.scan_my_topics(
        start_anchor=start,
        stop_at_message_id=None,
        needed_callback=check_topic,
        verbose=False,  # We handle verbose output in callback
    )

    # Update saved state if we scanned further
    if oldest_scanned:
        update_sync_mine_state(site_id, oldest_scanned)

    if not topics_to_sync:
        status = "All my topics synced." if reached_end else "No new un-synced topics found."
        print(status, flush=True)
        return

    print(f"Found {len(topics_to_sync)} topics to sync.", flush=True)
    print(flush=True)

    topics_synced, total_new_messages = _sync_topics(
        client,
        site_id,
        site_name,
        topics_to_sync,
        incremental=False,  # Fetch all messages for new topics
        export_json=False,
        verbose=verbose,
    )

    update_site_last_sync(site_id)

    print(flush=True)
    print(f"My topics sync complete. {total_new_messages} new messages from {topics_synced} topics.", flush=True)


def cmd_export(args: argparse.Namespace) -> None:
    """Export stored messages."""
    site_name = args.site or get_default_site()
    format_type = args.format or "json"

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    # Get unread message IDs for this site
    unread_topics = get_unread_topics(site_id)
    unread_by_topic: Dict[str, List[int]] = {}
    for t in unread_topics:
        key = f"{t['stream_name']}:{t['topic_name']}"
        unread_by_topic[key] = t["message_ids"]

    db = get_database()

    if args.stream and args.topic:
        # Export single topic
        _export_single_topic(
            site_name, site_id, args.stream, args.topic, format_type, unread_by_topic
        )
    elif args.stream:
        # Export all topics in a stream
        cursor = db.execute(
            "SELECT id, name FROM streams WHERE site_id = ? AND name = ?",
            (site_id, args.stream),
        )
        stream_row = cursor.fetchone()

        if not stream_row:
            print(f"Stream '{args.stream}' not found. Available streams:", file=sys.stderr)
            for s in get_all_streams(site_id):
                print(f"  - {s['name']}", file=sys.stderr)
            sys.exit(1)

        topics = get_topics_for_stream(stream_row["id"])
        print(f"Exporting {len(topics)} topics from #{args.stream}...")

        for topic in topics:
            _export_single_topic(
                site_name, site_id, args.stream, topic["name"], format_type, unread_by_topic
            )
    else:
        # Export everything
        streams = get_all_streams(site_id)

        if not streams:
            print("No streams found. Run sync first.", file=sys.stderr)
            sys.exit(1)

        print(f"Exporting all stored messages for {site_name}...")

        total_topics = 0
        for stream in streams:
            cursor = db.execute(
                "SELECT id FROM streams WHERE site_id = ? AND stream_id = ?",
                (site_id, stream["stream_id"]),
            )
            stream_row = cursor.fetchone()
            topics = get_topics_for_stream(stream_row["id"])
            total_topics += len(topics)

            for topic in topics:
                _export_single_topic(
                    site_name, site_id, stream["name"], topic["name"], format_type, unread_by_topic
                )

        print(f"Exported {total_topics} topics.")


def _export_single_topic(
    site_name: str,
    site_id: int,
    stream_name: str,
    topic_name: str,
    format_type: str,
    unread_by_topic: Dict[str, List[int]],
) -> None:
    """Export a single topic."""
    messages = get_topic_messages(site_id, stream_name, topic_name)

    if not messages:
        print(f"  Skipping empty topic: #{stream_name} > {topic_name}")
        return

    key = f"{stream_name}:{topic_name}"
    unread_ids = unread_by_topic.get(key, [])

    if format_type == "markdown":
        filepath = export_topic_to_markdown(site_name, stream_name, topic_name, messages, unread_ids)
    else:
        filepath = export_topic_to_json(site_name, stream_name, topic_name, messages, unread_ids)

    print(f"  Exported: #{stream_name} > {topic_name} ({len(messages)} messages) -> {filepath}")


def cmd_channels(args: argparse.Namespace) -> None:
    """List downloaded channels with topic and message counts."""
    site_name = args.site or get_default_site()

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    channels = get_channels_summary(site_id)

    if not channels:
        print(f"No channels downloaded for {site_name}. Run 'sync' first.")
        return

    print(f"Downloaded channels for {site_name}:")
    print()

    for ch in channels:
        unread_str = f" ({ch['unread_count']} unread)" if ch["unread_count"] > 0 else ""
        print(f"#{ch['stream_name']}{unread_str}")
        print(f"  {ch['topic_count']} topics, {ch['message_count']} messages")


def cmd_topics(args: argparse.Namespace) -> None:
    """List topics in a channel with message counts."""
    site_name = args.site or get_default_site()
    stream_name = args.stream

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    stream = get_stream_by_name(site_id, stream_name)
    if stream is None:
        print(f"Channel '{stream_name}' not found. Available channels:", file=sys.stderr)
        for ch in get_channels_summary(site_id):
            print(f"  - {ch['stream_name']}", file=sys.stderr)
        sys.exit(1)

    topics = get_topics_summary(site_id, stream_name)

    if not topics:
        print(f"No topics downloaded for #{stream_name}.")
        return

    print(f"#{stream_name} - {len(topics)} topics:")
    print()

    for topic in topics:
        unread_str = f" ({topic['unread_count']} unread)" if topic["unread_count"] > 0 else ""
        print(f"  {topic['topic_name']}{unread_str}")
        print(f"    {topic['message_count']} messages")


def cmd_messages(args: argparse.Namespace) -> None:
    """Show messages in a topic with read status."""
    from datetime import datetime

    site_name = args.site or get_default_site()
    stream_name = args.stream
    topic_name = args.topic

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    messages = get_topic_messages_with_unread(site_id, stream_name, topic_name)

    if not messages:
        print(f"No messages found for #{stream_name} > {topic_name}.", file=sys.stderr)
        print("Either the topic doesn't exist or hasn't been synced.", file=sys.stderr)
        sys.exit(1)

    unread_count = sum(1 for m in messages if m["is_unread"])
    print(f"#{stream_name} > {topic_name}")
    print(f"{len(messages)} messages ({unread_count} unread)")
    print()
    print("=" * 60)

    for msg in messages:
        status = "[UNREAD] " if msg["is_unread"] else ""
        timestamp = datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M")
        print()
        print(f"{status}{msg['sender_name']} ({timestamp})")
        print("-" * 40)
        # Prefer markdown content, fall back to stripped HTML for old messages
        content = msg.get("content_markdown") or msg["content_text"]
        print(content)
        print()


def cmd_summary(args: argparse.Namespace) -> None:
    """Show or generate AI summary for a thread, channel, or all."""
    site_name = args.site or get_default_site()
    stream_name = getattr(args, 'stream', None)
    topic_name = getattr(args, 'topic', None)
    model = args.model

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    # No stream = iterate over everything missing summaries
    if not stream_name:
        _summary_all(site_id, args)
        return

    # No topic = iterate over channel
    if not topic_name:
        _summary_channel(site_id, stream_name, args)
        return

    # Single topic mode
    _summary_single(site_id, stream_name, topic_name, model, args.force, no_generate=False)


def _summary_single(
    site_id: int,
    stream_name: str,
    topic_name: str,
    explicit_model: Optional[str],
    force: bool,
    no_generate: bool,
) -> bool:
    """Generate/show summary for a single topic. Returns True if successful."""
    import json as json_module

    topic_info = get_topic_by_names(site_id, stream_name, topic_name)
    if topic_info is None:
        print(f"Topic not found: #{stream_name} > {topic_name}", file=sys.stderr)
        return False

    topic_id = topic_info["id"]
    topic_last_msg = topic_info["last_message_id"]

    existing = get_summary(topic_id)
    stale = is_summary_stale(topic_id) if existing else True

    # If we have a fresh summary and not forcing, just show it
    if existing and not stale and not force:
        _display_summary(stream_name, topic_name, existing, topic_last_msg)
        return True

    # If --no-generate, show cached only
    if no_generate:
        if existing:
            if stale:
                print("[Summary is stale - new messages since generation]")
                print()
            _display_summary(stream_name, topic_name, existing, topic_last_msg)
        else:
            print(f"No summary cached for #{stream_name} > {topic_name}")
        return True

    # Check quota before generating (unless --model explicitly set)
    if explicit_model:
        model = explicit_model
    else:
        model = _get_available_model()
        if model is None:
            print("Quota exhausted. Use --model to override.", file=sys.stderr)
            if existing:
                print()
                print("Showing cached summary:")
                _display_summary(stream_name, topic_name, existing, topic_last_msg)
            return False

    # Generate new summary
    messages = get_topic_messages(site_id, stream_name, topic_name)
    if not messages:
        print(f"No messages found for #{stream_name} > {topic_name}.", file=sys.stderr)
        return False

    if force:
        status = "Regenerating summary"
    elif stale and existing:
        status = "Updating stale summary"
    else:
        status = "Generating summary"
    print(f"{status} for #{stream_name} > {topic_name} ({len(messages)} messages)...")
    print()

    try:
        result = generate_summary(messages, model=model)
    except Exception as e:
        print(f"Error generating summary: {e}", file=sys.stderr)
        if existing:
            print()
            print("Showing cached summary:")
            _display_summary(stream_name, topic_name, existing, topic_last_msg)
        return False

    save_summary(
        topic_id=topic_id,
        summary_text=result["summary"],
        importance=result["importance"],
        urgency=result["urgency"],
        last_message_id=topic_last_msg,
        key_points=json_module.dumps(result.get("key_points", [])),
        action_items=json_module.dumps(result.get("action_items", [])),
        participants=json_module.dumps(result.get("participants", [])),
    )

    summary_data = get_summary(topic_id)
    _display_summary(stream_name, topic_name, summary_data, topic_last_msg)
    return True


def _summary_channel(site_id: int, stream_name: str, args: argparse.Namespace) -> None:
    """Generate summaries for topics in a channel that don't have them."""
    import json as json_module

    stream = get_stream_by_name(site_id, stream_name)
    if stream is None:
        print(f"Channel '{stream_name}' not found.", file=sys.stderr)
        sys.exit(1)

    topics = get_topics_summary(site_id, stream_name)
    if not topics:
        print(f"No topics found in #{stream_name}.")
        return

    # Filter to topics without summaries (or all if --force)
    to_process = []
    for t in topics:
        topic_info = get_topic_by_names(site_id, stream_name, t["topic_name"])
        if topic_info is None:
            continue
        existing = get_summary(topic_info["id"])

        # Only process topics without summaries, unless --force
        if existing and not args.force:
            continue

        to_process.append((stream_name, t["topic_name"], topic_info))

    if not to_process:
        print(f"All {len(topics)} topics in #{stream_name} already have summaries.")
        return

    # Sort by most recent first (higher last_message_id = more recent)
    to_process.sort(key=lambda x: x[2]["last_message_id"], reverse=True)

    _generate_batch(site_id, to_process, args)


def _summary_all(site_id: int, args: argparse.Namespace) -> None:
    """Generate summaries for all downloaded topics that don't have them."""
    import json as json_module

    channels = get_channels_summary(site_id)
    if not channels:
        print("No channels downloaded. Run 'sync' first.")
        return

    # Collect all topics without summaries
    to_process = []
    for ch in channels:
        stream_name = ch["stream_name"]
        topics = get_topics_summary(site_id, stream_name)

        for t in topics:
            topic_info = get_topic_by_names(site_id, stream_name, t["topic_name"])
            if topic_info is None:
                continue
            existing = get_summary(topic_info["id"])

            # Only process topics without summaries, unless --force
            if existing and not args.force:
                continue

            to_process.append((stream_name, t["topic_name"], topic_info))

    if not to_process:
        total = sum(ch["topic_count"] for ch in channels)
        print(f"All {total} downloaded topics already have summaries.")
        return

    # Sort by most recent first (higher last_message_id = more recent)
    to_process.sort(key=lambda x: x[2]["last_message_id"], reverse=True)

    _generate_batch(site_id, to_process, args)


def _generate_batch(
    site_id: int,
    to_process: List[tuple],  # [(stream_name, topic_name, topic_info), ...]
    args: argparse.Namespace,
) -> None:
    """Generate summaries for a batch of topics with quota checking."""
    import json as json_module

    # If --model is explicitly specified by user, skip quota check
    # (We detect this by checking if it was passed on command line vs using default)
    explicit_model = getattr(args, 'model', None)
    skip_quota_check = explicit_model is not None

    print(f"Processing {len(to_process)} topics...")
    print()

    generated = 0
    skipped = 0
    quota_exhausted = False

    for i, (stream_name, topic_name, topic_info) in enumerate(to_process, 1):
        # Check quota before each generation (unless --model explicitly set)
        if skip_quota_check:
            model = explicit_model
        else:
            model = _get_available_model()
            if model is None:
                print(f"\nQuota exhausted after {generated} summaries.")
                quota_exhausted = True
                break

        print(f"[{i}/{len(to_process)}] #{stream_name} > {topic_name}...", end=" ", flush=True)

        topic_id = topic_info["id"]
        topic_last_msg = topic_info["last_message_id"]

        messages = get_topic_messages(site_id, stream_name, topic_name)
        if not messages:
            print("(no messages)")
            skipped += 1
            continue

        try:
            result = generate_summary(messages, model=model)
            save_summary(
                topic_id=topic_id,
                summary_text=result["summary"],
                importance=result["importance"],
                urgency=result["urgency"],
                last_message_id=topic_last_msg,
                key_points=json_module.dumps(result.get("key_points", [])),
                action_items=json_module.dumps(result.get("action_items", [])),
                participants=json_module.dumps(result.get("participants", [])),
            )
            print(f"{result['importance']}/{result['urgency']} ({model})")
            generated += 1
        except Exception as e:
            print(f"error: {e}")
            skipped += 1

    print()
    remaining = len(to_process) - generated - skipped
    status = f"Generated {generated} summaries"
    if skipped:
        status += f", {skipped} skipped"
    if quota_exhausted and remaining > 0:
        status += f", {remaining} remaining (quota exhausted)"
    print(status)


def cmd_triage(args: argparse.Namespace) -> None:
    """Filter threads by AI-classified importance/urgency."""
    import json as json_module
    from datetime import datetime

    site_name = args.site or get_default_site()
    unread_only = not args.all

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    topics = get_topics_for_triage(site_id, unread_only=unread_only)

    if not topics:
        print("No topics found." if not unread_only else "No unread topics found.")
        return

    # Filter by importance/urgency if specified
    importance_filter = args.importance
    urgency_filter = args.urgency

    importance_order = {"high": 3, "medium": 2, "low": 1, None: 0}
    urgency_order = {"high": 3, "medium": 2, "low": 1, None: 0}

    # Generate missing summaries if requested
    if args.generate_missing:
        missing = [t for t in topics if t["summary_text"] is None]
        if args.limit:
            missing = missing[:args.limit]

        # If --model is explicitly specified by user, skip quota check
        explicit_model = args.model

        if missing:
            print(f"Generating summaries for {len(missing)} threads...")
            print()

            generated = 0
            for i, topic in enumerate(missing, 1):
                # Check quota before each generation (unless --model explicitly set)
                if explicit_model:
                    model = explicit_model
                else:
                    model = _get_available_model()
                    if model is None:
                        print(f"\nQuota exhausted after {generated} summaries.")
                        print(f"{len(missing) - i + 1} threads remaining.")
                        break

                stream_name = topic["stream_name"]
                topic_name = topic["topic_name"]
                topic_id = topic["topic_id"]
                topic_last_msg = topic["topic_last_msg"]

                print(f"[{i}/{len(missing)}] #{stream_name} > {topic_name}...", end=" ", flush=True)

                messages = get_topic_messages(site_id, stream_name, topic_name)
                if not messages:
                    print("(no messages)")
                    continue

                try:
                    result = generate_summary(messages, model=model)
                    save_summary(
                        topic_id=topic_id,
                        summary_text=result["summary"],
                        importance=result["importance"],
                        urgency=result["urgency"],
                        last_message_id=topic_last_msg,
                        key_points=json_module.dumps(result.get("key_points", [])),
                        action_items=json_module.dumps(result.get("action_items", [])),
                        participants=json_module.dumps(result.get("participants", [])),
                    )
                    print(f"{result['importance']}/{result['urgency']} ({model})")
                    generated += 1

                    # Update topic data with new summary
                    topic["summary_text"] = result["summary"]
                    topic["importance"] = result["importance"]
                    topic["urgency"] = result["urgency"]
                except Exception as e:
                    print(f"error: {e}")

            print()

    # Apply filters
    filtered = []
    for t in topics:
        imp = t["importance"]
        urg = t["urgency"]

        if importance_filter and importance_order.get(imp, 0) < importance_order.get(importance_filter, 0):
            continue
        if urgency_filter and urgency_order.get(urg, 0) < urgency_order.get(urgency_filter, 0):
            continue

        filtered.append(t)

    # Group by importance + urgency
    high_urgent = [t for t in filtered if t["importance"] == "high" and t["urgency"] == "high"]
    high_other = [t for t in filtered if t["importance"] == "high" and t["urgency"] != "high"]
    medium = [t for t in filtered if t["importance"] == "medium"]
    low = [t for t in filtered if t["importance"] == "low"]
    no_summary = [t for t in filtered if t["importance"] is None]

    total_with_summary = len([t for t in filtered if t["importance"] is not None])
    scope = "unread threads" if unread_only else "threads"
    print(f"Triage: {len(filtered)} {scope} ({total_with_summary} summarized)")
    print("=" * 70)

    def _build_topic_url(t: Dict) -> str:
        from urllib.parse import quote
        stream_name_encoded = quote(t["stream_name"], safe="")
        topic_name_encoded = quote(t["topic_name"], safe="")
        return f"{t['site_url']}/#narrow/stream/{t['stream_id']}-{stream_name_encoded}/topic/{topic_name_encoded}"

    def _print_topic_line(t: Dict) -> None:
        unread = f" [{t['unread_count']} unread]" if t["unread_count"] > 0 else ""
        stale = ""
        if t["summary_last_msg"] and t["topic_last_msg"] and t["summary_last_msg"] != t["topic_last_msg"]:
            stale = " [stale]"
        print(f"#{t['stream_name']} > {t['topic_name']}{unread}{stale}")
        print(f"  {_build_topic_url(t)}")
        if t["summary_text"]:
            # Truncate summary to first sentence or 100 chars
            summary = t["summary_text"]
            if len(summary) > 100:
                summary = summary[:97] + "..."
            print(f"  {summary}")

    if high_urgent:
        print()
        print(f"HIGH IMPORTANCE + HIGH URGENCY ({len(high_urgent)})")
        print("-" * 40)
        for t in high_urgent:
            _print_topic_line(t)

    if high_other:
        print()
        print(f"HIGH IMPORTANCE ({len(high_other)})")
        print("-" * 40)
        for t in high_other:
            _print_topic_line(t)

    if medium:
        print()
        print(f"MEDIUM IMPORTANCE ({len(medium)})")
        print("-" * 40)
        for t in medium:
            _print_topic_line(t)

    if low and (not importance_filter or importance_filter == "low"):
        print()
        print(f"LOW IMPORTANCE ({len(low)})")
        print("-" * 40)
        for t in low:
            _print_topic_line(t)

    if no_summary:
        print()
        print(f"NOT YET SUMMARIZED ({len(no_summary)})")
        print("-" * 40)
        for t in no_summary:
            _print_topic_line(t)

    # Summary counts
    hidden_low = len(low) if importance_filter and importance_filter != "low" else 0
    if hidden_low > 0:
        print()
        print(f"[{hidden_low} low-importance threads hidden]")


def cmd_mark_as_read(args: argparse.Namespace) -> None:
    """Mark a topic as read (on Zulip server and locally)."""
    site_name = args.site or get_default_site()
    stream_name = getattr(args, 'stream', None)
    topic_name = getattr(args, 'topic', None)
    importance_filter = getattr(args, 'importance', None)
    urgency_filter = getattr(args, 'urgency', None)

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    client = ZulipClient(site_name)

    def _mark_single_topic(stream_name: str, topic_name: str, stream_id: int) -> int:
        """Mark a single topic as read on server and locally. Returns local count."""
        # Mark on Zulip server
        client.mark_topic_as_read(stream_id, topic_name)
        # Update local database
        return mark_topic_as_read(site_id, stream_name, topic_name)

    # If stream/topic provided, mark that specific topic
    if stream_name and topic_name:
        stream = get_stream_by_name(site_id, stream_name)
        if stream is None:
            print(f"Stream not found: #{stream_name}", file=sys.stderr)
            sys.exit(1)

        _mark_single_topic(stream_name, topic_name, stream["stream_id"])
        print(f"Marked as read: #{stream_name} > {topic_name}")
        return

    # Otherwise, use importance/urgency filters
    if not importance_filter and not urgency_filter:
        print("Either stream/topic or --importance/--urgency filter required.", file=sys.stderr)
        sys.exit(1)

    # Get all unread topics with summaries
    topics = get_topics_for_triage(site_id, unread_only=True)

    importance_order = {"high": 3, "medium": 2, "low": 1}
    urgency_order = {"high": 3, "medium": 2, "low": 1}

    # Filter to topics matching criteria (must have summary)
    matching = []
    for t in topics:
        imp = t["importance"]
        urg = t["urgency"]

        # Skip topics without summaries
        if imp is None:
            continue

        # Filter by importance (at or below threshold)
        if importance_filter:
            if importance_order.get(imp, 0) > importance_order.get(importance_filter, 0):
                continue

        # Filter by urgency (at or below threshold)
        if urgency_filter:
            if urgency_order.get(urg, 0) > urgency_order.get(urgency_filter, 0):
                continue

        matching.append(t)

    if not matching:
        print("No matching unread topics found.")
        return

    for t in matching:
        _mark_single_topic(t["stream_name"], t["topic_name"], t["stream_id"])
        print(f"Marked as read: #{t['stream_name']} > {t['topic_name']}")

    print(f"\nTotal: {len(matching)} topics marked as read.")


def _display_summary(
    stream_name: str,
    topic_name: str,
    summary: Dict,
    current_last_msg: Optional[int],
) -> None:
    """Display a formatted summary."""
    import json as json_module
    from datetime import datetime

    print(f"#{stream_name} > {topic_name}")
    print("=" * 70)

    imp = summary["importance"].upper()
    urg = summary["urgency"].upper()
    print(f"IMPORTANCE: {imp}  |  URGENCY: {urg}")

    created = summary.get("created_at", "")
    if created:
        try:
            dt = datetime.fromisoformat(created)
            created_str = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            created_str = created
    else:
        created_str = "unknown"

    stale_note = ""
    if current_last_msg and summary["last_message_id"] != current_last_msg:
        stale_note = " [stale - new messages since summary]"

    print(f"Generated: {created_str}{stale_note}")
    print()

    print("SUMMARY")
    print("-" * 40)
    print(summary["summary_text"])
    print()

    # Key points
    key_points = summary.get("key_points")
    if key_points:
        if isinstance(key_points, str):
            key_points = json_module.loads(key_points)
        if key_points:
            print("KEY POINTS")
            print("-" * 40)
            for point in key_points:
                print(f"- {point}")
            print()

    # Action items
    action_items = summary.get("action_items")
    if action_items:
        if isinstance(action_items, str):
            action_items = json_module.loads(action_items)
        if action_items:
            print("ACTION ITEMS")
            print("-" * 40)
            for item in action_items:
                print(f"- {item}")
            print()

    # Participants
    participants = summary.get("participants")
    if participants:
        if isinstance(participants, str):
            participants = json_module.loads(participants)
        if participants:
            print("PARTICIPANTS")
            print("-" * 40)
            for p in participants:
                if isinstance(p, dict):
                    print(f"- {p.get('name', 'Unknown')} ({p.get('count', '?')} messages)")
                else:
                    print(f"- {p}")
            print()


def cmd_search(args: argparse.Namespace) -> None:
    """Full-text search across messages."""
    import json as json_module
    from datetime import datetime

    site_name = args.site or get_default_site()
    query = args.query
    stream_filter = getattr(args, 'stream', None)
    limit = args.limit or 10
    json_output = args.json

    site_id = get_site_id(site_name)
    if site_id is None:
        print(f"No data found for site '{site_name}'. Run 'sync' first.", file=sys.stderr)
        sys.exit(1)

    results = search_threads(
        query=query,
        site_id=site_id,
        stream_name=stream_filter,
        limit=limit,
    )

    if not results:
        if json_output:
            print(json_module.dumps({"threads": [], "query": query}))
        else:
            print(f"No results for: {query}")
        return

    if json_output:
        # JSON output for RAG/Claude Code consumption
        output = {
            "query": query,
            "site": site_name,
            "thread_count": len(results),
            "threads": results,
        }
        print(json_module.dumps(output, indent=2))
    else:
        # Human-readable output
        print(f"Search: {query}")
        print(f"Found {len(results)} threads")
        print("=" * 70)

        for thread in results:
            print()
            unread_note = ""
            if thread.get("importance"):
                unread_note = f" [{thread['importance']}/{thread['urgency']}]"
            print(f"#{thread['stream']} > {thread['topic']}{unread_note}")
            print(f"  {thread['url']}")

            # Show match info
            msg_matches = len(thread['matched_message_ids'])
            summary_match = thread.get('matched_in_summary', False)
            if msg_matches and summary_match:
                print(f"  {thread['message_count']} messages, {msg_matches} matched + summary matched")
            elif summary_match:
                print(f"  {thread['message_count']} messages, matched in summary")
            else:
                print(f"  {thread['message_count']} messages, {msg_matches} matched")

            if thread.get("summary"):
                summary = thread["summary"]
                if len(summary) > 100:
                    summary = summary[:97] + "..."
                prefix = ">>> " if summary_match else ""
                print(f"  {prefix}{summary}")

            # Show a snippet of matched messages
            matched_msgs = [m for m in thread["messages"] if m["matched"]]
            for msg in matched_msgs[:2]:  # Show first 2 matches
                ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d")
                content = msg["content"][:80].replace("\n", " ")
                if len(msg["content"]) > 80:
                    content += "..."
                print(f"    [{ts}] {msg['sender']}: {content}")


def cmd_rebuild_fts(args: argparse.Namespace) -> None:
    """Rebuild FTS index from scratch."""
    print("Rebuilding full-text search indexes...")
    counts = rebuild_fts_index()
    print(f"Indexed {counts['messages']} messages and {counts['summaries']} summaries.")


def cmd_sites(args: argparse.Namespace) -> None:
    """List configured Zulip sites."""
    sites = list_sites()
    default_site = get_default_site()

    print("Configured sites:")
    for site in sites:
        marker = " (default)" if site == default_site else ""
        print(f"  - {site}{marker}")


def main() -> NoReturn:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="zulip-client",
        description="Local Zulip message sync and unread management",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # unread command
    unread_parser = subparsers.add_parser("unread", help="Show unread message summary")
    unread_parser.add_argument("-s", "--site", help="Zulip site")
    unread_parser.add_argument("-a", "--all", action="store_true", help="Show all sites")
    unread_parser.set_defaults(func=cmd_unread)

    # sync command
    sync_parser = subparsers.add_parser("sync", help="Download messages (default: all subscribed streams)")
    sync_parser.add_argument("-s", "--site", help="Zulip site")
    sync_parser.add_argument("-a", dest="all_sites", action="store_true", help="Sync all configured sites")
    sync_parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed progress")
    sync_parser.add_argument("-n", "--limit", type=int, help="Limit number of topics to sync")
    sync_parser.add_argument("--all", dest="sync_all", action="store_true", help="Sync all subscribed streams (default)")
    sync_parser.add_argument("--unread", action="store_true", help="Sync threads with unread messages")
    sync_parser.add_argument("--mine", action="store_true", help="Sync threads I've participated in")
    sync_parser.add_argument("--full", action="store_true", help="Force full resync (skip incremental mode)")
    sync_parser.set_defaults(func=cmd_sync)

    # export command
    export_parser = subparsers.add_parser("export", help="Export stored messages")
    export_parser.add_argument("stream", nargs="?", help="Channel/stream name (if omitted, export all)")
    export_parser.add_argument("topic", nargs="?", help="Topic name (if omitted, export all topics in channel)")
    export_parser.add_argument("-s", "--site", help="Zulip site")
    export_parser.add_argument(
        "-f", "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format",
    )
    export_parser.set_defaults(func=cmd_export)

    # channels command
    channels_parser = subparsers.add_parser("channels", help="List downloaded channels")
    channels_parser.add_argument("-s", "--site", help="Zulip site")
    channels_parser.set_defaults(func=cmd_channels)

    # topics command
    topics_parser = subparsers.add_parser("topics", help="List topics in a channel")
    topics_parser.add_argument("stream", help="Channel/stream name")
    topics_parser.add_argument("-s", "--site", help="Zulip site")
    topics_parser.set_defaults(func=cmd_topics)

    # messages command
    messages_parser = subparsers.add_parser("messages", help="Show messages in a topic")
    messages_parser.add_argument("stream", help="Channel/stream name")
    messages_parser.add_argument("topic", help="Topic name")
    messages_parser.add_argument("-s", "--site", help="Zulip site")
    messages_parser.set_defaults(func=cmd_messages)

    # sites command
    sites_parser = subparsers.add_parser("sites", help="List configured Zulip sites")
    sites_parser.set_defaults(func=cmd_sites)

    # summary command
    summary_parser = subparsers.add_parser("summary", help="Generate AI summaries for topics without them")
    summary_parser.add_argument("stream", nargs="?", help="Channel/stream name (if omitted, process all channels)")
    summary_parser.add_argument("topic", nargs="?", help="Topic name (if omitted, process all topics in channel)")
    summary_parser.add_argument("-s", "--site", help="Zulip site")
    summary_parser.add_argument("-f", "--force", action="store_true", help="Regenerate even if already summarized")
    summary_parser.add_argument("--model", help=f"Claude model (default: {DEFAULT_MODEL}). If specified, skips quota checking.")
    summary_parser.set_defaults(func=cmd_summary)

    # triage command
    triage_parser = subparsers.add_parser("triage", help="Filter threads by AI-classified importance")
    triage_parser.add_argument("-s", "--site", help="Zulip site")
    triage_parser.add_argument("-a", "--all", action="store_true", help="Include all threads (not just unread)")
    triage_parser.add_argument("--importance", choices=["high", "medium", "low"], help="Filter by minimum importance")
    triage_parser.add_argument("--urgency", choices=["high", "medium", "low"], help="Filter by minimum urgency")
    triage_parser.add_argument("--generate-missing", action="store_true", help="Generate summaries for threads without them")
    triage_parser.add_argument("--model", help=f"Claude model (default: {DEFAULT_MODEL}). If specified, skips quota checking.")
    triage_parser.add_argument("-n", "--limit", type=int, help="Limit number of summaries to generate")
    triage_parser.set_defaults(func=cmd_triage)

    # mark-as-read command
    mark_read_parser = subparsers.add_parser("mark-as-read", help="Mark topics as read")
    mark_read_parser.add_argument("stream", nargs="?", help="Channel/stream name")
    mark_read_parser.add_argument("topic", nargs="?", help="Topic name")
    mark_read_parser.add_argument("-s", "--site", help="Zulip site")
    mark_read_parser.add_argument("--importance", choices=["high", "medium", "low"],
                                  help="Mark all topics at or below this importance level")
    mark_read_parser.add_argument("--urgency", choices=["high", "medium", "low"],
                                  help="Mark all topics at or below this urgency level")
    mark_read_parser.set_defaults(func=cmd_mark_as_read)

    # search command
    search_parser = subparsers.add_parser("search", help="Full-text search across messages")
    search_parser.add_argument("query", help="Search query (supports AND, OR, NOT, phrases, prefix*)")
    search_parser.add_argument("-s", "--site", help="Zulip site")
    search_parser.add_argument("--stream", help="Limit search to specific stream")
    search_parser.add_argument("-n", "--limit", type=int, default=10, help="Max threads to return (default: 10)")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON (for Claude Code/RAG)")
    search_parser.set_defaults(func=cmd_search)

    # rebuild-fts command
    rebuild_fts_parser = subparsers.add_parser("rebuild-fts", help="Rebuild full-text search index")
    rebuild_fts_parser.set_defaults(func=cmd_rebuild_fts)

    args = parser.parse_args()

    # Validate: topic requires stream for export/summary
    if args.command in ("export", "summary") and getattr(args, "topic", None) and not getattr(args, "stream", None):
        parser.error("topic requires stream")

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        close_database()

    sys.exit(0)


if __name__ == "__main__":
    main()
