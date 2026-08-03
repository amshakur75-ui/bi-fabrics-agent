# 12 — Microsoft Teams Delivery & Two-Way Conversation (bi-fabrics-audit-agent)

Research focus: how a **READ-ONLY Fabric/PBI capacity audit agent** delivers alerts into Microsoft Teams **today (one-way)** and how it can hold a **two-way conversation later** (the 30% concentration alert names a user; the team can reply / ask back).

Scope note (already researched elsewhere, kept high-level here): a Databricks App cannot itself be the Bot Framework messaging endpoint; Copilot Studio + Fabric data-agent consumption is covered in another file. This document goes deep on **Teams delivery mechanics**: Incoming Webhooks + the O365-connector retirement and Power Automate "Workflows" replacement; Adaptive Cards; Azure Bot Service / Bot Framework for a true two-way bot; Microsoft Graph Teams messaging + RSC; and Logic App / Power Automate posting.

Each section ends with **"How it helps"** split into **One-way alerts now** vs **Two-way bot later**.

---

## EXECUTIVE DECISION SUMMARY (read this first)

| Capability | Best mechanism NOW (one-way) | Best mechanism LATER (two-way) | Why |
|---|---|---|---|
| Push an alert card to a channel | **Power Automate "Workflows" incoming webhook** ("When a Teams webhook request is received") — POST a `type:message` + Adaptive Card attachment | Same, OR a **registered Azure Bot** posting via Bot Connector | Webhook = zero infra, just an HTTPS POST. Old O365-connector incoming webhooks are retiring (see dates). |
| Name a specific user / get their attention | Adaptive Card `<at>` mention via webhook/Graph, OR **Graph `sendActivityNotification`** (activity-feed ping with `recipient`) | Bot proactive 1:1 message to that user | Activity-feed notification + mention = the cleanest "this names you" surface without a bot. |
| User replies / asks back ("why?", "ack", "snooze") | **Power Automate "Post adaptive card and wait for a response"** (flow pauses, captures the submit) — lightweight two-way without a hosted bot | **Azure Bot Service bot** handling `Action.Execute` / `Action.Submit` invokes + free-text turns | "Wait for a response" gives 1 round-trip per card with no always-on endpoint. A real conversational back-and-forth needs a bot. |
| Conversational Q&A over the audit | n/a | **Azure Bot** (messaging endpoint + proactive) **or** Copilot Studio + Fabric data agent (other doc) | Free-text turns require an always-listening messaging endpoint. |

**Recommended path for this agent:** (1) ship one-way alert cards via a Power Automate Workflows webhook immediately; (2) add `Action.Execute` buttons (Acknowledge / Snooze / Explain) with `Action.Submit` fallback so the card is forward-compatible; (3) when two-way is needed, stand up an Azure Bot resource whose messaging endpoint runs on a *real* web host (Azure App Service / Container App / Function — NOT a Databricks App), register the **Microsoft Teams** channel, ship a Teams app manifest containing the `bots` entry, and use **proactive messaging** (stored conversation references) to deliver the same alerts plus handle replies.

---

## 1. Microsoft 365 (Office 365) Connectors retirement & the Workflows replacement

### 1.1 Retirement of Office 365 connectors within Microsoft Teams (devblogs)
- **URL:** https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/
- **Summary:** O365/M365 Connectors (the classic "Incoming Webhook" connector that produced `*.webhook.office.com` URLs) are being retired in favour of the **Workflows app in Microsoft Teams** (powered by Power Automate). The timeline has been extended several times.
- **Key dates (as restated across updates):**
  - **Aug 15, 2024** — creation of *new* connectors blocked.
  - **Connector URL must be updated** (existing webhooks re-keyed) by the stated deadline (originally Dec 31 2024 → extended to Jan 31 2025).
  - **Latest published rollout:** deprecation rollout **begins ~May 18, 2026** and **completes ~May 22, 2026**, at which point connectors cease functioning. (Dates have moved before; treat as "imminent, verify before relying.")
- **Message Card / button caveat:** the **MessageCard** format can still be used when migrating, **but MessageCard payloads with button rendering are NOT supported** in the Workflows path. Interactive buttons require **Adaptive Cards**.
- **Other limitations:** Workflows post as the default **"Flow bot"** identity (no custom bot branding); private-channel posting via the Workflows app rolled out ~Apr 2026.
- **How it helps — One-way now:** confirms the classic `webhook.office.com` incoming webhook is a dead end; build on Workflows. **Two-way later:** none directly, but it pushes you toward Adaptive Cards (which carry the interactive actions a bot will later handle).

