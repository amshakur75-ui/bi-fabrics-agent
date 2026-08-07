import {
  and,
  asc,
  desc,
  eq,
  gt,
  gte,
  inArray,
  lt,
  sql,
  type SQL,
} from 'drizzle-orm';
import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';

import {
  chat,
  message,
  vote,
  type DBMessage,
  type Chat,
  type Vote,
} from './schema';
import type { VisibilityType } from '@chat-template/utils';
import { ChatSDKError } from '@chat-template/core/errors';
import type { LanguageModelV3Usage } from '@ai-sdk/provider';
import { isDatabaseAvailable } from './connection';
import { getAuthMethod, getAuthMethodDescription } from '@chat-template/auth';

// Optionally, if not using email/pass login, you can
// use the Drizzle adapter for Auth.js / NextAuth
// https://authjs.dev/reference/adapter/drizzle
let _db: ReturnType<typeof drizzle>;

const getOrInitializeDb = async () => {
  if (!isDatabaseAvailable()) {
    throw new Error(
      'Database configuration required. Please set PGDATABASE/PGHOST/PGUSER or POSTGRES_URL environment variables.',
    );
  }

  if (_db) return _db;

  const authMethod = getAuthMethod();
  if (authMethod === 'oauth' || authMethod === 'cli') {
    // Dynamic auth path - db will be initialized asynchronously
    console.log(
      `Using ${getAuthMethodDescription()} authentication for Postgres connection`,
    );
  } else if (process.env.POSTGRES_URL) {
    // Traditional connection string
    const client = postgres(process.env.POSTGRES_URL);
    _db = drizzle(client);
  }

  return _db;
};

// Helper to ensure db is initialized for dynamic auth connections
async function ensureDb() {
  const db = await getOrInitializeDb();
  // Always get a fresh DB instance for dynamic auth connections to handle token expiry
  const authMethod = getAuthMethod();
  if (authMethod === 'oauth' || authMethod === 'cli') {
    const authDescription = getAuthMethodDescription();
    console.log(`[ensureDb] Getting ${authDescription} database connection...`);
    try {
      // Import getDb for database connection
      const { getDb } = await import('./connection-pool.js');
      const database = await getDb();
      console.log(
        `[ensureDb] ${authDescription} db connection obtained successfully`,
      );
      return database;
    } catch (error) {
      console.error(
        `[ensureDb] Failed to get ${authDescription} connection:`,
        error,
      );
      throw error;
    }
  }

  // For static connections (POSTGRES_URL), use cached instance
  if (!db) {
    console.error('[ensureDb] DB is still null after initialization attempt!');
    throw new Error('Database connection could not be established');
  }
  return db;
}

export async function saveChat({
  id,
  userId,
  title,
  visibility,
}: {
  id: string;
  userId: string;
  title: string;
  visibility: VisibilityType;
}) {
  if (!isDatabaseAvailable()) {
    console.log('[saveChat] Database not available, skipping persistence');
    return;
  }

  try {
    return await (await ensureDb()).insert(chat).values({
      id,
      createdAt: new Date(),
      userId,
      title,
      visibility,
    });
  } catch (error) {
    console.error('[saveChat] Error saving chat:', error);
    throw new ChatSDKError('bad_request:database', 'Failed to save chat');
  }
}

export async function deleteChatById({ id }: { id: string }) {
  if (!isDatabaseAvailable()) {
    console.log('[deleteChatById] Database not available, skipping deletion');
    return null;
  }

  try {
    await (await ensureDb()).delete(message).where(eq(message.chatId, id));

    const [chatsDeleted] = await (await ensureDb())
      .delete(chat)
      .where(eq(chat.id, id))
      .returning();
    return chatsDeleted;
  } catch (_error) {
    throw new ChatSDKError(
      'bad_request:database',
      'Failed to delete chat by id',
    );
  }
}

