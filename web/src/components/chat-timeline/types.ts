import type { ChatActivity, FileChangeRecord, UiChatMessage } from '../../types/api'

export interface MessageFeedItem {
  type: 'message'
  key: string
  sortOrder: number
  message: UiChatMessage
}

export interface ActivityDisplayItem {
  key: string
  sortOrder: number
  activity: ChatActivity
  change?: FileChangeRecord
}

export interface ActivityFeedItem {
  type: 'activity'
  key: string
  sortOrder: number
  display: ActivityDisplayItem
}

export interface TurnGroupEntry {
  type: 'turn-group'
  key: string
  sortOrder: number
  turnIndex: number
  items: Array<ActivityFeedItem | MessageFeedItem>
}

export interface ActivitySummaryParts {
  verb: string
  text: string
  verbClass: string
}

export type FeedEntry = MessageFeedItem | ActivityFeedItem
export type RenderEntry = FeedEntry | TurnGroupEntry