### 1.2 Create an Incoming Webhook (Teams platform docs)
- **URL:** https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook
- **Summary:** Canonical dev doc. Now opens with the deprecation banner and directs you to create webhooks via **Workflows** using the **"When a Teams webhook request is received"** trigger (template: **"Send webhook alerts to a channel"**). External app POSTs JSON to the generated webhook URL; the workflow posts the message/card to the channel.
- **Exact payload (Adaptive Card via webhook), C#/JS sample:**
  ```json
  {
    "type": "message",
    "attachments": [
      {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "contentUrl": null,
        "content": {
          "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
          "type": "AdaptiveCard",
          "version": "1.2",
          "body": [ { "type": "TextBlock", "text": "Message Text" } ]
        }
      }
    ]
  }
  ```
  POST with `Content-Type: application/json`. Plain text also works: `{ "text": "Hello from a webhook workflow!" }`.
- **Hard limits / identifiers:**
  - **Message size limit: 28 KB** (larger → error).
  - **Rate limit: >4 requests/second → throttled**; use exponential backoff; handle **HTTP 429**.
  - Incoming Webhook is **scoped at the channel level**.
  - **Workflows are owned by a user**, not a channel/team → can become **orphan flows** if the owner leaves (add co-owners). This is an operational risk for an unattended audit agent.
- **Adaptive Card action support via Incoming Webhook:** all native AC elements supported **except `Action.Submit`**. Supported actions: **`Action.OpenUrl`, `Action.ShowCard`, `Action.ToggleVisibility`, `Action.Execute`**.
- **How it helps — One-way now:** this is the primary delivery channel — emit `type:message` + AC attachment over HTTPS POST from the Python agent (`requests.post`). **Two-way later:** `Action.Execute` *is* supported here, so cards posted via webhook can carry buttons that a future bot handles; but the webhook itself cannot receive the response (no `Action.Submit`), so genuine round-trips need Workflows "wait for a response" or a bot.

### 1.3 Send messages in Teams using incoming webhooks (support.microsoft.com — end-user setup)
- **URL:** https://support.microsoft.com/en-us/office/send-messages-in-teams-using-incoming-webhooks-323660ec-12ca-40b1-a1d3-a3df47e808c4
- **Summary:** End-user steps to create the webhook from a channel. Templates: **"Send webhook alerts to a channel"**, **"Send webhook alerts from specific people to a channel"**, **"Send webhook alerts from people in an org to a channel"**. Both **Adaptive Card and Message Card** payloads are supported. Copy the generated callback URL and POST to it.
- **How it helps — One-way now:** the click-path an admin follows to provision the URL the agent will POST to. **Two-way later:** none.

### 1.4 Microsoft Teams connector reference (Power Automate connector)
- **URL:** https://learn.microsoft.com/en-us/connectors/teams/
- **Trigger — "When a Teams webhook request is received"** (`operationId: TeamsIncomingWebhookTrigger`):
  - POST-only (no GET). Accepts an array of Adaptive Cards in the body.
  - **Auth options on the trigger:** `triggerAuthenticationType` = *Anyone* / *Any user in my tenant* / *Specific users in my tenant* (`triggerAllowedUsers`). "Anyone" needs no token; tenant options require an OAuth bearer token on the POST.
  - **Callback URL form:** `https://<region>.logic.azure.com:443/...` (Logic-Apps-backed).
  - **Request body schema (Adaptive Cards)** — same `type:message` + `attachments[]` shape as 1.2; `contentType` must be `application/vnd.microsoft.card.adaptive`, `contentUrl` must be `null`, `content` is the AC JSON. MessageCard schema also accepted.
- **Action — "Post card in a chat or channel"** (`operationId: PostCardToConversation`): params `Post as` (`poster`: **Flow bot** | **User**), `Post in` (`location`), `Post card request` (`body`). **Flow bot is commercial-tenant only** — in GCC/GCCH/DoD it fails `BotNotInConversationRoster`; use **User** poster there.
- **Action — "Post message in a chat or channel"** (`operationId: PostMessageToConversation`): same poster/location pattern for plain messages.
- **Throttling (webhook trigger):** ~1,000 concurrent inbound calls; invoke calls 4,500–45,000 per 5 min depending on tier.
- **How it helps — One-way now:** authoritative schema + auth knobs for the webhook the agent POSTs to; lets you lock the trigger to tenant/specific callers (good for an internal audit agent). **Two-way later:** `PostCardToConversation` + a "wait for a response" variant (see §4) is the no-bot two-way option.

