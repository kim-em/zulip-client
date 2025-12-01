export const SCHEMA = `
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
`;
