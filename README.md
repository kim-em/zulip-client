# zulip-client

Local Zulip message sync and unread management CLI tool.

## Features

- **Unread Summary**: View unread message counts by stream and topic
- **Sync**: Download full thread content for topics with unread messages
- **Browse Local Data**: List channels, topics, and read messages offline
- **Export**: Export stored messages to JSON or Markdown
- **Multi-site**: Support for multiple Zulip instances (leanprover, lean-fro)
- **Incremental**: Only fetches new messages on subsequent syncs
- **Rate Limiting**: Automatic retry with exponential backoff on API limits

## Installation

```bash
# No dependencies required - uses only Python standard library
pipx install -e .
```

This installs the `zulip-client` command globally.

## Configuration

Uses existing Zulip credentials from `~/metacortex/.credentials/zulip.json`:

```json
{
  "default": "leanprover",
  "sites": {
    "leanprover": {
      "email": "your-email@example.com",
      "api_key": "your-api-key",
      "site": "https://leanprover.zulipchat.com"
    },
    "lean-fro": {
      "email": "your-email@lean-fro.org",
      "api_key": "your-api-key",
      "site": "https://lean-fro.zulipchat.com"
    }
  }
}
```

## Usage

### Show unread message summary

```bash
# Default site
zulip-client unread

# Specific site
zulip-client unread --site lean-fro

# All sites
zulip-client unread --all
```

Output:
```
https://leanprover.zulipchat.com - 47 unread messages

#general (12 unread)
  └─ Welcome thread (3)
  └─ Announcements (9)

#lean4 (35 unread)
  └─ grind tactic (15)
  └─ metaprogramming (20)
```

### Sync messages locally

```bash
# Sync topics with unread messages
zulip-client sync

# Verbose output
zulip-client sync --verbose

# Limit to first N topics
zulip-client sync --limit 10

# Sync all sites
zulip-client sync --all
```

### Sync your participated topics

Download all topics where you've posted at least one message:

```bash
# Sync all topics you've participated in
zulip-client sync-mine

# Limit to N new topics (incremental - re-run to get more)
zulip-client sync-mine --limit 100

# Verbose output
zulip-client sync-mine --verbose
```

The `--limit` flag only counts topics not already synced locally, so running
`sync-mine --limit 100` multiple times will eventually sync everything.

### Triage threads by AI-classified importance

Use Claude to generate summaries and classify threads by importance/urgency:

```bash
# Show triage view of unread threads (uses existing summaries)
zulip-client triage

# Generate summaries for threads that don't have them
zulip-client triage --generate-missing

# Limit summary generation (useful for quota management)
zulip-client triage --generate-missing --limit 10

# Filter by minimum importance or urgency
zulip-client triage --importance high
zulip-client triage --urgency medium

# Include all threads, not just unread
zulip-client triage --all
```

Output groups threads by importance:
```
Triage: 15 unread threads (12 summarized)
======================================================================

🔴 HIGH IMPORTANCE + URGENT
#lean4 > RFC: breaking change to simp [3 unread]
  Proposal to change simp behavior that affects downstream projects...

🟠 HIGH IMPORTANCE
#mathlib4 > maintainer review needed [5 unread]
  PR requires maintainer approval before merge...

🟡 MEDIUM IMPORTANCE
#general > meetup planning [2 unread]
  Discussing dates for the next community meetup...

⚪ NOT YET SUMMARIZED
#lean4 > question about tactics [1 unread]
```

### Export stored messages

```bash
# Export all stored messages to JSON
zulip-client export

# Export specific stream
zulip-client export --stream "lean4"

# Export specific topic
zulip-client export --stream "lean4" --topic "grind tactic"

# Export to Markdown
zulip-client export --format markdown
```

### Browse downloaded data

```bash
# List all downloaded channels with topic/message counts
zulip-client channels

# List topics in a channel
zulip-client topics "lean4"

# Show all messages in a topic (with read/unread status)
zulip-client messages "lean4" "grind tactic"
```

### List configured sites

```bash
zulip-client sites
```

## Data Storage

- **SQLite database**: `data/zulip.db` - stores all synced messages
- **JSON exports**: `data/export/{site}/{stream}/{topic}.json`

Both directories are gitignored.

## Development

```bash
# Install in editable mode (changes take effect immediately)
pipx install -e .

# Reinstall after changes to pyproject.toml
pipx reinstall zulip-client
```