export async function getChatsByUserId({
  id,
  limit,
  startingAfter,
  endingBefore,
}: {
  id: string;
  limit: number;
  startingAfter: string | null;
  endingBefore: string | null;
}) {
  if (!isDatabaseAvailable()) {
    console.log('[getChatsByUserId] Database not available, returning empty');
    return { chats: [], hasMore: false };
  }

  try {
    const extendedLimit = limit + 1;

    const query = async (whereCondition?: SQL<any>) => {
      const database = await ensureDb();

      return database
        .select()
        .from(chat)
        .where(
          whereCondition
            ? and(whereCondition, eq(chat.userId, id))
            : eq(chat.userId, id),
        )
        .orderBy(desc(chat.createdAt))
        .limit(extendedLimit);
    };

    let filteredChats: Array<Chat> = [];

    if (startingAfter) {
      console.log(
        '[getChatsByUserId] Fetching chat for startingAfter:',
        startingAfter,
      );
      const database = await ensureDb();
      const [selectedChat] = await database
        .select()
        .from(chat)
        .where(eq(chat.id, startingAfter))
        .limit(1);

      if (!selectedChat) {
        throw new ChatSDKError(
          'not_found:database',
          `Chat with id ${startingAfter} not found`,
        );
      }

      filteredChats = await query(gt(chat.createdAt, selectedChat.createdAt));
    } else if (endingBefore) {
      console.log(
        '[getChatsByUserId] Fetching chat for endingBefore:',
        endingBefore,
      );
      const database = await ensureDb();
      const [selectedChat] = await database
        .select()
        .from(chat)
        .where(eq(chat.id, endingBefore))
        .limit(1);

      if (!selectedChat) {
        throw new ChatSDKError(
          'not_found:database',
          `Chat with id ${endingBefore} not found`,
        );
      }

      filteredChats = await query(lt(chat.createdAt, selectedChat.createdAt));
    } else {
      console.log('[getChatsByUserId] Executing main query without pagination');
      filteredChats = await query();
    }

    const hasMore = filteredChats.length > limit;
    console.log(
      '[getChatsByUserId] Query successful, found',
      filteredChats.length,
      'chats',
    );

    return {
      chats: hasMore ? filteredChats.slice(0, limit) : filteredChats,
      hasMore,
    };
  } catch (error) {
    console.error('[getChatsByUserId] Error details:', error);
    console.error(
      '[getChatsByUserId] Error stack:',
      error instanceof Error ? error.stack : 'No stack available',
    );
    throw new ChatSDKError(
      'bad_request:database',
      'Failed to get chats by user id',
    );
  }
}

export async function getChatById({ id }: { id: string }) {
  if (!isDatabaseAvailable()) {
    console.log('[getChatById] Database not available, returning null');
    return null;
  }

  try {
    const [selectedChat] = await (await ensureDb())
      .select()
      .from(chat)
      .where(eq(chat.id, id));
    if (!selectedChat) {
      return null;
    }

    return selectedChat;
  } catch (_error) {
    throw new ChatSDKError('bad_request:database', 'Failed to get chat by id');
  }
}

export async function saveMessages({
  messages,
}: {
  messages: Array<DBMessage>;
}) {
  if (!isDatabaseAvailable()) {
    console.log('[saveMessages] Database not available, skipping persistence');
    return;
  }

  try {
    // Use upsert to handle both new messages and updates (e.g., MCP approval continuations)
    // When a message ID already exists, update its parts (which may have changed)
    // Using sql`excluded.X` to reference the values that would have been inserted
    return await (await ensureDb())
      .insert(message)
      .values(messages)
      .onConflictDoUpdate({
        target: message.id,
        set: {
          parts: sql`excluded.parts`,
          attachments: sql`excluded.attachments`,
          traceId: sql`excluded."traceId"`,
        },
      });
  } catch (_error) {
    console.error('[saveMessages] DB error:', _error);
    throw new ChatSDKError('bad_request:database', 'Failed to save messages');
  }
}

