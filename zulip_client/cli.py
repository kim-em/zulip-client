#!/usr/bin/env python3
"""Command-line interface for zulip-client."""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, NoReturn, Optional

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
)
from .export import export_topic_to_json, export_topic_to_markdown


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
    """Download threads with unread messages."""
    sites = list_sites() if args.all else [args.site or get_default_site()]

    for site_name in sites:
        _sync_site(site_name, args.verbose)
        if len(sites) > 1:
            print()


def _sync_site(site_name: str, verbose: bool) -> None:
    """Sync a single site."""
    client = ZulipClient(site_name)

    print(f"Syncing {client.site_url}...")

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

    print(f"Found {len(unread_topics)} topics with unread messages.")
    print()

    total_new_messages = 0

    for topic in unread_topics:
        stream_name = topic["stream_name"]
        topic_name = topic["topic_name"]

        if verbose:
            print(f"Syncing #{stream_name} > {topic_name}...")

        # Get or create stream and topic in database
        stream_db_id = get_or_create_stream(site_id, topic["stream_id"], stream_name)
        topic_db_id = get_or_create_topic(stream_db_id, topic_name)

        # Get the last message ID we have for incremental sync
        last_message_id = get_topic_last_message_id(topic_db_id)

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
                print(f"  Stored {inserted} new messages ({len(messages)} fetched)")

            # Export to JSON
            all_stored_messages = get_topic_messages(site_id, stream_name, topic_name)
            export_path = export_topic_to_json(
                site_name,
                stream_name,
                topic_name,
                all_stored_messages,
                topic["message_ids"],
            )

            if verbose:
                print(f"  Exported to {export_path}")
        elif verbose:
            print("  No new messages")

    update_site_last_sync(site_id)

    print()
    print(f"Sync complete. {total_new_messages} new messages stored.")


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
    sync_parser = subparsers.add_parser("sync", help="Download threads with unread messages")
    sync_parser.add_argument("-s", "--site", help="Zulip site")
    sync_parser.add_argument("-a", "--all", action="store_true", help="Sync all sites")
    sync_parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed progress")
    sync_parser.set_defaults(func=cmd_sync)

    # export command
    export_parser = subparsers.add_parser("export", help="Export stored messages")
    export_parser.add_argument("-s", "--site", help="Zulip site")
    export_parser.add_argument("--stream", help="Filter by stream name")
    export_parser.add_argument("--topic", help="Filter by topic name (requires --stream)")
    export_parser.add_argument(
        "-f", "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format",
    )
    export_parser.set_defaults(func=cmd_export)

    # sites command
    sites_parser = subparsers.add_parser("sites", help="List configured Zulip sites")
    sites_parser.set_defaults(func=cmd_sites)

    args = parser.parse_args()

    if args.command == "export" and args.topic and not args.stream:
        parser.error("--topic requires --stream")

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
