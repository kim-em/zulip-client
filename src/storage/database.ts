import Database from 'better-sqlite3';
import { existsSync, mkdirSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { SCHEMA } from './schema.js';
import type { ZulipMessage, UnreadStreamInfo, StreamInfo } from '../api/types.js';
import { convert } from 'html-to-text';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(__dirname, '..', '..', 'data');
const DB_PATH = join(DATA_DIR, 'zulip.db');

let db: Database.Database | null = null;

export function getDatabase(): Database.Database {
  if (db) {
    return db;
  }

  if (!existsSync(DATA_DIR)) {
    mkdirSync(DATA_DIR, { recursive: true });
  }

  db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.exec(SCHEMA);

  return db;
}

export function closeDatabase(): void {
  if (db) {
    db.close();
    db = null;
  }
}

// Site operations
export function getOrCreateSite(name: string, url: string): number {
  const db = getDatabase();

  const existing = db.prepare('SELECT id FROM sites WHERE name = ?').get(name) as { id: number } | undefined;
  if (existing) {
    return existing.id;
  }

  const result = db.prepare('INSERT INTO sites (name, url) VALUES (?, ?)').run(name, url);
  return result.lastInsertRowid as number;
}

export function updateSiteLastSync(siteId: number): void {
  const db = getDatabase();
  db.prepare('UPDATE sites SET last_sync = ? WHERE id = ?').run(new Date().toISOString(), siteId);
}

// Stream operations
export function getOrCreateStream(siteId: number, streamId: number, name: string): number {
  const db = getDatabase();

  const existing = db.prepare(
    'SELECT id FROM streams WHERE site_id = ? AND stream_id = ?'
  ).get(siteId, streamId) as { id: number } | undefined;

  if (existing) {
    // Update name if changed
    db.prepare('UPDATE streams SET name = ? WHERE id = ?').run(name, existing.id);
    return existing.id;
  }

  const result = db.prepare(
    'INSERT INTO streams (site_id, stream_id, name) VALUES (?, ?, ?)'
  ).run(siteId, streamId, name);
  return result.lastInsertRowid as number;
}

export function getStreamByZulipId(siteId: number, streamId: number): { id: number; name: string } | undefined {
  const db = getDatabase();
  return db.prepare(
    'SELECT id, name FROM streams WHERE site_id = ? AND stream_id = ?'
  ).get(siteId, streamId) as { id: number; name: string } | undefined;
}

// Topic operations
export function getOrCreateTopic(streamId: number, name: string): number {
  const db = getDatabase();

  const existing = db.prepare(
    'SELECT id FROM topics WHERE stream_id = ? AND name = ?'
  ).get(streamId, name) as { id: number } | undefined;

  if (existing) {
    return existing.id;
  }

  const result = db.prepare(
    'INSERT INTO topics (stream_id, name) VALUES (?, ?)'
  ).run(streamId, name);
  return result.lastInsertRowid as number;
}

export function getTopicLastMessageId(topicId: number): number | null {
  const db = getDatabase();
  const result = db.prepare('SELECT last_message_id FROM topics WHERE id = ?').get(topicId) as { last_message_id: number | null } | undefined;
  return result?.last_message_id ?? null;
}

export function updateTopicLastMessageId(topicId: number, messageId: number): void {
  const db = getDatabase();
  db.prepare('UPDATE topics SET last_message_id = ? WHERE id = ?').run(messageId, topicId);
}

// Message operations
export function insertMessages(topicId: number, messages: ZulipMessage[]): number {
  const db = getDatabase();
  const stmt = db.prepare(`
    INSERT OR IGNORE INTO messages
    (topic_id, message_id, sender_name, sender_email, content, content_text, timestamp, raw_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);

  let inserted = 0;
  const insertMany = db.transaction((msgs: ZulipMessage[]) => {
    for (const msg of msgs) {
      const contentText = convert(msg.content, {
        wordwrap: false,
        selectors: [
          { selector: 'a', options: { ignoreHref: true } },
          { selector: 'img', format: 'skip' },
        ],
      });

      const result = stmt.run(
        topicId,
        msg.id,
        msg.sender_full_name,
        msg.sender_email,
        msg.content,
        contentText,
        msg.timestamp,
        JSON.stringify(msg)
      );

      if (result.changes > 0) {
        inserted++;
      }
    }
  });

  insertMany(messages);
  return inserted;
}

// Unread tracking
export function clearUnreadMessages(siteId: number): void {
  const db = getDatabase();
  db.prepare('DELETE FROM unread_messages WHERE site_id = ?').run(siteId);
}

export function insertUnreadMessages(
  siteId: number,
  unreads: UnreadStreamInfo[],
  streamMap: Map<number, string>
): void {
  const db = getDatabase();
  const stmt = db.prepare(`
    INSERT INTO unread_messages (site_id, message_id, stream_id, stream_name, topic_name)
    VALUES (?, ?, ?, ?, ?)
  `);

  const insertMany = db.transaction((items: Array<{ streamId: number; topic: string; messageIds: number[] }>) => {
    for (const item of items) {
      const streamName = streamMap.get(item.streamId) || `stream_${item.streamId}`;
      for (const msgId of item.messageIds) {
        stmt.run(siteId, msgId, item.streamId, streamName, item.topic);
      }
    }
  });

  const items = unreads.map(u => ({
    streamId: u.stream_id,
    topic: u.topic,
    messageIds: u.unread_message_ids,
  }));

  insertMany(items);
}

// Query operations
export interface UnreadSummary {
  streamName: string;
  streamId: number;
  topics: Array<{
    topicName: string;
    count: number;
  }>;
  totalCount: number;
}

export function getUnreadSummary(siteId: number): UnreadSummary[] {
  const db = getDatabase();

  const rows = db.prepare(`
    SELECT stream_name, stream_id, topic_name, COUNT(*) as count
    FROM unread_messages
    WHERE site_id = ?
    GROUP BY stream_name, stream_id, topic_name
    ORDER BY stream_name, topic_name
  `).all(siteId) as Array<{
    stream_name: string;
    stream_id: number;
    topic_name: string;
    count: number;
  }>;

  const summaryMap = new Map<string, UnreadSummary>();

  for (const row of rows) {
    let summary = summaryMap.get(row.stream_name);
    if (!summary) {
      summary = {
        streamName: row.stream_name,
        streamId: row.stream_id,
        topics: [],
        totalCount: 0,
      };
      summaryMap.set(row.stream_name, summary);
    }
    summary.topics.push({ topicName: row.topic_name, count: row.count });
    summary.totalCount += row.count;
  }

  return Array.from(summaryMap.values());
}

export function getTotalUnreadCount(siteId: number): number {
  const db = getDatabase();
  const result = db.prepare(
    'SELECT COUNT(*) as count FROM unread_messages WHERE site_id = ?'
  ).get(siteId) as { count: number };
  return result.count;
}

export interface UnreadTopic {
  streamId: number;
  streamName: string;
  topicName: string;
  messageIds: number[];
}

export function getUnreadTopics(siteId: number): UnreadTopic[] {
  const db = getDatabase();

  const rows = db.prepare(`
    SELECT stream_id, stream_name, topic_name, GROUP_CONCAT(message_id) as message_ids
    FROM unread_messages
    WHERE site_id = ?
    GROUP BY stream_id, stream_name, topic_name
  `).all(siteId) as Array<{
    stream_id: number;
    stream_name: string;
    topic_name: string;
    message_ids: string;
  }>;

  return rows.map(row => ({
    streamId: row.stream_id,
    streamName: row.stream_name,
    topicName: row.topic_name,
    messageIds: row.message_ids.split(',').map(Number),
  }));
}

// Export helpers
export interface StoredMessage {
  id: number;
  message_id: number;
  sender_name: string;
  sender_email: string;
  content: string;
  content_text: string;
  timestamp: number;
  raw_json: string;
}

export interface TopicExport {
  site: string;
  stream: string;
  topic: string;
  messages: StoredMessage[];
  unreadMessageIds: number[];
}

export function getTopicMessages(
  siteId: number,
  streamName: string,
  topicName: string
): StoredMessage[] {
  const db = getDatabase();

  return db.prepare(`
    SELECT m.*
    FROM messages m
    JOIN topics t ON m.topic_id = t.id
    JOIN streams s ON t.stream_id = s.id
    WHERE s.site_id = ? AND s.name = ? AND t.name = ?
    ORDER BY m.timestamp ASC
  `).all(siteId, streamName, topicName) as StoredMessage[];
}

export function getSiteId(siteName: string): number | undefined {
  const db = getDatabase();
  const result = db.prepare('SELECT id FROM sites WHERE name = ?').get(siteName) as { id: number } | undefined;
  return result?.id;
}

export function getAllStreams(siteId: number): Array<{ name: string; streamId: number }> {
  const db = getDatabase();
  return db.prepare(`
    SELECT name, stream_id as streamId FROM streams WHERE site_id = ?
  `).all(siteId) as Array<{ name: string; streamId: number }>;
}

export function getTopicsForStream(streamDbId: number): Array<{ name: string; id: number }> {
  const db = getDatabase();
  return db.prepare(`
    SELECT name, id FROM topics WHERE stream_id = ?
  `).all(streamDbId) as Array<{ name: string; id: number }>;
}
