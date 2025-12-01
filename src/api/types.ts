// Zulip API response types

export interface ZulipMessage {
  id: number;
  sender_id: number;
  sender_email: string;
  sender_full_name: string;
  content: string;
  content_type: string;
  timestamp: number;
  subject: string; // topic name
  display_recipient: string | ZulipUser[]; // stream name or list of users for DMs
  type: 'stream' | 'private';
  stream_id?: number;
}

export interface ZulipUser {
  id: number;
  email: string;
  full_name: string;
}

export interface GetMessagesResponse {
  result: 'success' | 'error';
  msg: string;
  messages: ZulipMessage[];
  found_oldest: boolean;
  found_newest: boolean;
  found_anchor: boolean;
  history_limited: boolean;
}

export interface UnreadStreamInfo {
  stream_id: number;
  topic: string;
  unread_message_ids: number[];
}

export interface UnreadPMInfo {
  other_user_id: number;
  unread_message_ids: number[];
}

export interface UnreadHuddleInfo {
  user_ids_string: string;
  unread_message_ids: number[];
}

export interface UnreadMsgs {
  pms: UnreadPMInfo[];
  streams: UnreadStreamInfo[];
  huddles: UnreadHuddleInfo[];
  mentions: number[];
  count: number;
}

export interface StreamInfo {
  stream_id: number;
  name: string;
  description: string;
}

export interface RegisterResponse {
  result: 'success' | 'error';
  msg: string;
  queue_id: string;
  last_event_id: number;
  unread_msgs?: UnreadMsgs;
  subscriptions?: StreamInfo[];
}

export interface NarrowFilter {
  operator: 'stream' | 'topic' | 'sender' | 'search' | 'is' | 'channel';
  operand: string;
}
