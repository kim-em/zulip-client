# zulip-client

Local Zulip message sync, triage, and AI summarization tool.

## Architecture

### Data Storage

**SQLite database**: `data/zulip.db` - the source of truth for all synced data.

Tables:
- `sites` - Zulip instances (leanprover, lean-fro)
- `streams` - channels within each site
- `topics` - threads within streams
- `messages` - individual messages with content
- `unread_messages` - tracks unread state
- `summaries` - AI-generated thread summaries with importance/urgency ratings
- `sync_mine_state` - tracks incremental sync progress for `sync-mine`
- `sync_all_state` - tracks incremental sync progress for `sync --all` (high water mark + message count)
- `messages_fts` - FTS5 virtual table for message content search
- `summaries_fts` - FTS5 virtual table for summary/key_points/action_items search

When looking for summaries, query the `summaries` table, not JSON files.

### Full-Text Search

FTS5 is used for fast full-text search across messages AND summaries:

**Indexed tables:**
- `messages_fts` - `content_text`, `sender_name`
- `summaries_fts` - `summary_text`, `key_points`, `action_items`

**Features:**
- Search returns complete threads when any message OR summary matches
- Results indicate whether match came from messages, summary, or both
- Use `zulip-client search "query" --json` for RAG consumption
- Supports FTS5 syntax: AND, OR, NOT, "phrases", prefix*

### Key Modules

- `cli.py` - Click-based CLI with all commands (unread, sync, triage, export, etc.)
- `database.py` - SQLite operations, schema, queries
- `api.py` - Zulip API client with rate limiting
- `summarize.py` - Claude integration for AI summaries
- `export.py` - JSON/Markdown export

### Summarization

`PROMPT.md` contains the prompt for Claude when generating thread summaries.
Summaries are stored in the `summaries` table with importance/urgency classifications.

The `triage` command shows threads grouped by importance, using stored summaries.
Use `--generate-missing` to create summaries for unsummarized threads.

### Credentials

Stored in `~/metacortex/.credentials/zulip.json` (not in this repo).

### MCP Server (mcp/ subdirectory)

Separate package `zulip-client-mcp` for Claude Code integration:
- Install: `pip install ./mcp` (requires `mcp` package)
- Run: `zulip-mcp` or configure in `.mcp.json`
- Tools: `zulip_search`, `zulip_list_streams`, `zulip_get_thread`, `zulip_list_sites`
