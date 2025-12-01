import { ZulipClient } from '../api/client.js';
import {
  getOrCreateSite,
  getOrCreateStream,
  getOrCreateTopic,
  getTopicLastMessageId,
  updateTopicLastMessageId,
  insertMessages,
  clearUnreadMessages,
  insertUnreadMessages,
  getUnreadTopics,
  updateSiteLastSync,
  getTopicMessages,
} from '../storage/database.js';
import { exportTopicToJson } from '../storage/json-export.js';
import { listSites, getDefaultSite } from '../config/credentials.js';

export interface SyncOptions {
  site?: string;
  all?: boolean;
  verbose?: boolean;
}

export async function syncCommand(options: SyncOptions): Promise<void> {
  const sites = options.all ? listSites() : [options.site || getDefaultSite()];

  for (const siteName of sites) {
    await syncSite(siteName, options.verbose || false);

    if (sites.length > 1) {
      console.log('');
    }
  }
}

async function syncSite(siteName: string, verbose: boolean): Promise<void> {
  const client = new ZulipClient(siteName);

  console.log(`Syncing ${client.siteUrl}...`);

  // Get current unread state
  const { unreadMsgs, subscriptions } = await client.register();

  // Build stream ID to name map
  const streamMap = new Map<number, string>();
  for (const sub of subscriptions) {
    streamMap.set(sub.stream_id, sub.name);
  }

  // Store site and unread state in database
  const siteId = getOrCreateSite(siteName, client.siteUrl);

  // Update unread messages in database
  clearUnreadMessages(siteId);
  insertUnreadMessages(siteId, unreadMsgs.streams, streamMap);

  // Get unique topics with unread messages
  const unreadTopics = getUnreadTopics(siteId);

  if (unreadTopics.length === 0) {
    console.log('No unread messages to sync.');
    updateSiteLastSync(siteId);
    return;
  }

  console.log(`Found ${unreadTopics.length} topics with unread messages.`);
  console.log('');

  let totalNewMessages = 0;

  for (const topic of unreadTopics) {
    const streamName = topic.streamName;
    const topicName = topic.topicName;

    if (verbose) {
      console.log(`Syncing #${streamName} > ${topicName}...`);
    }

    // Get or create stream and topic in database
    const streamDbId = getOrCreateStream(siteId, topic.streamId, streamName);
    const topicDbId = getOrCreateTopic(streamDbId, topicName);

    // Get the last message ID we have for incremental sync
    const lastMessageId = getTopicLastMessageId(topicDbId);

    // Fetch messages from Zulip
    const messages = await client.getTopicMessages(streamName, topicName, {
      afterMessageId: lastMessageId || undefined,
      verbose,
    });

    if (messages.length > 0) {
      // Insert messages into database
      const inserted = insertMessages(topicDbId, messages);
      totalNewMessages += inserted;

      // Update last message ID
      const maxMessageId = Math.max(...messages.map(m => m.id));
      updateTopicLastMessageId(topicDbId, maxMessageId);

      if (verbose) {
        console.log(`  Stored ${inserted} new messages (${messages.length} fetched)`);
      }

      // Export to JSON
      const allStoredMessages = getTopicMessages(siteId, streamName, topicName);
      const exportPath = exportTopicToJson(
        siteName,
        streamName,
        topicName,
        allStoredMessages,
        topic.messageIds
      );

      if (verbose) {
        console.log(`  Exported to ${exportPath}`);
      }
    } else if (verbose) {
      console.log(`  No new messages`);
    }
  }

  updateSiteLastSync(siteId);

  console.log('');
  console.log(`Sync complete. ${totalNewMessages} new messages stored.`);
}
