# zulip-client

Local Zulip message sync and unread management CLI tool.

## Features

- **Unread Summary**: View unread message counts by stream and topic
- **Sync**: Download full thread content for topics with unread messages
- **Export**: Export stored messages to JSON or Markdown
- **Multi-site**: Support for multiple Zulip instances (leanprover, lean-fro)
- **Incremental**: Only fetches new messages on subsequent syncs

## Installation

```bash
# No dependencies required - uses only Python standard library
pip install -e .
```

Or run directly without installing:
```bash
python -m zulip_client <command>
```

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
python -m zulip_client unread

# Specific site
python -m zulip_client unread --site lean-fro

# All sites
python -m zulip_client unread --all
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
python -m zulip_client sync

# Verbose output
python -m zulip_client sync --verbose

# Sync all sites
python -m zulip_client sync --all
```

### Export stored messages

```bash
# Export all stored messages to JSON
python -m zulip_client export

# Export specific stream
python -m zulip_client export --stream "lean4"

# Export specific topic
python -m zulip_client export --stream "lean4" --topic "grind tactic"

# Export to Markdown
python -m zulip_client export --format markdown
```

### List configured sites

```bash
python -m zulip_client sites
```

## Data Storage

- **SQLite database**: `data/zulip.db` - stores all synced messages
- **JSON exports**: `data/export/{site}/{stream}/{topic}.json`

Both directories are gitignored.

## Development

```bash
# Run directly
python -m zulip_client <command>

# Or install in editable mode
pip install -e .
zulip-client <command>
```
