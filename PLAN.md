# Zulip Client Implementation Plan

## Overview

Create a TypeScript/Node CLI tool for managing Zulip unread messages with local storage sync capabilities.

**Repository:** `kim-em/zulip-client` at `~/projects/zulip-client`

## Core Features

### 1. Unread Summary Mode
```bash
zulip-client unread [--site leanprover]
```
Output format:
```
leanprover.zulipchat.com - 47 unread messages

#general (12 unread)
  └─ Welcome thread (3)
  └─ Announcements (9)

#lean4 (35 unread)
  └─ grind tactic (15)
  └─ metaprogramming (20)
```

### 2. Sync Mode
```bash
zulip-client sync [--site leanprover]
```
- Downloads full thread content for all threads containing unread messages
- Stores in SQLite database
- Exports to JSON files for human inspection
- Incremental: reuses existing downloaded messages, only fetches new ones

### 3. Export Mode
```bash
zulip-client export [--stream "lean4"] [--topic "grind"] [--format json|markdown]
```
- Export stored messages to JSON or Markdown files

## Project Structure

```
zulip-client/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts              # CLI entry point
│   ├── cli/
│   │   ├── unread.ts         # Unread summary command
│   │   ├── sync.ts           # Sync command
│   │   └── export.ts         # Export command
│   ├── api/
│   │   ├── client.ts         # Zulip API client
│   │   └── types.ts          # API response types
│   ├── storage/
│   │   ├── database.ts       # SQLite operations
│   │   ├── schema.ts         # Database schema
│   │   └── json-export.ts    # JSON file export
│   └── config/
│       └── credentials.ts    # Read ~/metacortex/.credentials/zulip.json
├── data/                     # Local storage (gitignored)
│   ├── zulip.db              # SQLite database
│   └── export/               # JSON exports by site/stream/topic
└── README.md
```

## Database Schema

```sql
-- Sites (leanprover, lean-fro)
CREATE TABLE sites (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,           -- "leanprover"
  url TEXT NOT NULL,                   -- "https://leanprover.zulipchat.com"
  last_sync TIMESTAMP
);

-- Streams/Channels
CREATE TABLE streams (
  id INTEGER PRIMARY KEY,
  site_id INTEGER REFERENCES sites(id),
  stream_id INTEGER NOT NULL,          -- Zulip's stream ID
  name TEXT NOT NULL,
  UNIQUE(site_id, stream_id)
);

-- Topics
CREATE TABLE topics (
  id INTEGER PRIMARY KEY,
  stream_id INTEGER REFERENCES streams(id),
  name TEXT NOT NULL,
  last_message_id INTEGER,             -- For incremental sync
  UNIQUE(stream_id, name)
);

-- Messages
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  topic_id INTEGER REFERENCES topics(id),
  message_id INTEGER NOT NULL,         -- Zulip's message ID
  sender_name TEXT NOT NULL,
  sender_email TEXT NOT NULL,
  content TEXT NOT NULL,               -- HTML content
  content_text TEXT NOT NULL,          -- Plain text (stripped)
  timestamp INTEGER NOT NULL,
  is_read INTEGER DEFAULT 0,
  raw_json TEXT,                       -- Full API response
  UNIQUE(topic_id, message_id)
);

-- Track unread state
CREATE TABLE unread_messages (
  id INTEGER PRIMARY KEY,
  site_id INTEGER REFERENCES sites(id),
  message_id INTEGER NOT NULL,
  stream_name TEXT,
  topic_name TEXT,
  UNIQUE(site_id, message_id)
);
```

## API Integration

### Getting Unread Messages

The Zulip API provides unread info via the `/register` endpoint:

```typescript
// POST /api/v1/register with fetch_event_types=["message"]
// Returns unread_msgs object with:
// - pms: direct messages
// - streams: [{stream_id, topic, unread_message_ids}, ...]
// - mentions: mentioned message IDs
```

### Fetching Thread Content

```typescript
// GET /api/v1/messages
// narrow: [{"operator": "stream", "operand": "lean4"},
//          {"operator": "topic", "operand": "grind"}]
// anchor: "oldest" or specific message_id
// num_before: 0, num_after: 1000
```

### Incremental Sync Strategy

1. Call `/register` to get current unread message IDs
2. For each (stream, topic) with unreads:
   - Check local DB for `last_message_id`
   - Fetch messages with `anchor = last_message_id + 1`
   - Store new messages, update `last_message_id`
3. Update `unread_messages` table with current state

## CLI Implementation

Using `commander` for CLI parsing:

```typescript
import { Command } from 'commander';

const program = new Command();

program
  .name('zulip-client')
  .description('Local Zulip message sync and unread management')
  .version('1.0.0');

program
  .command('unread')
  .description('Show unread message summary')
  .option('-s, --site <site>', 'Zulip site (default: leanprover)')
  .option('-a, --all', 'Show all sites')
  .action(unreadCommand);

program
  .command('sync')
  .description('Download threads with unread messages')
  .option('-s, --site <site>', 'Zulip site (default: leanprover)')
  .option('-a, --all', 'Sync all sites')
  .option('-v, --verbose', 'Show progress')
  .action(syncCommand);

program
  .command('export')
  .description('Export stored messages')
  .option('-s, --site <site>', 'Zulip site')
  .option('--stream <stream>', 'Filter by stream')
  .option('--topic <topic>', 'Filter by topic')
  .option('-f, --format <format>', 'Output format (json|markdown)', 'json')
  .action(exportCommand);
```

## Dependencies

```json
{
  "dependencies": {
    "commander": "^12.0.0",
    "better-sqlite3": "^11.0.0",
    "node-fetch": "^3.0.0",
    "html-to-text": "^9.0.0"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "@types/better-sqlite3": "^7.0.0",
    "typescript": "^5.0.0",
    "tsx": "^4.0.0"
  }
}
```

## Credentials

Reuse existing credentials from `~/metacortex/.credentials/zulip.json`:

```json
{
  "default": "leanprover",
  "sites": {
    "leanprover": {
      "email": "...",
      "api_key": "...",
      "site": "https://leanprover.zulipchat.com"
    },
    "lean-fro": {
      "email": "...",
      "api_key": "...",
      "site": "https://lean-fro.zulipchat.com"
    }
  }
}
```

## JSON Export Structure

```
data/export/
└── leanprover/
    └── lean4/
        └── grind-tactic.json
```

Each JSON file contains:
```json
{
  "site": "leanprover",
  "stream": "lean4",
  "topic": "grind tactic",
  "messages": [
    {
      "id": 12345,
      "sender": "Kim Morrison",
      "timestamp": "2025-01-15T10:30:00Z",
      "content": "...",
      "is_read": false
    }
  ],
  "exported_at": "2025-12-01T...",
  "unread_count": 5
}
```

## Implementation Status

- [x] Initialize project structure (package.json, tsconfig.json)
- [x] Create credentials reader
- [x] Create Zulip API client with auth
- [x] Set up SQLite database and schema
- [x] Implement unread command
- [x] Implement sync command
- [x] Implement export command
- [x] Add README with usage examples
- [ ] Test with npm install and build
- [ ] Create GitHub repo

## Notes

- The `is:unread` narrow filter exists but may not be documented; we'll use `/register` endpoint instead which explicitly provides unread message IDs
- Rate limiting: Zulip recommends max 1000 messages per request
- The tool is read-only from Zulip's perspective (no marking as read via this tool - that stays in the web UI)