export async function getMessagesByChatId({ id }: { id: string }) {
  if (!isDatabaseAvailable()) {
    console.log(
      '[getMessagesByChatId] Database not available, returning empty',
    );
    return [];
  }

  try {
    return await (await ensureDb())
      .select()
      .from(message)
      .where(eq(message.chatId, id))
      .orderBy(asc(message.createdAt));
  } catch (_error) {
    console.error('[getMessagesByChatId] Database error:', _error);
    throw new ChatSDKError(
      'bad_request:database',
      'Failed to get messages by chat id',
    );
  }
}

export async function getMessageById({ id }: { id: string }) {
  if (!isDatabaseAvailable()) {
    console.log('[getMessageById] Database not available, returning empty');
    return [];
  }

  try {
    return await (await ensureDb())
      .select()
      .from(message)
      .where(eq(message.id, id));
  } catch (_error) {
    throw new ChatSDKError(
      'bad_request:database',
      'Failed to get message by id',
    );
  }
}

export async function deleteMessagesByChatIdAfterTimestamp({
  chatId,
  timestamp,
}: {
  chatId: string;
  timestamp: Date;
}) {
  if (!isDatabaseAvailable()) {
    console.log(
      '[deleteMessagesByChatIdAfterTimestamp] Database not available, skipping deletion',
    );
    return;
  }

  try {
    const messagesToDelete = await (await ensureDb())
      .select({ id: message.id })
      .from(message)
      .where(
        and(eq(message.chatId, chatId), gte(message.createdAt, timestamp)),
      );

    const messageIds = messagesToDelete.map((message) => message.id);

    if (messageIds.length > 0) {
      const db = await ensureDb();
      // Delete votes first to satisfy the Vote.messageId → Message.id FK constraint
      await db.delete(vote).where(inArray(vote.messageId, messageIds));
      return await db
        .delete(message)
        .where(
          and(eq(message.chatId, chatId), inArray(message.id, messageIds)),
        );
    }
  } catch (_error) {
    throw new ChatSDKError(
      'bad_request:database',
      'Failed to delete messages by chat id after timestamp',
    );
  }
}

export async function updateChatVisiblityById({
  chatId,
  visibility,
}: {
  chatId: string;
  visibility: 'private' | 'public';
}) {
  if (!isDatabaseAvailable()) {
    console.log(
      '[updateChatVisiblityById] Database not available, skipping update',
    );
    return;
  }

  try {
    return await (await ensureDb())
      .update(chat)
      .set({ visibility })
      .where(eq(chat.id, chatId));
  } catch (_error) {
    throw new ChatSDKError(
      'bad_request:database',
      'Failed to update chat visibility by id',
    );
  }
}


export async function updateChatTitleById({
  chatId,
  title,
}: {
  chatId: string;
  title: string;
}) {
  if (!isDatabaseAvailable()) {
    console.log('[updateChatTitleById] Database not available, skipping update');
    return;
  }

  try {
    return await (await ensureDb())
      .update(chat)
      .set({ title })
      .where(eq(chat.id, chatId));
  } catch (_error) {
    throw new ChatSDKError(
      'bad_request:database',
      'Failed to update chat title by id',
    );
  }
}

export async function updateChatLastContextById({
  chatId,
  context,
}: {
  chatId: string;
  // Store raw LanguageModelUsage to keep it simple
  context: LanguageModelV3Usage;
}) {
  if (!isDatabaseAvailable()) {
    console.log(
      '[updateChatLastContextById] Database not available, skipping update',
    );
    return;
  }

  try {
    return await (await ensureDb())
      .update(chat)
      .set({ lastContext: context })
      .where(eq(chat.id, chatId));
  } catch (error) {
    console.warn('Failed to update lastContext for chat', chatId, error);
    return;
  }
}

