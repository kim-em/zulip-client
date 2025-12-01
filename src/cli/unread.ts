import { ZulipClient } from '../api/client.js';
import {
  getOrCreateSite,
  clearUnreadMessages,
  insertUnreadMessages,
  getUnreadSummary,
  getTotalUnreadCount,
} from '../storage/database.js';
import { listSites, getDefaultSite } from '../config/credentials.js';

export interface UnreadOptions {
  site?: string;
  all?: boolean;
}

export async function unreadCommand(options: UnreadOptions): Promise<void> {
  const sites = options.all ? listSites() : [options.site || getDefaultSite()];

  for (const siteName of sites) {
    await showUnreadForSite(siteName);

    if (sites.length > 1) {
      console.log('');
    }
  }
}

async function showUnreadForSite(siteName: string): Promise<void> {
  const client = new ZulipClient(siteName);

  console.log(`Fetching unread messages from ${client.siteUrl}...`);

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

  // Display summary
  const totalCount = getTotalUnreadCount(siteId);
  const summary = getUnreadSummary(siteId);

  console.log('');
  console.log(`${client.siteUrl} - ${totalCount} unread messages`);
  console.log('');

  if (summary.length === 0) {
    console.log('  No unread messages in streams.');
    return;
  }

  for (const stream of summary) {
    console.log(`#${stream.streamName} (${stream.totalCount} unread)`);
    for (const topic of stream.topics) {
      console.log(`  └─ ${topic.topicName} (${topic.count})`);
    }
  }

  // Also mention DMs and mentions if present
  if (unreadMsgs.pms.length > 0) {
    const pmCount = unreadMsgs.pms.reduce((sum, pm) => sum + pm.unread_message_ids.length, 0);
    console.log('');
    console.log(`Direct messages: ${pmCount} unread`);
  }

  if (unreadMsgs.mentions.length > 0) {
    console.log('');
    console.log(`Mentions: ${unreadMsgs.mentions.length} unread`);
  }
}