---

## 2. Adaptive Cards — schema, versioning, actions, Teams support matrix

### 2.1 Universal Action Model (`Action.Execute`) — Adaptive Cards docs
- **URL:** https://learn.microsoft.com/en-us/adaptive-cards/authoring-cards/universal-action-model
- **Summary:** Unifies the action model across Teams (Bots) and Outlook (Actionable Messages). **`Action.Execute` replaces both `Action.Submit` (Bots) and `Action.Http` (Actionable Messages)** and adds (a) auto-**refresh** of a card on display and (b) the ability to **return an updated card** from the bot in response to an action.
- **Schema versions (load-bearing):**
  - **Universal Bot action model / `Action.Execute` introduced in Adaptive Cards v1.4** — set card `"version": "1.4"` (or wrap in `ActionSet` with `fallback` for older clients).
  - The broader "Universal Actions" concept is associated with **v1.5**.
- **`Action.Execute` properties:** `type` (req), `verb` (string id you switch on server-side), `data` (hidden payload merged with inputs), `title`, `iconUrl`, `style`, **`fallback`** (`Action.Submit` or `"drop"`), `requires`.
- **Refresh:** `refresh.action` (an `Action.Execute`) + `refresh.userIds` (array of user **MRIs**, **max 60**). Without `userIds`, no auto-refresh (manual refresh button) — deliberate, to avoid mass concurrent invokes in big channels. `userIds` ignored in Outlook.
- **Invoke flow:** a user click (or refresh) sends an **`adaptiveCard/action` Invoke activity** to the bot:
  ```json
  { "type": "invoke", "name": "adaptiveCard/action",
    "value": { "action": { "type": "Action.Execute", "verb": "...", "data": {...} },
               "trigger": "automatic | manual" } }
  ```
  Bot responds HTTP 200 with body `{ "statusCode": 200-599, "type": "...", "value": ... }`. To return a replacement card: `type: application/vnd.microsoft.card.adaptive`, `value:` the new AC. To return a message: `type: application/vnd.microsoft.activity.message`. Auth prompts use `401` + `application/vnd.microsoft.activity.loginRequest` (OAuthCard).
- **Fallback rule (critical for compatibility):** define each `Action.Execute` with a `fallback` of `Action.Submit`, and **wrap in an `ActionSet`** (older clients mis-handle fallback outside an ActionSet). On AC-1.4-capable Teams the Execute renders; on older clients the Submit renders. Your bot must handle **both** `Action.Execute` and `Action.Submit`.
- **How it helps — One-way now:** even one-way cards should declare `Action.Execute` with `Action.Submit` fallback so they're forward-compatible the day a bot is added — no card re-authoring. **Two-way later:** this is the exact mechanism the bot uses to receive button clicks and reply with an updated card (e.g., "Acknowledged by Priya at 14:02"). Refresh + `userIds` lets the 30%-concentration card show a per-user view.

### 2.2 Teams card reference & support matrix (Teams platform)
- **URL:** https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-reference
- **Teams Adaptive Card version support:**
  - **Desktop: v1.5 or earlier** for bot-sent cards and action-based message extensions (also dialogs, link unfurling).
  - **Mobile (Teams mobile app): up to v1.2 only** — cards using >1.2 may render incorrectly. **For broadest reach target AC 1.2; for buttons that a bot will handle, use 1.4 Execute + 1.2-safe fallback.**
- **Card-type × feature matrix (relevant rows):**
  - Adaptive Card: Bots ✔, Message-ext results ✔, Dialogs ✔, **Incoming Webhooks ✔**, Connectors for M365 Groups ❌.
  - Connector card for M365 Groups: Incoming Webhooks ✔.
- **Teams Adaptive Card notes:**
  - "Positive"/"destructive" action **styling not supported** on Teams.
  - `isEnabled` on `Action.Submit` **not supported** in Teams.
  - No file/image upload inside AC; `speak` only for immersive reader.
  - Incoming Webhook AC actions: everything **except `Action.Submit`** (Execute/OpenUrl/ShowCard/ToggleVisibility supported).
- **Inputs available (from AC schema, used in Teams):** `Input.Text`, `Input.Number`, `Input.Date`, `Input.Time`, `Input.Toggle`, `Input.ChoiceSet` — usable for ack reasons, snooze duration, etc. (Note: inputs only return data via `Action.Submit`/`Action.Execute` to a bot or via Power Automate "wait for a response"; a plain webhook cannot collect them.)
- **Cards NOT supported in Teams:** Animation, Audio, Video cards.
- **How it helps — One-way now:** dictates the safe schema version (1.2 mobile / 1.5 desktop) and which actions render. **Two-way later:** confirms inputs + Execute are first-class for bot-handled cards.

