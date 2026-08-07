import {
  Router,
  type Request,
  type Response,
  type Router as RouterType,
} from 'express';
import { authMiddleware, requireAuth } from '../middleware/auth';
import {
  getChatsByUserId,
  isDatabaseAvailable,
  getAlertAckMap,
  getAlertTicketMap,
  setAlertAck,
  clearAlertAck,
  resolveAlert,
  reopenAlert,
  getChatlessAlertTickets,
  getAlertAckMapByIncidentKeys,
  setAlertAckByIncident,
  clearAlertAckByIncident,
  resolveAlertByIncident,
  reopenAlertByIncident,
} from '@chat-template/db';

export const alertsRouter: RouterType = Router();

alertsRouter.use(authMiddleware);

// System user that owns Tier-2 alert conversations (written by fabric_audit_agent's alert path).
// These are public/shared: any authenticated app user can list + open them.
const ALERT_USER_ID = 'fabric-audit-agent';

/**
 * GET /api/alerts - shared Tier-2 alert conversations (system-owned, public), newest first, each
 * annotated with its ack/snooze state so the sidebar can show it.
 */
alertsRouter.get('/', requireAuth, async (req: Request, res: Response) => {
  if (!isDatabaseAvailable()) {
    return res.status(204).end();
  }

  const limit = Number.parseInt((req.query.limit as string) || '20');
  try {
    const result = await getChatsByUserId({
      id: ALERT_USER_ID,
      limit,
      startingAfter: null,
      endingBefore: null,
    });
    const chatIds = result.chats.map((c) => c.id);
    const [ackMap, ticketMap, chatlessTickets] = await Promise.all([
      getAlertAckMap(chatIds),
      getAlertTicketMap(chatIds),
      getChatlessAlertTickets(limit),
    ]);
    const chatlessIncidentKeys = chatlessTickets.map((t) => t.incidentKey);
    const chatlessAckMap =
      await getAlertAckMapByIncidentKeys(chatlessIncidentKeys);

    // Chat-backed tickets (unchanged shape, now also carrying id/hasChat so the client can treat
    // both kinds uniformly).
    const chatBacked = result.chats.map((c) => ({
      ...c,
      id: c.id,
      hasChat: true as const,
      incidentKey: null as string | null,
      ack: ackMap[c.id] ?? null,
      ticket: ticketMap[c.id] ?? null,
    }));

    // Part-7 read-path: chat-less tickets (alert_ticket.chat_id IS NULL) — no Chat row exists, so
    // there's no title/createdAt to reuse. Identity is incident_key; the client falls back to a
    // fresh-chat deep-link (?query=) instead of navigating to /chat/:id for these.
    const chatless = chatlessTickets.map((t) => ({
      id: t.incidentKey,
      title:
        [t.checkType, t.resource ?? t.workspace].filter(Boolean).join(': ') ||
        'Alert',
      createdAt: t.firstDetected ?? null,
      hasChat: false as const,
      incidentKey: t.incidentKey,
      ack: chatlessAckMap[t.incidentKey] ?? null,
      ticket: {
        checkType: t.checkType,
        severity: t.severity,
        resource: t.resource,
        workspace: t.workspace,
        detail: t.detail,
        firstDetected: t.firstDetected,
        currentlyActive: t.currentlyActive,
      },
    }));

    const chats = [...chatBacked, ...chatless].sort((a, b) => {
      const at = a.ticket?.firstDetected ?? a.createdAt ?? '';
      const bt = b.ticket?.firstDetected ?? b.createdAt ?? '';
      return bt.localeCompare(at);
    });

    res.json({ ...result, chats });
  } catch (error) {
    console.error('[/api/alerts] Error in handler:', error);
    res.status(500).json({ error: 'Failed to fetch alerts' });
  }
});

/** POST /api/alerts/:chatId/ack - acknowledge an alert (stops its 48h reminders). */
alertsRouter.post(
  '/:chatId/ack',
  requireAuth,
  async (req: Request, res: Response) => {
    try {
      await setAlertAck({
        chatId: req.params.chatId,
        status: 'acked',
        snoozeUntil: null,
        updatedBy: req.session?.user.email ?? req.session?.user.id ?? null,
      });
      res.json({ ok: true, status: 'acked' });
    } catch (error) {
      console.error('[/api/alerts/:chatId/ack] Error:', error);
      res.status(500).json({ error: 'Failed to acknowledge alert' });
    }
  },
);

/** POST /api/alerts/:chatId/snooze - snooze reminders for N days (default 7). */
alertsRouter.post(
  '/:chatId/snooze',
  requireAuth,
  async (req: Request, res: Response) => {
    const days = Math.min(Math.max(Number(req.body?.days) || 7, 1), 90);
    const snoozeUntil = new Date(Date.now() + days * 86400000).toISOString();
    try {
      await setAlertAck({
        chatId: req.params.chatId,
        status: 'snoozed',
        snoozeUntil,
        updatedBy: req.session?.user.email ?? req.session?.user.id ?? null,
      });
      res.json({ ok: true, status: 'snoozed', snoozeUntil });
    } catch (error) {
      console.error('[/api/alerts/:chatId/snooze] Error:', error);
      res.status(500).json({ error: 'Failed to snooze alert' });
    }
  },
);

