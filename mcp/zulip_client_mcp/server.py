"""MCP server for Zulip search integration with Claude Code."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from zulip_client.credentials import list_sites, get_default_site
from zulip_client.database import (
    get_site_id,
    search_threads,
    get_channels_summary,
    get_topic_messages,
    get_topic_by_names,
    get_summary,
)

mcp = FastMCP("zulip-search")


@mcp.tool()
def zulip_search(
    query: str,
    site: str | None = None,
    stream: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Search Zulip messages in the local database.
    Returns full threads when any message matches the query.

    Args:
        query: Search terms to find in message content (supports AND, OR, NOT, phrases, prefix*)
        site: Zulip site name (default: leanprover)
        stream: Optional stream/channel to limit search
        limit: Maximum number of threads to return (default: 10)

    Returns:
        List of thread objects with full message history, including:
        - site, stream, topic names and Zulip URL
        - All messages in the thread (with matched flag)
        - Summary, importance, urgency if available
    """
    site_name = site or get_default_site()
    site_id = get_site_id(site_name)

    if site_id is None:
        return [{"error": f"No data for site '{site_name}'. Run 'zulip-client sync' first."}]

    return search_threads(
        query=query,
        site_id=site_id,
        stream_name=stream,
        limit=limit,
    )


@mcp.tool()
def zulip_list_streams(site: str | None = None) -> list[dict]:
    """
    List all synced Zulip streams/channels with message counts.

    Args:
        site: Zulip site name (default: leanprover)

    Returns:
        List of stream objects with name, topic count, message count
    """
    site_name = site or get_default_site()
    site_id = get_site_id(site_name)

    if site_id is None:
        return [{"error": f"No data for site '{site_name}'. Run 'zulip-client sync' first."}]

    channels = get_channels_summary(site_id)
    return [
        {
            "stream": ch["stream_name"],
            "stream_id": ch["stream_id"],
            "topic_count": ch["topic_count"],
            "message_count": ch["message_count"],
            "unread_count": ch["unread_count"],
        }
        for ch in channels
    ]


@mcp.tool()
def zulip_get_thread(
    stream: str,
    topic: str,
    site: str | None = None,
) -> dict:
    """
    Get a specific Zulip thread by stream and topic name.

    Args:
        stream: Stream/channel name
        topic: Topic name
        site: Zulip site name (default: leanprover)

    Returns:
        Thread object with all messages and summary if available
    """
    from urllib.parse import quote

    site_name = site or get_default_site()
    site_id = get_site_id(site_name)

    if site_id is None:
        return {"error": f"No data for site '{site_name}'. Run 'zulip-client sync' first."}

    topic_info = get_topic_by_names(site_id, stream, topic)
    if topic_info is None:
        return {"error": f"Topic not found: #{stream} > {topic}"}

    messages = get_topic_messages(site_id, stream, topic)
    summary_data = get_summary(topic_info["id"])

    # Get site URL for building link
    from zulip_client.credentials import get_site_credentials
    creds = get_site_credentials(site_name)
    site_url = creds["site"]

    # Get stream_id for URL
    from zulip_client.database import get_stream_by_name
    stream_info = get_stream_by_name(site_id, stream)
    stream_id = stream_info["stream_id"] if stream_info else 0

    stream_encoded = quote(stream, safe="")
    topic_encoded = quote(topic, safe="")
    url = f"{site_url}/#narrow/stream/{stream_id}-{stream_encoded}/topic/{topic_encoded}"

    result = {
        "site": site_name,
        "stream": stream,
        "topic": topic,
        "url": url,
        "message_count": len(messages),
        "messages": [
            {
                "id": m["message_id"],
                "sender": m["sender_name"],
                "sender_email": m["sender_email"],
                "content": m.get("content_markdown") or m["content_text"],
                "timestamp": m["timestamp"],
            }
            for m in messages
        ],
    }

    if summary_data:
        result["summary"] = summary_data["summary_text"]
        result["importance"] = summary_data["importance"]
        result["urgency"] = summary_data["urgency"]

    return result


@mcp.tool()
def zulip_list_sites() -> list[str]:
    """
    List configured Zulip sites.

    Returns:
        List of site names that can be used with other tools
    """
    return list_sites()


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
