import { existsSync, mkdirSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import type { StoredMessage } from './database.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const EXPORT_DIR = join(__dirname, '..', '..', 'data', 'export');

function sanitizeFilename(name: string): string {
  return name
    .replace(/[<>:"/\\|?*]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .toLowerCase();
}

export interface ExportedMessage {
  id: number;
  sender: string;
  senderEmail: string;
  timestamp: string;
  content: string;
  contentText: string;
}

export interface TopicExportJson {
  site: string;
  stream: string;
  topic: string;
  messages: ExportedMessage[];
  unreadCount: number;
  unreadMessageIds: number[];
  exportedAt: string;
  messageCount: number;
}

export function exportTopicToJson(
  siteName: string,
  streamName: string,
  topicName: string,
  messages: StoredMessage[],
  unreadMessageIds: number[] = []
): string {
  const siteDir = join(EXPORT_DIR, sanitizeFilename(siteName));
  const streamDir = join(siteDir, sanitizeFilename(streamName));

  if (!existsSync(streamDir)) {
    mkdirSync(streamDir, { recursive: true });
  }

  const filename = `${sanitizeFilename(topicName)}.json`;
  const filepath = join(streamDir, filename);

  const unreadSet = new Set(unreadMessageIds);

  const exportData: TopicExportJson = {
    site: siteName,
    stream: streamName,
    topic: topicName,
    messages: messages.map(m => ({
      id: m.message_id,
      sender: m.sender_name,
      senderEmail: m.sender_email,
      timestamp: new Date(m.timestamp * 1000).toISOString(),
      content: m.content,
      contentText: m.content_text,
    })),
    unreadCount: unreadMessageIds.length,
    unreadMessageIds,
    exportedAt: new Date().toISOString(),
    messageCount: messages.length,
  };

  writeFileSync(filepath, JSON.stringify(exportData, null, 2));
  return filepath;
}

export function exportTopicToMarkdown(
  siteName: string,
  streamName: string,
  topicName: string,
  messages: StoredMessage[],
  unreadMessageIds: number[] = []
): string {
  const siteDir = join(EXPORT_DIR, sanitizeFilename(siteName));
  const streamDir = join(siteDir, sanitizeFilename(streamName));

  if (!existsSync(streamDir)) {
    mkdirSync(streamDir, { recursive: true });
  }

  const filename = `${sanitizeFilename(topicName)}.md`;
  const filepath = join(streamDir, filename);

  const unreadSet = new Set(unreadMessageIds);

  const lines: string[] = [
    `# ${streamName} > ${topicName}`,
    '',
    `Site: ${siteName}`,
    `Exported: ${new Date().toISOString()}`,
    `Messages: ${messages.length}`,
    `Unread: ${unreadMessageIds.length}`,
    '',
    '---',
    '',
  ];

  for (const msg of messages) {
    const isUnread = unreadSet.has(msg.message_id);
    const date = new Date(msg.timestamp * 1000);
    const dateStr = date.toISOString().replace('T', ' ').slice(0, 19);

    lines.push(`## ${isUnread ? '[UNREAD] ' : ''}${msg.sender_name}`);
    lines.push(`*${dateStr}*`);
    lines.push('');
    lines.push(msg.content_text);
    lines.push('');
    lines.push('---');
    lines.push('');
  }

  writeFileSync(filepath, lines.join('\n'));
  return filepath;
}