### 2.3 Adaptive Cards schema explorer (cross-reference)
- **URL:** https://adaptivecards.io/explorer/Action.Submit.html  and  https://adaptivecards.io/explorer/
- **Summary:** Canonical per-element schema (Action.Submit/OpenUrl/Execute/ShowCard, all Input.* elements). Use for authoring exact JSON.
- **How it helps:** authoritative element-level schema for building the alert card body in Python.

---

## 3. Azure Bot Service / Bot Framework — a real two-way bot

### 3.1 Send proactive messages (Teams platform)
- **URL:** https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages
- **Summary:** A proactive message = any bot message not in reply to a user turn (exactly the audit-alert use case). There is **no active `turnContext`** — you must already have a **conversation reference** (or create the conversation).
- **Flow:** (1) get the **Microsoft Entra user ID / userId / teamId / channelId** + **tenantId**; (2) create the conversation if needed; (3) get **conversationId**; (4) send via **`continueConversation` / `ContinueConversationAsync`**.
- **Identifiers & rules:**
  - Store **`tenantId`** + (`userId` or `channelId`) or the whole **`conversationReference`** object. Persist in a DB, not memory.
  - 1:1 personal proactive message uses the user's **`aadObjectId`** (passed in the `Id` param) — **personal scope only**; the bot **must already be installed in personal scope** or you get **403 `ForbiddenOperationException`**.
  - `userId` is unique per (bot, user) — not reusable across bots. `channelId` is global.
  - **App must be installed** in the team/chat before a channel/group proactive message works.
  - Teams does **not** support proactive messaging by email/UPN.
  - **`serviceUrl`**: use the value from an incoming activity; if unavailable use the global endpoint **`https://smba.trafficmanager.net/teams/`** (public cloud; GCC/GCCH/DoD have their own `smba.infra.*` endpoints). Don't hardcode for replies.
  - Detect blocked/uninstalled: proactive send returns **403 with `subCode: MessageWritesBlocked`** per user (build an opt-out/health report).
  - `POST /v3/conversations` body needs `bot`, `members[]`, `channelData.tenant.id`; returns conversation `id`.
- **How it helps — One-way now:** if you choose the bot route even for one-way alerts, this is how the bot pushes a card to the named user / channel on a schedule. **Two-way later:** mandatory foundation — the bot must capture & persist conversation references from `onMembersAdded`/any activity to later both push alerts and continue replies.

### 3.2 Send proactive notifications (Azure Bot Service SDK)
- **URL:** https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-howto-proactive-message?view=azure-bot-service-4.0
- **Summary:** SDK-level mechanics. Bot app = web app with a **messages endpoint** + (for proactive) an extra **notify endpoint**. On the notify trigger, call the adapter's **continue conversation** with a stored `ConversationReference` and a callback; the callback's `turnContext.SendActivityAsync(...)` delivers the message.
- **Key facts:** conversation reference carries `conversation`, `user`, and **`serviceUrl`**; if `serviceUrl` changes, old references break (`continueConversation` errors) — re-acquire. `ContinueConversationAsync` needs the bot's **`MicrosoftAppId`**. Don't burst proactive messages (channels throttle/disable).
- **IMPORTANT migration note:** the doc now states the **Bot Framework SDK is archived (no longer maintained; support ended Dec 31 2025)**. Microsoft steers new bots to the **Microsoft 365 Agents SDK** (C#/JS/Python, `aka.ms/agents`) or, for Teams-native agents, the **Teams AI Library / Teams SDK**. **Design decision:** build the new two-way bot on the **M365 Agents SDK** (or Teams AI Library), not the legacy Bot Framework SDK — though the underlying Azure Bot resource + Bot Connector protocol/auth are unchanged.
- **How it helps — Two-way later:** the concrete server shape (messages + notify endpoints, adapter, stored references). Confirms which SDK to adopt going forward.

### 3.3 Authenticate requests with the Bot Connector API (the OAuth/JWT handshake)
- **URL:** https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication?view=azure-bot-service-4.0
- **Summary:** Service-to-service auth in both directions. The SDK does this automatically given App ID + password; needed only if hand-rolling.
- **Outbound (bot → Connector, i.e. sending messages):** OAuth2 client-credentials:
  ```
  POST https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token   (multi-tenant)
  grant_type=client_credentials
  client_id=<MicrosoftAppId>&client_secret=<MicrosoftAppPassword>
  &scope=https%3A%2F%2Fapi.botframework.com%2F.default
  ```
  (single-tenant: `.../<TENANT-ID>/oauth2/v2.0/token`; or user-assigned managed identity with `resource=https://api.botframework.com`). Put the returned token in `Authorization: Bearer ...` on calls to `https://smba.trafficmanager.net/teams/v3/conversations/...`.
- **Inbound (Connector → bot, i.e. verifying the request hitting your messaging endpoint):** verify the JWT in the `Authorization: Bearer` header:
  - OpenID metadata (static): `https://login.botframework.com/v1/.well-known/openidconfiguration` → keys at `.../v1/.well-known/keys` (refresh ≤24h).
  - Required claims: **`iss = https://api.botframework.com`**, **`aud = <your bot's Microsoft App ID>`**, within validity (5-min skew), valid RS256 signature, and **`serviceUrl` claim matches** the activity's `serviceUrl`. Reject with **403** on failure / missing channel endorsement.
- **How it helps — Two-way later:** exact endpoints/scopes/claims your hosted endpoint must satisfy. This is also *why* a Databricks App can't be the endpoint (it can't host a public, JWT-verifying messaging endpoint registered to the Azure Bot resource). One-way: same outbound token if pushing via Connector REST directly.

