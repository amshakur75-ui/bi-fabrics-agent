import {
  Router,
  type Request,
  type Response,
  type Router as RouterType,
} from 'express';
import { authMiddleware, requireAuth } from '../middleware/auth';
import { getChatsByUserId, isDatabaseAvailable } from '@chat-template/db';

export const alertsRouter: RouterType = Router();

alertsRouter.use(authMiddleware);

// System user that owns Tier-2 alert conversations (written by fabric_audit_agent's alert path).
// These are public/shared: any authenticated app user can list + open them.
const ALERT_USER_ID = 'fabric-audit-agent';

/**
 * GET /api/alerts - shared Tier-2 alert conversations (system-owned, public), newest first.
 */
alertsRouter.get('/', requireAuth, async (req: Request, res: Response) => {
  if (!isDatabaseAvailable()) {
    return res.status(204).end();
  }

  const limit = Number.parseInt((req.query.limit as string) || '20');
  try {
    const chats = await getChatsByUserId({
      id: ALERT_USER_ID,
      limit,
      startingAfter: null,
      endingBefore: null,
    });
    res.json(chats);
  } catch (error) {
    console.error('[/api/alerts] Error in handler:', error);
    res.status(500).json({ error: 'Failed to fetch alerts' });
  }
});
