import { toFacts } from '../core/mappers/index.js';

/** Follow `nextLink` pages, accumulating `.value`. Pure-ish (uses injected http). */
export async function fetchAllPages(http, url) {
  const all = [];
  let next = url;
  let guard = 0;
  while (next && guard++ < 1000) {
    const page = await http.getJson(next);
    if (Array.isArray(page?.value)) all.push(...page.value);
    else if (page != null) all.push(page);
    next = page?.nextLink ?? null;
  }
  if (guard >= 1000) console.warn('[fetchAllPages] page guard reached — results may be truncated', url);
  return all;
}

/**
 * REST CollectorPort. The HTTP client is injected so this is testable offline and
 * swappable at transfer (real client adds Entra auth + base URL + paging).
 * Endpoints are representative; verify exact paths against the live API at transfer.
 * If a domain URL isn't configured, the domain is passed as []/{}  so toFacts tolerates it.
 * @param {{ http: { getJson: (url:string) => Promise<any> }, config: object }} deps
 * @returns {{ collect: () => Promise<object> }}
 */
export function createRestCollector({ http, config }) {
  return {
    async collect() {
      // Capacity domain (paged list + refreshes)
      const [capacityRaw, refreshesRaw] = await Promise.all([
        config.capacityUrl ? http.getJson(config.capacityUrl) : Promise.resolve(null),
        config.refreshesUrl ? fetchAllPages(http, config.refreshesUrl) : Promise.resolve([]),
      ]);

      // Remaining domains — each is optional; missing URL → empty/default
      const [datasetsRaw, reportsRaw, pipelinesRaw, lineageRaw, accessRaw, usageRaw] =
        await Promise.all([
          config.datasetsUrl ? fetchAllPages(http, config.datasetsUrl) : Promise.resolve([]),
          config.reportsUrl  ? fetchAllPages(http, config.reportsUrl)  : Promise.resolve([]),
          config.pipelinesUrl ? fetchAllPages(http, config.pipelinesUrl) : Promise.resolve([]),
          config.lineageUrl  ? http.getJson(config.lineageUrl)  : Promise.resolve({}),
          config.accessUrl   ? http.getJson(config.accessUrl)   : Promise.resolve({}),
          config.usageUrl    ? http.getJson(config.usageUrl)    : Promise.resolve({}),
        ]);

      const raw = {
        capacity: capacityRaw?.value?.[0] ?? capacityRaw ?? {},
        refreshes: Array.isArray(refreshesRaw) ? refreshesRaw : (refreshesRaw?.value ?? []),
        datasets:  datasetsRaw,
        reports:   reportsRaw,
        pipelines: pipelinesRaw,
        lineage:   lineageRaw,
        access:    accessRaw,
        usage:     usageRaw,
      };

      return toFacts(raw);
    },
  };
}