/** POST /api/alerts/:chatId/resolve - resolve a ticket with a required note (Step 8/9). */
alertsRouter.post(
  '/:chatId/resolve',
  requireAuth,
  async (req: Request, res: Response) => {
    const note = String(req.body?.note ?? '').trim();
    if (!note) {
      return res.status(400).json({ error: 'A resolution note is required.' });
    }
    try {
      await resolveAlert({
        chatId: req.params.chatId,
        note,
        resolvedBy: req.session?.user.email ?? req.session?.user.id ?? null,
      });
      res.json({ ok: true, status: 'resolved' });
    } catch (error) {
      console.error('[/api/alerts/:chatId/resolve] Error:', error);
      res.status(500).json({ error: 'Failed to resolve ticket' });
    }
  },
);

/** POST /api/alerts/:chatId/reopen - reopen a resolved ticket (reminders + alerts resume). */
alertsRouter.post(
  '/:chatId/reopen',
  requireAuth,
  async (req: Request, res: Response) => {
    try {
      await reopenAlert(req.params.chatId);
      res.json({ ok: true, status: 'open' });
    } catch (error) {
      console.error('[/api/alerts/:chatId/reopen] Error:', error);
      res.status(500).json({ error: 'Failed to reopen ticket' });
    }
  },
);

/** DELETE /api/alerts/:chatId/ack - clear an ack/snooze (reminders resume). */
alertsRouter.delete(
  '/:chatId/ack',
  requireAuth,
  async (req: Request, res: Response) => {
    try {
      await clearAlertAck(req.params.chatId);
      res.json({ ok: true, status: null });
    } catch (error) {
      console.error('[/api/alerts/:chatId/ack DELETE] Error:', error);
      res.status(500).json({ error: 'Failed to clear alert state' });
    }
  },
);

// ── Part-7 read-path: incident-key-keyed actions for chat-less tickets ─────
// Mirrors the /:chatId/* routes above exactly, but keyed by the alert_ticket's incident_key
// instead of a chat_id (there is no chat for these tickets — see getChatlessAlertTickets).

/** POST /api/alerts/by-incident/:incidentKey/resolve - resolve a chat-less ticket. */
alertsRouter.post(
  '/by-incident/:incidentKey/resolve',
  requireAuth,
  async (req: Request, res: Response) => {
    const note = String(req.body?.note ?? '').trim();
    if (!note) {
      return res.status(400).json({ error: 'A resolution note is required.' });
    }
    try {
      await resolveAlertByIncident({
        incidentKey: req.params.incidentKey,
        note,
        resolvedBy: req.session?.user.email ?? req.session?.user.id ?? null,
      });
      res.json({ ok: true, status: 'resolved' });
    } catch (error) {
      console.error(
        '[/api/alerts/by-incident/:incidentKey/resolve] Error:',
        error,
      );
      res.status(500).json({ error: 'Failed to resolve ticket' });
    }
  },
);

/** POST /api/alerts/by-incident/:incidentKey/reopen - reopen a resolved chat-less ticket. */
alertsRouter.post(
  '/by-incident/:incidentKey/reopen',
  requireAuth,
  async (req: Request, res: Response) => {
    try {
      await reopenAlertByIncident(req.params.incidentKey);
      res.json({ ok: true, status: 'open' });
    } catch (error) {
      console.error(
        '[/api/alerts/by-incident/:incidentKey/reopen] Error:',
        error,
      );
      res.status(500).json({ error: 'Failed to reopen ticket' });
    }
  },
);

/** POST /api/alerts/by-incident/:incidentKey/ack - acknowledge a chat-less ticket. */
alertsRouter.post(
  '/by-incident/:incidentKey/ack',
  requireAuth,
  async (req: Request, res: Response) => {
    try {
      await setAlertAckByIncident({
        incidentKey: req.params.incidentKey,
        status: 'acked',
        snoozeUntil: null,
        updatedBy: req.session?.user.email ?? req.session?.user.id ?? null,
      });
      res.json({ ok: true, status: 'acked' });
    } catch (error) {
      console.error('[/api/alerts/by-incident/:incidentKey/ack] Error:', error);
      res.status(500).json({ error: 'Failed to acknowledge alert' });
    }
  },
);

/** DELETE /api/alerts/by-incident/:incidentKey/ack - clear ack/snooze for a chat-less ticket. */
alertsRouter.delete(
  '/by-incident/:incidentKey/ack',
  requireAuth,
  async (req: Request, res: Response) => {
    try {
      await clearAlertAckByIncident(req.params.incidentKey);
      res.json({ ok: true, status: null });
    } catch (error) {
      console.error(
        '[/api/alerts/by-incident/:incidentKey/ack DELETE] Error:',
        error,
      );
      res.status(500).json({ error: 'Failed to clear alert state' });
    }
  },
);