### 3.4 Bot Framework Connector REST reference
- **URL:** https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-api-reference?view=azure-bot-service-4.0
- **Summary:** REST surface: `POST /v3/conversations` (create), `POST /v3/conversations/{conversationId}/activities` (send), `PUT .../activities/{activityId}` (update), delete. Base = the `serviceUrl` (Teams: regionalized `smba.*`).
- **How it helps:** the raw HTTP you'd call from Python if not using an SDK (e.g., to update an alert card after acknowledgement).

### 3.5 Teams app manifest — `bots` entry + RSC declaration
- **URLs:** https://learn.microsoft.com/en-us/microsoftteams/platform/resources/schema/manifest-schema  •  app-permissions overview: https://learn.microsoft.com/en-us/microsoftteams/app-permissions
- **Summary:** To put a bot in Teams you ship a **Teams app package** (manifest.json + icons). Key manifest pieces:
  - **`bots[]`**: `botId` (= the bot's Microsoft App/Entra client ID), `scopes` (`personal`, `team`, `groupchat`), `supportsFiles`, command lists.
  - **`webApplicationInfo`**: `{ id: <Entra app id>, resource: <app uri> }` — required for SSO and for Graph proactive-install / activity-feed scenarios.
  - **`authorization.permissions.resourceSpecific[]`**: each `{ "name": "<RSC permission>", "type": "Application" | "Delegated" }` — this is where RSC is declared. **Use manifest v1.12+** for delegated RSC, v1.6+ for application RSC.
- **How it helps — Two-way later:** without the `bots` manifest entry installed in the team/chat/personal scope, the bot cannot send or receive in Teams. RSC declared here is what lets the app send channel/activity notifications without tenant-wide admin grants. One-way: same manifest needed if delivering via bot rather than webhook.

---

## 4. Power Automate / Logic App posting (the no-bot two-way option)

### 4.1 Overview of adaptive cards for Teams (Power Automate)
- **URL:** https://learn.microsoft.com/en-us/power-automate/overview-adaptive-cards
- **Action names (exact):**
  - **"Post your own adaptive card as the Flow bot to a user"** (fire-and-forget; only OpenURL buttons work).
  - **"Post an adaptive card as the Flow bot to a Teams user, and wait for a response"** — flow **pauses** until the named recipient submits required inputs; returns dynamic content for **1 response per recipient per card**.
  - **"Post your own adaptive card as the flow bot to a channel"** (fire-and-forget).
  - **"Post an adaptive card as the flow bot to a Teams channel, and wait for a response"** — pauses until **anyone** in the channel responds (1 response per responder per card).
- **Two-way mechanics:** the "wait for a response" actions are the lightweight round-trip: card inputs (`Input.*` + `Action.Submit`) come back as **dynamic content** in subsequent flow steps (including the submitting user's id; pair with Office 365 Users connector to resolve profile). 
- **Known issues (load-bearing):** non-"wait" cards error on any button except **OpenURL**; `Action.Submit` on a non-wait card errors; a wait card accepts **only one submit** then ignores further ones; configure **Update Message** / **Should update card** to replace the card after submit (prevents double-submit). DoD environment: adaptive cards not available.
- **How it helps — Two-way later (lightweight):** lets the audit agent get an **acknowledgement / reason / snooze** back from the named user **without hosting a bot** — the agent POSTs to a Workflows webhook that triggers a flow ending in "post adaptive card and wait for a response", and the flow records the reply (and could call back the agent). This is the recommended *first* two-way step before committing to a full bot. **One-way now:** the plain "post adaptive card" actions are an alternative to the raw webhook.

### 4.2 Create flows that post adaptive cards to Teams (Power Automate)
- **URL:** https://learn.microsoft.com/en-us/power-automate/create-adaptive-cards
- **Summary:** Step-by-step authoring of the post/post-and-wait actions; AC JSON goes in the **Adaptive card** field; how to read back response dynamic content.
- **How it helps:** implementation recipe for the §4.1 two-way pattern.

---

## 5. Microsoft Graph — Teams messaging & activity-feed notifications

### 5.1 Send chatMessage in a channel or chat (Graph v1.0)
- **URLs:** https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0  •  channel variant: https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0
- **Endpoints:**
  - Channel: `POST /teams/{team-id}/channels/{channel-id}/messages`  (reply: `.../messages/{message-id}/replies`)
  - Chat: `POST /chats/{chat-id}/messages`
- **Permissions:**
  - **Delegated (work/school):** least-priv **`ChannelMessage.Send`** (channel) / **`ChatMessage.Send`** (chat); higher `Group.ReadWrite.All` / `Chat.ReadWrite`.
  - **Application:** **only `Teamwork.Migrate.All`**, and it is **migration-only** ("Application permissions are only supported for migration").
- **CRITICAL CONSTRAINT:** **Graph cannot send normal app-context (daemon) Teams messages.** App permission = import/migration only. So a headless audit agent **cannot** use Graph `chatMessage POST` with a client-credentials token to post alerts — it would need a **delegated** (signed-in user) token, or it must use a **bot** / **webhook** instead.
- **Body:** `body.contentType` (`text`|`html`) + `content`; Adaptive Card via `<attachment id="..."></attachment>` placeholder + `attachments[]` (`contentType: application/vnd.microsoft.card.adaptive`); **`mentions[]`** with `<at id="0">Name</at>` to **name/@-mention a specific user** (this is how the 30%-concentration card names the user inline). Success = **201 Created**.
- **How it helps — One-way now:** viable only if a delegated/service-account identity is acceptable; the `mentions` block is the cleanest way to @-mention the offending user. **Two-way later:** reading replies uses `ChannelMessage.Read.*` / change notifications, but RSC/bot is the supported path for app-context send.

### 5.2 Send activity feed notifications (Teams platform + Graph)
- **URLs:** https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/send-activity-feed-notification  •  Graph how-to: https://learn.microsoft.com/en-us/graph/teams-send-activityfeednotifications
- **Endpoints:**
  - To a user: `POST /users/{userId}/teamwork/sendActivityNotification`
  - In a team: `POST /teams/{teamId}/sendActivityNotification`
  - In a chat: `POST /chats/{chatId}/sendActivityNotification`
  - Bulk (≤100 users): `POST /teamwork/sendActivityNotificationToRecipients`
- **Permissions:** delegated or application; recommended **application via RSC** — **`TeamsActivity.Send.User`** (user), **`TeamsActivity.Send.Group`** (team), **`TeamsActivity.Send.Chat`** (chat). These are **"basic" RSC permissions, always consentable on install, no admin grant needed**, and **always enabled at tenant level**. (Non-RSC app permission `TeamsActivity.Send` also exists.)
- **Body:** `topic` (e.g. an `entityUrl`/deep link), `activityType` (must match an `activityTypes` entry in manifest, OR the reserved **`systemDefault`** for free-form text), `previewText`, **`recipient`** (the named user), `templateParameters` (e.g. `{ "name": "actor", "value": "Capacity Auditor" }`). The special `{actor}` param = the caller (app name in app-only calls).
- **Manifest requirements:** `webApplicationInfo` (Entra app id), optional `activities.activityTypes[]` with `templateText` like `"{actor} flagged that {user} drives 30% of capacity"`, and the RSC `authorization.permissions.resourceSpecific[]` block. Manifest **v1.7+**. App must be installed for the recipient.
- **How it helps — One-way now:** **the strongest fit for "the alert names a user"** — sends a banner+feed ping *to that specific person* with a deep link back, using app-only RSC (no admin consent, no bot). Templated text personalizes ("…Priya drives 30%…"). **Two-way later:** the deep link can open a tab/bot where the conversation continues; pairs well with a bot for the reply.

### 5.3 Resource-Specific Consent (RSC) for apps
- **URL:** https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/resource-specific-consent  •  grant: https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/grant-resource-specific-consent
- **Summary:** RSC grants scoped access to a *specific* team/chat/user instance (where the app is installed), declared in the manifest, consented by the resource owner at install — **no tenant-wide admin consent** for most.
- **Modes:** **Application** (no signed-in user; only resource owners grant) vs **Delegated** (on behalf of signed-in user). Application RSC works with **Microsoft Graph + Bot Framework SDK**, manifest **v1.6+**; delegated RSC via Teams JS client, manifest **v1.12+**.
- **Send-relevant RSC permissions:**
  - **`ChannelMessage.Send.Group`** — "Send messages to this team's channels" (**Application**).
  - **`TeamsActivity.Send.Group` / `.Chat` / `.User`** — activity-feed notifications (**Application**, basic/always-on).
  - (Chat message send RSC: `ChatMessage.Send.Chat` is referenced for chat sends.)
- **Manifest shape:**
  ```json
  "authorization": { "permissions": { "resourceSpecific": [
    { "name": "ChannelMessage.Send.Group", "type": "Application" },
    { "name": "TeamsActivity.Send.User",   "type": "Application" }
  ] } }
  ```
- **How it helps:** **RSC is the key that unlocks app-context (headless) Teams delivery without admin-consented tenant-wide Graph permissions** — exactly what an automated audit agent wants. `ChannelMessage.Send.Group` (RSC, app) is the supported way to post a channel message in app context (vs. the migration-only `Teamwork.Migrate.All`); `TeamsActivity.Send.*` powers the user-naming pings. Two-way later: same app/manifest carries the bot.

### 5.4 Proactive bot installation & messaging via Graph
- **URL:** https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/proactive-bots-and-messages/graph-proactive-bots-and-messages
- **Summary:** To message a user who hasn't installed your app, **install it for them via Graph**, then capture the `conversationUpdate` to get the conversation/chatId, then proactively message.
- **Permissions (application):** **`TeamsAppInstallation.ReadWriteSelfForUser.All`** (install self for any user), **`TeamsAppInstallation.ReadWriteSelfForTeam.All`** (self into any team) — require **admin grant once**, then apply tenant-wide. Needs `webApplicationInfo` in manifest.
- **Flow:** get `teamsAppId` (`GET /appCatalogs/teamsApps?$filter=externalId eq '{manifestId}'`); check install (`GET /users/{id}/teamwork/installedApps?$expand=teamsApp&$filter=teamsApp/id eq '{teamsAppId}'`); install (`POST /users/{id}/teamwork/installedApps` with `teamsApp@odata.bind`); get chat (`GET /users/{id}/teamwork/installedApps/{installId}/chat`).
- **How it helps — Two-way later:** lets the audit bot reach a named user **before** they've ever opened the app — important for "the alert names a user who must respond." One-way: same, to seed the personal chat for pushes.

### 5.5 Install app for a user (Graph v1.0 reference)
- **URL:** https://learn.microsoft.com/en-us/graph/api/userteamwork-post-installedapps?view=graph-rest-1.0
- **Endpoint:** `POST /users/{user-id | upn}/teamwork/installedApps` with body `{ "teamsApp@odata.bind": "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/{teamsAppId}" }` → **201 Created**. Optional `consentedPermissionSet.resourceSpecificPermissions[]` (e.g. `{ permissionValue: "TeamsActivity.Send.User", permissionType: "Application" }`) consents RSC at install.
- **Permissions:** delegated least-priv `TeamsAppInstallation.ReadWriteSelfForUser` (higher `...ReadWriteForUser`); application `TeamsAppInstallation.ReadWriteSelfForUser.All` (higher `...ReadWriteForUser.All`). To install *any* app (not just self) use `...ReadWriteForUser(.All)`.
- **How it helps:** exact call + body to provision the bot/app per user so proactive/activity-feed delivery works.

### 5.6 Get channel/chat messages for bots & agents (RSC read path)
- **URL:** https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-messages-for-bots-and-agents
- **Summary:** For a bot to *receive* all messages in a channel/chat (not just @mentions) — needed for free-text two-way — declare RSC **`ChannelMessage.Read.Group`** / **`ChatMessage.Read.Chat`** in the manifest. Without it a Teams bot only gets messages where it's @mentioned (channel) by default.
- **How it helps — Two-way later:** governs whether the bot hears "why is my capacity flagged?" typed without an @mention. For an alert-ack bot, @mention or Adaptive Card buttons may suffice (no broad read RSC needed).

---

## 6. Synthesis — concrete build guidance for bi-fabrics-audit-agent

**Stage 0 (now, one-way, zero infra):** Admin creates a Power Automate **Workflows** incoming webhook ("Send webhook alerts to a channel", lock trigger to tenant). Python agent POSTs `type:message` + Adaptive Card (`version 1.2` body for mobile safety) to the callback URL. Respect 28 KB / 4 req-s limits with backoff on 429. Mitigate orphan-flow risk by assigning co-owners.

**Stage 0b (name the user, still no bot):** Ship a minimal Teams **app manifest** (no bot yet) with `webApplicationInfo` + RSC `TeamsActivity.Send.User`/`.Group`/`.Chat`; call Graph `sendActivityNotification` with `recipient` = the flagged user and templated text → personal banner/feed ping with a deep link. App-only via RSC, no tenant admin consent.

**Stage 1 (lightweight two-way):** Move the alert into a flow that ends in **"Post an adaptive card … and wait for a response"** with `Input.ChoiceSet` (Acknowledge / Snooze / Explain) → flow captures the reply (and submitter id) and can call back the agent. No hosted endpoint.

**Stage 2 (full conversational bot):** Create an **Azure Bot** resource, host the **messages + notify** endpoints on Azure App Service / Container Apps / Functions (NOT a Databricks App), register the **Microsoft Teams** channel, add a `bots` entry to the manifest, build on the **M365 Agents SDK / Teams AI Library** (Bot Framework SDK is archived). Persist **conversation references**; use **proactive messaging** for scheduled alerts; author cards with **`Action.Execute` + `Action.Submit` fallback (wrapped in ActionSet)** and handle the `adaptiveCard/action` invoke to ack/snooze/explain and **return an updated card**. Verify inbound Connector JWTs (`iss=api.botframework.com`, `aud=<AppId>`, serviceUrl match); get outbound tokens from `login.microsoftonline.com/botframework.com/oauth2/v2.0/token` scope `api.botframework.com/.default`.

**Key gotchas:** Graph app-context `chatMessage POST` is **migration-only** — don't rely on it for alerts (use webhook / RSC `ChannelMessage.Send.Group` / bot). Mobile Teams caps AC at **1.2**. Workflows "Flow bot" is **commercial-tenant only** (GCC/DoD must post as user). Proactive 1:1 needs the app pre-installed in **personal scope** (else 403). MessageCard buttons don't render in Workflows — use Adaptive Cards.

---

## FLAT URL LIST (all sources)

1. https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/
2. https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook
3. https://support.microsoft.com/en-us/office/send-messages-in-teams-using-incoming-webhooks-323660ec-12ca-40b1-a1d3-a3df47e808c4
4. https://learn.microsoft.com/en-us/connectors/teams/
5. https://learn.microsoft.com/en-us/microsoftteams/m365-custom-connectors
6. https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/what-are-webhooks-and-connectors
7. https://learn.microsoft.com/en-us/adaptive-cards/authoring-cards/universal-action-model
8. https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-reference
9. https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/overview
10. https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/work-with-universal-actions-for-adaptive-cards
11. https://adaptivecards.io/explorer/Action.Submit.html
12. https://adaptivecards.io/explorer/
13. https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages
14. https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-howto-proactive-message?view=azure-bot-service-4.0
15. https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication?view=azure-bot-service-4.0
16. https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-api-reference?view=azure-bot-service-4.0
17. https://learn.microsoft.com/en-us/microsoftteams/platform/resources/schema/manifest-schema
18. https://learn.microsoft.com/en-us/microsoftteams/app-permissions
19. https://learn.microsoft.com/en-us/power-automate/overview-adaptive-cards
20. https://learn.microsoft.com/en-us/power-automate/create-adaptive-cards
21. https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0
22. https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0
23. https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/send-activity-feed-notification
24. https://learn.microsoft.com/en-us/graph/teams-send-activityfeednotifications
25. https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/resource-specific-consent
26. https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/grant-resource-specific-consent
27. https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/proactive-bots-and-messages/graph-proactive-bots-and-messages
28. https://learn.microsoft.com/en-us/graph/api/userteamwork-post-installedapps?view=graph-rest-1.0
29. https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-messages-for-bots-and-agents
30. https://learn.microsoft.com/en-us/graph/permissions-reference
31. https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/rate-limit
32. https://learn.microsoft.com/en-us/microsoftteams/platform/bots/build-notification-capability
