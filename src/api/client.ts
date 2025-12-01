import { getSiteCredentials, type SiteCredentials } from '../config/credentials.js';
import type {
  GetMessagesResponse,
  RegisterResponse,
  NarrowFilter,
  ZulipMessage,
  UnreadMsgs,
  StreamInfo,
} from './types.js';

export class ZulipClient {
  private credentials: SiteCredentials;
  private siteName: string;

  constructor(siteName?: string) {
    this.siteName = siteName || 'leanprover';
    this.credentials = getSiteCredentials(siteName);
  }

  get siteUrl(): string {
    return this.credentials.site;
  }

  get site(): string {
    return this.siteName;
  }

  private get authHeader(): string {
    const auth = Buffer.from(`${this.credentials.email}:${this.credentials.api_key}`).toString('base64');
    return `Basic ${auth}`;
  }

  private async request<T>(
    method: 'GET' | 'POST',
    endpoint: string,
    params?: Record<string, string | number | boolean>
  ): Promise<T> {
    const url = new URL(`${this.credentials.site}/api/v1${endpoint}`);

    const options: RequestInit = {
      method,
      headers: {
        Authorization: this.authHeader,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
    };

    if (method === 'GET' && params) {
      for (const [key, value] of Object.entries(params)) {
        url.searchParams.set(key, String(value));
      }
    } else if (method === 'POST' && params) {
      const body = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        body.set(key, String(value));
      }
      options.body = body.toString();
    }

    const response = await fetch(url.toString(), options);

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Zulip API error ${response.status}: ${text}`);
    }

    const data = await response.json() as T;
    return data;
  }

  /**
   * Register an event queue and get initial state including unread messages
   */
  async register(): Promise<{
    unreadMsgs: UnreadMsgs;
    subscriptions: StreamInfo[];
  }> {
    const response = await this.request<RegisterResponse>('POST', '/register', {
      fetch_event_types: JSON.stringify(['message', 'subscription']),
      event_types: JSON.stringify([]),
      apply_markdown: 'false',
    });

    if (response.result !== 'success') {
      throw new Error(`Register failed: ${response.msg}`);
    }

    return {
      unreadMsgs: response.unread_msgs || { pms: [], streams: [], huddles: [], mentions: [], count: 0 },
      subscriptions: response.subscriptions || [],
    };
  }

  /**
   * Get messages with optional filtering
   */
  async getMessages(options: {
    narrow?: NarrowFilter[];
    anchor?: 'newest' | 'oldest' | 'first_unread' | number;
    numBefore?: number;
    numAfter?: number;
  } = {}): Promise<ZulipMessage[]> {
    const {
      narrow = [],
      anchor = 'newest',
      numBefore = 0,
      numAfter = 100,
    } = options;

    const response = await this.request<GetMessagesResponse>('GET', '/messages', {
      narrow: JSON.stringify(narrow),
      anchor: String(anchor),
      num_before: numBefore,
      num_after: numAfter,
    });

    if (response.result !== 'success') {
      throw new Error(`Get messages failed: ${response.msg}`);
    }

    return response.messages;
  }

  /**
   * Get all messages in a stream/topic, handling pagination
   */
  async getTopicMessages(
    streamName: string,
    topicName: string,
    options: { afterMessageId?: number; verbose?: boolean } = {}
  ): Promise<ZulipMessage[]> {
    const narrow: NarrowFilter[] = [
      { operator: 'stream', operand: streamName },
      { operator: 'topic', operand: topicName },
    ];

    const allMessages: ZulipMessage[] = [];
    let anchor: 'oldest' | number = options.afterMessageId ? options.afterMessageId + 1 : 'oldest';
    const batchSize = 1000;

    while (true) {
      const response = await this.request<GetMessagesResponse>('GET', '/messages', {
        narrow: JSON.stringify(narrow),
        anchor: String(anchor),
        num_before: 0,
        num_after: batchSize,
      });

      if (response.result !== 'success') {
        throw new Error(`Get messages failed: ${response.msg}`);
      }

      // Filter out the anchor message if we used a specific ID
      const newMessages = typeof anchor === 'number'
        ? response.messages.filter(m => m.id > anchor)
        : response.messages;

      if (newMessages.length === 0) {
        break;
      }

      allMessages.push(...newMessages);

      if (options.verbose) {
        process.stderr.write(`  Fetched ${allMessages.length} messages...\r`);
      }

      if (response.found_newest || newMessages.length < batchSize) {
        break;
      }

      anchor = newMessages[newMessages.length - 1].id;
    }

    if (options.verbose) {
      process.stderr.write('\n');
    }

    return allMessages;
  }
}