export async function voteMessage({
  chatId,
  messageId,
  type,
}: {
  chatId: string;
  messageId: string;
  type: 'up' | 'down';
}) {
  if (!isDatabaseAvailable()) {
    return;
  }

  const db = await ensureDb();
  await db
    .insert(vote)
    .values({ chatId, messageId, isUpvoted: type === 'up' })
    .onConflictDoUpdate({
      target: [vote.chatId, vote.messageId],
      set: { isUpvoted: type === 'up' },
    });
}

export async function getVotesByChatId({ id }: { id: string }): Promise<Vote[]> {
  if (!isDatabaseAvailable()) {
    return [];
  }

  const db = await ensureDb();
  return db.select().from(vote).where(eq(vote.chatId, id));
}

// ── Tier-2 alert ack / snooze (Step 6c) ─────────────────────────────────────
// The chat app writes ack/snooze here; the Tier-2 job reads it (ai_chatbot.alert_ack) to suppress
// the 48h reminders. Keyed by the alert's chat_id (the handle both sides share).

export type AlertAck = {
  status: 'acked' | 'snoozed' | 'resolved';
  snoozeUntil: string | null;
  resolutionNote?: string | null;
  updatedBy?: string | null;
  updatedAt?: string | null;
};

export async function resolveAlert({
  chatId,
  note,
  resolvedBy,
}: {
  chatId: string;
  note: string;
  resolvedBy?: string | null;
}): Promise<void> {
  if (!isDatabaseAvailable()) return;
  const db = await ensureDb();
  await db.execute(sql`
    INSERT INTO ai_chatbot.alert_ack (chat_id, status, resolution_note, updated_by, updated_at)
    VALUES (${chatId}, 'resolved', ${note}, ${resolvedBy ?? null}, now())
    ON CONFLICT (chat_id) DO UPDATE SET
      status = 'resolved',
      resolution_note = excluded.resolution_note,
      snooze_until = NULL,
      updated_by = excluded.updated_by,
      updated_at = now()
  `);
}

export async function reopenAlert(chatId: string): Promise<void> {
  // Reopen = clear the ticket state so reminders + alerts resume (Step 8 recurrence handling).
  await clearAlertAck(chatId);
}

export async function setAlertAck({
  chatId,
  status,
  snoozeUntil,
  updatedBy,
}: {
  chatId: string;
  status: 'acked' | 'snoozed';
  snoozeUntil?: string | null;
  updatedBy?: string | null;
}): Promise<void> {
  if (!isDatabaseAvailable()) return;
  const db = await ensureDb();
  await db.execute(sql`
    INSERT INTO ai_chatbot.alert_ack (chat_id, status, snooze_until, updated_by, updated_at)
    VALUES (${chatId}, ${status}, ${snoozeUntil ?? null}, ${updatedBy ?? null}, now())
    ON CONFLICT (chat_id) DO UPDATE SET
      status = excluded.status,
      snooze_until = excluded.snooze_until,
      updated_by = excluded.updated_by,
      updated_at = now()
  `);
}

export async function clearAlertAck(chatId: string): Promise<void> {
  if (!isDatabaseAvailable()) return;
  const db = await ensureDb();
  await db.execute(sql`DELETE FROM ai_chatbot.alert_ack WHERE chat_id = ${chatId}`);
}

// ── Part-7 read-path: chat-less tickets (incident_key-keyed ack) ───────────
// alert_ticket rows are now written even when chat creation failed upstream (chat_id NULL),
// keyed by incident_key. alert_ack gained a parallel, nullable `incident_key` column (see
// packages/db/migrations/manual/0001_alert_ack_incident_key.sql) so those tickets can still be
// acked/resolved/reopened without a chat. All functions below degrade to a no-op / empty result
// if that column doesn't exist yet on an older deployment — never break the existing chat-backed
// path.

