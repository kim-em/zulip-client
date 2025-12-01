import {
  getSiteId,
  getAllStreams,
  getTopicsForStream,
  getTopicMessages,
  getUnreadTopics,
  getDatabase,
} from '../storage/database.js';
import { exportTopicToJson, exportTopicToMarkdown } from '../storage/json-export.js';
import { getDefaultSite } from '../config/credentials.js';

export interface ExportOptions {
  site?: string;
  stream?: string;
  topic?: string;
  format?: 'json' | 'markdown';
}

export async function exportCommand(options: ExportOptions): Promise<void> {
  const siteName = options.site || getDefaultSite();
  const format = options.format || 'json';

  const siteId = getSiteId(siteName);
  if (!siteId) {
    console.error(`No data found for site '${siteName}'. Run 'sync' first.`);
    process.exit(1);
  }

  // Get unread message IDs for this site
  const unreadTopics = getUnreadTopics(siteId);
  const unreadByTopic = new Map<string, number[]>();
  for (const t of unreadTopics) {
    const key = `${t.streamName}:${t.topicName}`;
    unreadByTopic.set(key, t.messageIds);
  }

  const db = getDatabase();

  // Determine what to export
  if (options.stream && options.topic) {
    // Export single topic
    await exportSingleTopic(
      siteName,
      siteId,
      options.stream,
      options.topic,
      format,
      unreadByTopic
    );
  } else if (options.stream) {
    // Export all topics in a stream
    const stream = db.prepare(`
      SELECT id, name FROM streams WHERE site_id = ? AND name = ?
    `).get(siteId, options.stream) as { id: number; name: string } | undefined;

    if (!stream) {
      console.error(`Stream '${options.stream}' not found. Available streams:`);
      const streams = getAllStreams(siteId);
      for (const s of streams) {
        console.log(`  - ${s.name}`);
      }
      process.exit(1);
    }

    const topics = getTopicsForStream(stream.id);
    console.log(`Exporting ${topics.length} topics from #${options.stream}...`);

    for (const topic of topics) {
      await exportSingleTopic(
        siteName,
        siteId,
        options.stream,
        topic.name,
        format,
        unreadByTopic
      );
    }
  } else {
    // Export everything
    const streams = getAllStreams(siteId);

    if (streams.length === 0) {
      console.error('No streams found. Run sync first.');
      process.exit(1);
    }

    console.log(`Exporting all stored messages for ${siteName}...`);

    let totalTopics = 0;
    for (const stream of streams) {
      const streamDbId = db.prepare(`
        SELECT id FROM streams WHERE site_id = ? AND stream_id = ?
      `).get(siteId, stream.streamId) as { id: number };

      const topics = getTopicsForStream(streamDbId.id);
      totalTopics += topics.length;

      for (const topic of topics) {
        await exportSingleTopic(
          siteName,
          siteId,
          stream.name,
          topic.name,
          format,
          unreadByTopic
        );
      }
    }

    console.log(`Exported ${totalTopics} topics.`);
  }
}

async function exportSingleTopic(
  siteName: string,
  siteId: number,
  streamName: string,
  topicName: string,
  format: 'json' | 'markdown',
  unreadByTopic: Map<string, number[]>
): Promise<void> {
  const messages = getTopicMessages(siteId, streamName, topicName);

  if (messages.length === 0) {
    console.log(`  Skipping empty topic: #${streamName} > ${topicName}`);
    return;
  }

  const key = `${streamName}:${topicName}`;
  const unreadIds = unreadByTopic.get(key) || [];

  const exportFn = format === 'markdown' ? exportTopicToMarkdown : exportTopicToJson;
  const filepath = exportFn(siteName, streamName, topicName, messages, unreadIds);

  console.log(`  Exported: #${streamName} > ${topicName} (${messages.length} messages) -> ${filepath}`);
}
