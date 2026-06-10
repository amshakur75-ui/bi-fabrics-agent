import { buildTeamsCard } from '../core/teams-card.js';

/**
 * Teams DeliveryPort. The HTTP client is injected so it is testable offline and
 * swappable at transfer (real client posts to the Azure Bot Service / incoming webhook).
 * @param {{ http: { postJson: (url:string, body:object) => Promise<any> }, webhookUrl: string }} deps
 * @returns {{ deliver: (envelope:object) => Promise<object> }}
 */
export function createTeamsDelivery({ http, webhookUrl }) {
  return {
    async deliver(envelope) {
      const card = buildTeamsCard(envelope);
      await http.postJson(webhookUrl, card);
      return { delivered: true, target: webhookUrl, sections: card.sections.length };
    },
  };
}