export async function resolveAlertByIncident({
  incidentKey,
  note,
  resolvedBy,
}: {
  incidentKey: string;
  note: string;
  resolvedBy?: string | null;
}): Promise<void> {
  if (!isDatabaseAvailable()) return;
  const db = await ensureDb();
  await db.execute(sql`
    INSERT INTO ai_chatbot.alert_ack (incident_key, status, resolution_note, updated_by, updated_at)
    VALUES (${incidentKey}, 'resolved', ${note}, ${resolvedBy ?? null}, now())
    ON CONFLICT (incident_key) DO UPDATE SET
      status = 'resolved',
      resolution_note = excluded.resolution_note,
      snooze_until = NULL,
      updated_by = excluded.updated_by,
      updated_at = now()
  `);
}

export async function reopenAlertByIncident(incidentKey: string): Promise<void> {
  await clearAlertAckByIncident(incidentKey);
}

export async function setAlertAckByIncident({
  incidentKey,
  status,
  snoozeUntil,
  updatedBy,
}: {
  incidentKey: string;
  status: 'acked' | 'snoozed';
  snoozeUntil?: string | null;
  updatedBy?: string | null;
}): Promise<void> {
  if (!isDatabaseAvailable()) return;
  const db = await ensureDb();
  await db.execute(sql`
    INSERT INTO ai_chatbot.alert_ack (incident_key, status, snooze_until, updated_by, updated_at)
    VALUES (${incidentKey}, ${status}, ${snoozeUntil ?? null}, ${updatedBy ?? null}, now())
    ON CONFLICT (incident_key) DO UPDATE SET
      status = excluded.status,
      snooze_until = excluded.snooze_until,
      updated_by = excluded.updated_by,
      updated_at = now()
  `);
}

export async function clearAlertAckByIncident(incidentKey: string): Promise<void> {
  if (!isDatabaseAvailable()) return;
  const db = await ensureDb();
  await db.execute(
    sql`DELETE FROM ai_chatbot.alert_ack WHERE incident_key = ${incidentKey}`,
  );
}

export async function getAlertAckMapByIncidentKeys(
  incidentKeys: string[],
): Promise<Record<string, AlertAck>> {
  const out: Record<string, AlertAck> = {};
  if (!isDatabaseAvailable() || incidentKeys.length === 0) return out;
  const db = await ensureDb();
  let rows: unknown = [];
  try {
    rows = await db.execute(sql`
      SELECT incident_key, status, snooze_until, resolution_note, updated_by, updated_at
      FROM ai_chatbot.alert_ack
      WHERE incident_key IN (${sql.join(
        incidentKeys.map((id) => sql`${id}`),
        sql`, `,
      )})
    `);
  } catch {
    // incident_key column may not exist yet on an older deployment — degrade to no ack state.
    return out;
  }
  for (const r of rows as unknown as Array<{
    incident_key: string;
    status: string;
    snooze_until: string | null;
    resolution_note: string | null;
    updated_by: string | null;
    updated_at: string | null;
  }>) {
    const status =
      r.status === 'acked' || r.status === 'resolved' ? r.status : 'snoozed';
    out[r.incident_key] = {
      status,
      snoozeUntil: r.snooze_until,
      resolutionNote: r.resolution_note,
      updatedBy: r.updated_by,
      updatedAt: r.updated_at,
    };
  }
  return out;
}

export async function getAlertAckMap(
  chatIds: string[],
): Promise<Record<string, AlertAck>> {
  const out: Record<string, AlertAck> = {};
  if (!isDatabaseAvailable() || chatIds.length === 0) return out;
  const db = await ensureDb();
  const rows = await db.execute(sql`
    SELECT chat_id, status, snooze_until, resolution_note, updated_by, updated_at
    FROM ai_chatbot.alert_ack
    WHERE chat_id IN (${sql.join(
      chatIds.map((id) => sql`${id}`),
      sql`, `,
    )})
  `);
  for (const r of rows as unknown as Array<{
    chat_id: string;
    status: string;
    snooze_until: string | null;
    resolution_note: string | null;
    updated_by: string | null;
    updated_at: string | null;
  }>) {
    const status =
      r.status === 'acked' || r.status === 'resolved' ? r.status : 'snoozed';
    out[r.chat_id] = {
      status,
      snoozeUntil: r.snooze_until,
      resolutionNote: r.resolution_note,
      updatedBy: r.updated_by,
      updatedAt: r.updated_at,
    };
  }
  return out;
}

