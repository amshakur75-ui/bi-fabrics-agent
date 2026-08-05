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
  setAlertAck,
  clearAlertAck,
  resolveAlert,
  reopenAlert,
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
    const ackMap = await getAlertAckMap(result.chats.map((c) => c.id));
    res.json({
      ...result,
      chats: result.chats.map((c) => ({ ...c, ack: ackMap[c.id] ?? null })),
    });
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
