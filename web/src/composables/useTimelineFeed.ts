import { computed, ref, watch, type Ref } from 'vue'

import type { ChatActivity, CodexTurnTiming, UiChatMessage } from '../types/api'
import type { ActivityDisplayItem, ActivityFeedItem, FeedEntry, MessageFeedItem, RenderEntry, TurnGroupEntry } from '../components/chat-timeline/types'
import { fileChangeRecords, hasActivityDetails, isApprovalActivity, isHiddenActivity } from '../components/chat-timeline/helpers'

interface UseTimelineFeedOptions {
  messages: Ref<UiChatMessage[]>
  activities: Ref<ChatActivity[]>
  turnTimings: Ref<CodexTurnTiming[] | undefined>
  isSending: Ref<boolean>
  showReasoningCards: Ref<boolean | undefined>
}

export function useTimelineFeed(options: UseTimelineFeedOptions) {
  const visibleActivities = computed(() =>
    options.activities.value.filter((activity) => !isHiddenActivity(activity, options.showReasoningCards.value)),
  )

  const activityOpenOverrides = ref<Record<string, boolean>>({})
  const turnGroupOpenOverrides = ref<Record<string, boolean>>({})

  const feedEntries = computed<FeedEntry[]>(() => {
    const sequenceValues = [...options.messages.value, ...visibleActivities.value].map(
      (item) => item.sequence,
    )
    const explicitSequences = sequenceValues.filter(
      (value): value is number => typeof value === 'number' && Number.isFinite(value),
    )
    const hasMissingSequences = explicitSequences.length !== sequenceValues.length

    if (!explicitSequences.length || hasMissingSequences) {
      let sortOrder = 0
      const lastMessage = options.messages.value[options.messages.value.length - 1]
      const trailingAssistantMessage =
        visibleActivities.value.length && lastMessage?.role === 'assistant' ? lastMessage : null
      const leadingMessages = trailingAssistantMessage
        ? options.messages.value.slice(0, -1)
        : options.messages.value

      const legacyMessages: MessageFeedItem[] = leadingMessages.map((message) => {
        sortOrder += 1000
        return {
          type: 'message',
          key: `message:${message.id}`,
          sortOrder,
          message,
        }
      })

      const legacyActivities: ActivityFeedItem[] = visibleActivities.value.flatMap((activity) => {
        const changes = fileChangeRecords(activity)

        if (changes.length) {
          return changes.map((change, index) => {
            const key = `activity:${activity.id}:change:${index}`
            sortOrder += 1
            return {
              type: 'activity',
              key,
              sortOrder,
              display: {
                key,
                sortOrder,
                activity,
                change,
              },
            }
          })
        }

        sortOrder += 1
        const key = `activity:${activity.id}`
        return [
          {
            type: 'activity',
            key,
            sortOrder,
            display: {
              key,
              sortOrder,
              activity,
            },
          },
        ]
      })

      const trailingMessages: MessageFeedItem[] = trailingAssistantMessage
        ? [
            {
              type: 'message',
              key: `message:${trailingAssistantMessage.id}`,
              sortOrder: sortOrder + 1000,
              message: trailingAssistantMessage,
            },
          ]
        : []

      return [...legacyMessages, ...legacyActivities, ...trailingMessages]
    }

    let fallbackSequence = explicitSequences.length ? Math.max(...explicitSequences) + 1 : 0

    const messages: MessageFeedItem[] = options.messages.value.map((message) => {
      const sequence =
        typeof message.sequence === 'number' && Number.isFinite(message.sequence)
          ? message.sequence
          : fallbackSequence++

      return {
        type: 'message',
        key: `message:${message.id}`,
        sortOrder: sequence * 1000 + 500,
        message,
      }
    })

    const activities: ActivityFeedItem[] = visibleActivities.value.flatMap((activity) => {
      const sequence =
        typeof activity.sequence === 'number' && Number.isFinite(activity.sequence)
          ? activity.sequence
          : fallbackSequence++
      const baseSortOrder = sequence * 1000
      const changes = fileChangeRecords(activity)

      if (changes.length) {
        return changes.map((change, index) => {
          const key = `activity:${activity.id}:change:${index}`
          const sortOrder = baseSortOrder + index + 1
          return {
            type: 'activity',
            key,
            sortOrder,
            display: {
              key,
              sortOrder,
              activity,
              change,
            },
          }
        })
      }

      const key = `activity:${activity.id}`
      return [
        {
          type: 'activity',
          key,
          sortOrder: baseSortOrder + 1,
          display: {
            key,
            sortOrder: baseSortOrder + 1,
            activity,
          },
        },
      ]
    })

    return [...activities, ...messages].sort((left, right) => {
      if (left.sortOrder !== right.sortOrder) {
        return left.sortOrder - right.sortOrder
      }
      if (left.type === right.type) {
        return left.key.localeCompare(right.key)
      }
      return left.type === 'activity' ? -1 : 1
    })
  })

  const latestAssistantSortOrder = computed(() => {
    const assistantOrders = feedEntries.value
      .filter((entry): entry is MessageFeedItem => entry.type === 'message' && entry.message.role === 'assistant')
      .map((entry) => entry.sortOrder)

    return assistantOrders.length ? Math.max(...assistantOrders) : Number.NEGATIVE_INFINITY
  })

  const latestCurrentTurnActivityKey = computed(() => {
    const activityEntries = feedEntries.value.filter(
      (entry): entry is ActivityFeedItem =>
        entry.type === 'activity' &&
        isCurrentTurnActivity(entry.display) &&
        hasActivityDetails(entry.display),
    )

    return activityEntries.length ? activityEntries[activityEntries.length - 1]?.display.key ?? null : null
  })

  const renderEntries = computed<RenderEntry[]>(() => {
    const entries: RenderEntry[] = []
    let activeUserMessage: MessageFeedItem | null = null
    let pendingTurnItems: Array<ActivityFeedItem | MessageFeedItem> = []
    let activeTurnIndex = -1

    function flushTurn() {
      if (!activeUserMessage) {
        if (pendingTurnItems.length) {
          entries.push(...pendingTurnItems)
        }

        pendingTurnItems = []
        return
      }

      entries.push(activeUserMessage)

      const assistantIndexes = pendingTurnItems.flatMap((entry, index) =>
        entry.type === 'message' && entry.message.role === 'assistant' ? [index] : [],
      )

      if (!assistantIndexes.length) {
        entries.push(...pendingTurnItems)
        activeUserMessage = null
        pendingTurnItems = []
        return
      }

      const finalAssistantIndex = assistantIndexes[assistantIndexes.length - 1] ?? -1
      const groupedItems = pendingTurnItems.slice(0, finalAssistantIndex)
      const finalAssistant = pendingTurnItems[finalAssistantIndex]
      const trailingItems = pendingTurnItems.slice(finalAssistantIndex + 1)

      if (groupedItems.length) {
        const firstSortOrder = groupedItems[0]?.sortOrder ?? 0
        const lastSortOrder = groupedItems[groupedItems.length - 1]?.sortOrder ?? firstSortOrder
        entries.push({
          type: 'turn-group',
          key: `turn-group:${firstSortOrder}:${lastSortOrder}`,
          sortOrder: firstSortOrder,
          turnIndex: activeTurnIndex,
          items: groupedItems,
        })
      }

      if (finalAssistant) {
        entries.push(finalAssistant)
      }
      if (trailingItems.length) {
        entries.push(...trailingItems)
      }

      activeUserMessage = null
      pendingTurnItems = []
    }

    for (const entry of feedEntries.value) {
      if (entry.type === 'message' && entry.message.role === 'user') {
        flushTurn()
        activeTurnIndex += 1
        activeUserMessage = entry
        continue
      }

      if (!activeUserMessage) {
        entries.push(entry)
        continue
      }

      pendingTurnItems.push(entry)
    }

    if (activeUserMessage) {
      flushTurn()
    }

    return entries
  })

  function isCurrentTurnActivity(display: ActivityDisplayItem) {
    if (!Number.isFinite(latestAssistantSortOrder.value)) {
      return true
    }
    return display.sortOrder > latestAssistantSortOrder.value
  }

  function shouldDefaultOpenActivity(display: ActivityDisplayItem) {
    if (isApprovalActivity(display.activity)) {
      return latestCurrentTurnActivityKey.value === display.key &&
        (display.activity.state === 'queued' || display.activity.state === 'running')
    }

    return options.isSending.value &&
      isCurrentTurnActivity(display) &&
      latestCurrentTurnActivityKey.value === display.key
  }

  function isActivityOpen(display: ActivityDisplayItem) {
    const override = activityOpenOverrides.value[display.key]
    if (typeof override === 'boolean') {
      return override
    }
    return shouldDefaultOpenActivity(display)
  }

  function onActivityToggle(display: ActivityDisplayItem, event: Event) {
    const target = event.target
    if (!(target instanceof HTMLDetailsElement)) {
      return
    }

    activityOpenOverrides.value = {
      ...activityOpenOverrides.value,
      [display.key]: target.open,
    }
  }

  function isTurnGroupOpen(group: TurnGroupEntry) {
    const override = turnGroupOpenOverrides.value[group.key]
    return typeof override === 'boolean' ? override : false
  }

  function onTurnGroupToggle(group: TurnGroupEntry, event: Event) {
    const target = event.target
    if (!(target instanceof HTMLDetailsElement)) {
      return
    }

    turnGroupOpenOverrides.value = {
      ...turnGroupOpenOverrides.value,
      [group.key]: target.open,
    }
  }

  function shouldShowFinalMessageSeparator(entry: MessageFeedItem, index: number) {
    if (entry.message.role !== 'assistant') {
      return false
    }

    const previousEntry = renderEntries.value[index - 1]
    return previousEntry?.type === 'turn-group' ? isTurnGroupOpen(previousEntry) : false
  }

  watch(
    latestAssistantSortOrder,
    (nextValue, previousValue) => {
      if (Number.isFinite(previousValue) && nextValue > previousValue) {
        activityOpenOverrides.value = {}
      }
    },
    { flush: 'post' },
  )

  watch(
    latestCurrentTurnActivityKey,
    (nextValue, previousValue) => {
      if (previousValue && nextValue && previousValue !== nextValue) {
        activityOpenOverrides.value = {}
      }
    },
    { flush: 'post' },
  )

  return {
    renderEntries,
    isActivityOpen,
    onActivityToggle,
    isTurnGroupOpen,
    onTurnGroupToggle,
    shouldShowFinalMessageSeparator,
    turnTimings: options.turnTimings,
  }
}