// ── Tier-2 alert ticket detail (Step 9) ─────────────────────────────────────
// The Tier-2 job WRITES this (ai_chatbot.alert_ticket) and the app READS it — the reverse of the
// ack boundary above — so the Alerts sidebar can show what/where/since-when/active for each ticket.
// Lifecycle (open/investigating/resolved) is derived in the UI from the ack map, not stored here.

export type AlertTicket = {
  checkType: string | null;
  severity: string | null;
  resource: string | null;
  workspace: string | null;
  detail: string | null;
  firstDetected: string | null;
  currentlyActive: boolean | null;
};

export async function getAlertTicketMap(
  chatIds: string[],
): Promise<Record<string, AlertTicket>> {
  const out: Record<string, AlertTicket> = {};
  if (!isDatabaseAvailable() || chatIds.length === 0) return out;
  const db = await ensureDb();
  let rows: unknown = [];
  try {
    rows = await db.execute(sql`
      SELECT chat_id, check_type, severity, resource, workspace, detail,
             first_detected, currently_active
      FROM ai_chatbot.alert_ticket
      WHERE chat_id IN (${sql.join(
        chatIds.map((id) => sql`${id}`),
        sql`, `,
      )})
    `);
  } catch {
    // Table may not exist yet on an older deployment — degrade to no detail, never break the list.
    return out;
  }
  for (const r of rows as unknown as Array<{
    chat_id: string;
    check_type: string | null;
    severity: string | null;
    resource: string | null;
    workspace: string | null;
    detail: string | null;
    first_detected: string | null;
    currently_active: boolean | null;
  }>) {
    out[r.chat_id] = {
      checkType: r.check_type,
      severity: r.severity,
      resource: r.resource,
      workspace: r.workspace,
      detail: r.detail,
      firstDetected: r.first_detected,
      currentlyActive: r.currently_active,
    };
  }
  return out;
}

// Part-7 fix: alert_ticket rows written with chat_id = NULL (chat creation failed upstream, but
// the finding still needs to surface) are invisible to getAlertTicketMap (it filters by chat_id
// IN (...)). This reads those chat-less rows directly, keyed by their own incident_key — there is
// no chat to join through. Additive: never touches/affects the chat-backed path above.
export type ChatlessAlertTicket = AlertTicket & { incidentKey: string };

export async function getChatlessAlertTickets(
  limit = 50,
): Promise<ChatlessAlertTicket[]> {
  if (!isDatabaseAvailable()) return [];
  const db = await ensureDb();
  let rows: unknown = [];
  try {
    rows = await db.execute(sql`
      SELECT incident_key, check_type, severity, resource, workspace, detail,
             first_detected, currently_active
      FROM ai_chatbot.alert_ticket
      WHERE chat_id IS NULL
      ORDER BY first_detected DESC NULLS LAST
      LIMIT ${limit}
    `);
  } catch {
    // Table may not exist yet, or predates the Part-7 nullable-chat_id migration — degrade to
    // none, never break the (chat-backed) list.
    return [];
  }
  return (
    rows as unknown as Array<{
      incident_key: string;
      check_type: string | null;
      severity: string | null;
      resource: string | null;
      workspace: string | null;
      detail: string | null;
      first_detected: string | null;
      currently_active: boolean | null;
    }>
  ).map((r) => ({
    incidentKey: r.incident_key,
    checkType: r.check_type,
    severity: r.severity,
    resource: r.resource,
    workspace: r.workspace,
    detail: r.detail,
    firstDetected: r.first_detected,
    currentlyActive: r.currently_active,
  }));
}
