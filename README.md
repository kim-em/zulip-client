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
npm install
npm run build
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
npm run dev -- unread

# Specific site
npm run dev -- unread --site lean-fro

# All sites
npm run dev -- unread --all
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
npm run dev -- sync

# Verbose output
npm run dev -- sync --verbose

# Sync all sites
npm run dev -- sync --all
```

### Export stored messages

```bash
# Export all stored messages to JSON
npm run dev -- export

# Export specific stream
npm run dev -- export --stream "lean4"

# Export specific topic
npm run dev -- export --stream "lean4" --topic "grind tactic"

# Export to Markdown
npm run dev -- export --format markdown
```

### List configured sites

```bash
npm run dev -- sites
```

## Data Storage

- **SQLite database**: `data/zulip.db` - stores all synced messages
- **JSON exports**: `data/export/{site}/{stream}/{topic}.json`

Both directories are gitignored.

## Development

```bash
# Run in development mode
npm run dev -- <command>

# Build for production
npm run build

# Run built version
npm start -- <command>
```
