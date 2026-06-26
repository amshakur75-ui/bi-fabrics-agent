# 08 — Azure Entra Identity Config, RBAC & Key Vault (deep dive)

Research for **bi-fabrics-audit-agent**: a READ-ONLY Fabric / Power BI capacity audit agent that authenticates as an **Entra service principal (app registration)** and runs inside **Azure Databricks** (job / notebook).

Scope of THIS doc (deep): app registration end-to-end; credential types (secret / certificate / **federated credentials / WIF**); whether a Databricks workload can call Fabric/PBI **secretlessly**; security groups for SP scoping; admin consent / enterprise apps; conditional access on SPs; Azure RBAC for `Microsoft.Fabric/capacities`; Azure Key Vault (RBAC vs access-policy models, built-in roles + IDs, references).

> Out of scope here (covered elsewhere): OAuth **scope strings**, the Power BI/Fabric **tenant settings**, the "managed identity can't call Power BI" fact, and MSAL client-credentials basics. These are referenced only where they intersect identity config.

**Bottom-line for the agent's auth (TL;DR):**
- Register a **single-tenant app registration**; the audit SP is its **enterprise application (service principal)** in the home tenant.
- Prefer **secretless**: add a **federated identity credential ("Other issuer")** on the app that trusts the **Databricks OIDC issuer** so the Databricks workload token exchanges for an Entra access token — *no stored secret*. This is technically supported because Entra WIF accepts arbitrary OIDC issuers.
- If federation isn't wired, fall back to a **certificate** (not a secret) stored in **Key Vault**, read at runtime via **Key Vault Secrets User** (data-plane RBAC). Avoid client secrets.
- Scope the SP via a dedicated **security group** for the PBI/Fabric tenant settings, plus **Azure RBAC `Reader` at the right scope** for `Microsoft.Fabric/capacities` ARM reads.
- Optionally harden with **Conditional Access for workload identities** (block sign-in outside the Databricks egress IP range; risk-based block). Requires Workload Identities Premium.

---

## 1. Entra app registration — end-to-end

**TITLE:** How to Register an App in Microsoft Entra ID — Microsoft identity platform
**URL:** https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app
**Summary:** App registration creates a *unidirectional* trust ("your app trusts the Microsoft identity platform, not the other way around"). The application object lives in the home tenant and **can't be moved between tenants** once created.
**Steps / exact field names:**
1. Sign in to **Microsoft Entra admin center** (https://entra.microsoft.com) as at least **Application Developer**.
2. **Entra ID > App registrations > New registration**.
3. Enter a **Name** (e.g. `identity-client-app`) — visible to users, changeable, non-unique.
4. **Supported account types** drop-down (recommend **Single tenant only - <your tenant>** for this agent):
   - *Single tenant only* — users/guests in your tenant only.
   - *Multiple Entra ID tenants* — multitenant (SaaS).
   - *Any Entra ID Tenant + Personal Microsoft accounts*.
   - *Personal accounts only*.
5. **Register**.
6. On the **Overview** page, record **Application (client) ID** — "uniquely identifies your application and is used in your application's code as part of validating the security tokens."
**Other identifiers (Overview > Essentials):** **Directory (tenant) ID**, **Object ID** (note: the app registration Object ID ≠ the enterprise-app/service-principal Object ID — see §6, §7).
**Note on visibility:** new app registrations are hidden on My Apps by default; toggle **Visible to users? = Yes** under *Enterprise apps > app > Properties*.
**Credentials:** added under **Certificates & secrets** (client secret, certificate, **Federated credentials** tab) — see §2.
**Expose an API / Application ID URI:** configured under **Expose an API** (Application ID URI, scopes). *Not needed for this agent* — the audit agent is a confidential client calling Power BI/Fabric/ARM as a resource consumer, it does not publish an API. (Mentioned for completeness; the Application ID URI is also usable as the `--id` in `az ad app federated-credential` commands.)
**How it helps:** establishes the SP identity; the **client ID + tenant ID** are the only two values the Databricks job needs in code (no secret if WIF is used — see §2/§3).

---

## 2. Credential types: client secret vs certificate vs federated credential (WIF)

**TITLE:** Security best practices for application registration / app properties — Microsoft identity platform
**URL:** https://learn.microsoft.com/en-us/entra/identity-platform/security-best-practices-for-app-registration
**Summary / Microsoft's explicit ranking (most → least preferred):**
1. **Managed identity** (where the resource supports it) — Azure manages credentials. *Caveat for this agent: managed identities cannot call Power BI / Fabric REST (covered elsewhere), so MI alone is insufficient for the PBI side.*
2. **Federated identity credentials (WIF)** — **secretless**; external IdP token exchanged for an Entra token. No stored credential to leak/rotate. **This is the recommended path for the agent.**
3. **Certificate credentials** ("x509 certificates issued by Trusted Certificate Authority as the only credential type for getting tokens").
4. **Client secret (password credential)** — "Don't use password credentials, also known as secrets."

**TITLE:** Entra ID app registration — client secret vs certificate (analysis)
**URL:** https://melmanm.github.io/misc/2023/12/02/article12-azure-ad-client-secret-vs-certificate.html
**Summary / exact facts:**
- **Client secret** is transmitted on the wire in the token request → interceptable & replayable until expiry; secret can be valid **up to 2 years (24 months)**; Microsoft advises **< 1 year** expiration.
- **Certificate / client assertion**: the cert is *never* sent over the wire; the signed JWT assertion is **short-lived** (expiry in the assertion payload), so interception risk is bounded.
- Recommendation: **certificate over secret**; if a cert must be used, **store it in Azure Key Vault**.
**How it helps:** drives the agent's credential decision — prefer WIF (no credential), else certificate-in-Key-Vault, never a bare secret in Databricks config.

**Where credentials are added:** App registration > **Certificates & secrets**:
- **Client secrets** tab — value shown once; copy immediately.
- **Certificates** tab — upload public key (.cer/.pem/.crt); private key stays with caller (or in Key Vault).
- **Federated credentials** tab — **Add credential** → choose a *Federated credential scenario* (see §3).

---

## 3. Workload Identity Federation (WIF) — secretless, and the Databricks question

**TITLE:** Workload Identity Federation — Microsoft Entra Workload ID
**URL:** https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation
**Summary:** Configure an **app registration** (or user-assigned managed identity) to **trust tokens from an external IdP**. The external workload exchanges its IdP token for a Microsoft identity-platform access token via the **client-credentials flow with a federated credential** (the IdP's JWT is passed in place of a self-signed assertion). "You eliminate the maintenance burden of manually managing credentials and eliminate the risk of leaking secrets or having certificates expire."
**Supported scenarios (verbatim list):** Kubernetes (AKS/EKS/GKE/on-prem) · GitHub Actions · Azure compute using app identities (trust an app→user-assigned MI) · Google Cloud · AWS (via Amazon Cognito) · **Other workloads running on compute platforms outside Azure** (configure trust to "the external IdP for your compute platform" and use the client-credentials flow with a federated credential) · SPIFFE/SPIRE · Azure Pipelines service connection.
**Token-exchange flow (6 steps):** workload gets IdP token → sends it to Microsoft identity platform requesting an access token → Entra validates the external token against the **OIDC issuer URL** and the FIC's issuer/subject/audience → Entra returns an access token → workload calls the protected resource.
**Hard limits / rules:**
- **Max 20 federated identity credentials** per application or user-assigned MI.
- **Entra-issued tokens may NOT be used** for federated flows ("The federated identity credentials flow does not support tokens issued by Microsoft Entra ID").
- Entra stores **only the first 100 signing keys** from the IdP's OIDC endpoint.
- FIC `issuer`, `subject`, `audience` must **case-sensitively match** the incoming token's claims.

**TITLE:** Create a trust relationship between an app and an external identity provider
**URL:** https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust
**Summary / exact FIC fields:**
- **issuer** — URL of the external IdP; must match the token's `iss` claim; OIDC-Discovery-compliant URL Entra uses to fetch validation keys. ≤600 chars; leading/trailing whitespace blocks exchange.
- **subject** — must match the token's `sub` claim; no fixed format (GUID / colon-delimited / arbitrary). ≤600 chars. *If wrong, the FIC still creates successfully — failure only appears at token-exchange time, silently.*
- **audiences** — exactly **one** value; recommended **`api://AzureADTokenExchange`**; ≤600 chars; matched against the token `aud`.
- **name** — immutable, 3–120 chars, URL-friendly (alphanumeric/dash/underscore, first char alphanumeric).
- **description** — optional, ≤600 chars.
- **Wildcards not supported** in any FIC property.
**Permissions to create a FIC:** be the app **Owner**, or hold **Application Administrator / Cloud Application Administrator / Global Administrator / Hybrid Identity Administrator** (permission `microsoft.directory/applications/credentials/update`).
**Portal scenarios (dropdown):** *GitHub actions deploying Azure resources* · *Kubernetes accessing Azure resources* · **Other issuer** (generic OIDC — enter any **Issuer** URL + **Subject identifier**) · *Flexible federated identity credential (preview)* — uses a **claim-matching expression** in a **Value** field instead of an exact subject.
**Microsoft Graph API:** `POST https://graph.microsoft.com/applications/{appObjectId}/federatedIdentityCredentials` with `{name, issuer, subject, audiences:["api://AzureADTokenExchange"]}` (also `az ad app federated-credential create` / `New-AzADAppFederatedCredential`). The app is addressed by **object ID** (or app/client ID or identifier URI for CLI).
**Generic OIDC example (Google):** `issuer: https://accounts.google.com`, `subject:` the service-account unique ID.

**TITLE:** Workload identity federation — considerations & restrictions
**URL:** https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations
**Summary / exact constraints:**
- Only **RS256-signed** issuer tokens are supported for exchange.
- **Propagation delay**: a token request "several minutes after configuring the FIC" can fail with `AADSTS70021: No matching federated identity record found for presented assertion.` — **add retry logic**.
- FICs **don't consume** the tenant SP object quota; **max 20** per app.
- `issuer`+`subject` combination must be **unique** on the app (409/400 on duplicate).
- User-assigned-MI FICs must be created **sequentially** (concurrent → 409 conflict); FIC creation unsupported in region **Malaysia South**.
- Can be governed by **Azure Policy** (deny on type `Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials`).

### The Databricks secretless verdict
**TITLE:** Authenticate with Azure managed identities — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/azure-mi
**TITLE:** Use Azure managed identities in Unity Catalog to access storage
**URL:** https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/azure-managed-identities
**Summary:** Databricks can use **Azure managed identities** (on the **Access Connector for Azure Databricks** — system- or user-assigned) to obtain **Entra ID tokens** for Azure resources *without managing credentials*; Databricks "treats managed identities as service principals." This obtains Microsoft Entra ID tokens for any resource that supports Entra auth.
**How it helps / nuance for the agent:**
- A Databricks managed identity gets Entra tokens for **Azure ARM resources** (so it can read `Microsoft.Fabric/capacities` via ARM directly — §5). But MI **cannot** be granted Power BI/Fabric REST app permissions (covered elsewhere). So MI alone can't run the PBI/Fabric-REST side of the audit.
- **The secretless pattern that DOES work for PBI/Fabric:** register an **app registration** (the audit SP, which *can* be added to the PBI security group), and add a **federated identity credential ("Other issuer")** trusting the **OIDC issuer of the Databricks-side identity** (the workload/identity token presented by the Databricks job). The Databricks job presents its OIDC token; Entra exchanges it for an access token for the audit **app registration** (which holds the PBI/Fabric API permissions). Result: the agent authenticates as the privileged app registration **with no stored secret**.
- Databricks also natively supports **OAuth token federation / OIDC** for its own service principals (issuer/subject "service principal federation policy"), confirming Databricks emits exchangeable OIDC tokens: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/oauth-federation — useful as the IdP side of the trust.
- **Practical caution:** the exact `issuer`/`subject` of the Databricks workload token must be discoverable and stable to populate the FIC; validate with a manual token-exchange before relying on it, and keep a **certificate-in-Key-Vault** fallback (§2, §8).

---

## 4. Security groups for SP scoping; admin consent; enterprise applications

**TITLE:** Enable service principal authentication for admin APIs — Microsoft Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/admin/enable-service-principal-admin-apis
**Summary:** To let the SP use the (read-only) admin APIs, the **SP must be a member of an allowed security group** referenced in the tenant setting ("**Specific security groups**" radio + add the group). Membership grants **read-only access to all admin-API info** (user names/emails, dataset/report metadata, current & future). Create the group in **Entra ID > Groups**, then **Add Members** (the SP).
**How it helps:** the agent's blast-radius / user-attribution collectors run through admin APIs gated by this group; one dedicated group = clean, auditable scoping that also lets you reuse the same group across other tenant settings.
*(The tenant settings themselves are covered elsewhere; this entry is only the group-membership/scoping mechanism.)*

**TITLE:** Grant tenant-wide admin consent to an application — Microsoft Entra ID
**URL:** https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent
**Summary / exact facts:** An **enterprise application (service principal)** is the tenant-side representation of an app. **Application permissions** (a.k.a. **app roles**) require **admin consent** — the SP calls APIs *without a signed-in user*, so app permissions can't be user-consented. Granting tenant-wide admin consent gives the app the requested permissions **on behalf of the whole org** and is a **sensitive operation**.
**Scoping note:** admin consent is **tenant-wide, not scoped to users/groups**; restrict *who can use the app* via the **Enterprise Application > assignment settings** ("Assignment required = Yes" + assigned users/groups). For a daemon SP the relevant scoping is the **security group** (above) + **least-privilege app permissions**.
**How it helps:** the agent's Power BI/Fabric REST app permissions (read-only) need a one-time **Grant admin consent for <tenant>** under the app's **API permissions** blade; status should read **Granted for <tenant>**.

Related references:
- Overview of user and admin consent: https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/user-admin-consent-overview
- Review permissions granted to enterprise applications: https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-application-permissions
- Apps & service principals in Entra ID (app object vs SP object): https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principals

---

## 5. Azure RBAC for `Microsoft.Fabric/capacities`

**TITLE:** Azure permissions for Analytics — Azure RBAC (Microsoft.Fabric operations)
**URL:** https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/analytics
**Parent index:** https://learn.microsoft.com/en-us/azure/role-based-access-control/resource-provider-operations
**Summary:** Complete `Microsoft.Fabric` control-plane (ARM) operation strings:

| Operation | Description |
| --- | --- |
| `Microsoft.Fabric/register/action` | Registers Fabric resource provider. |
| `Microsoft.Fabric/capacities/read` | Retrieves the information of the specified Fabric Capacity. |
| `Microsoft.Fabric/capacities/write` | Creates or updates the specified Fabric Capacity. |
| `Microsoft.Fabric/capacities/delete` | Deletes the Fabric Capacity. |
| `Microsoft.Fabric/capacities/suspend/action` | Suspend the specified Fabric capacity. |
| `Microsoft.Fabric/capacities/resume/action` | Resume the specified Fabric capacity. |
| `Microsoft.Fabric/capacities/skus/read` | Retrieve available SKU information for the Fabric capacity. |
| `Microsoft.Fabric/locations/checkNameAvailability/action` | Checks that a given Fabric resource name is valid and not in use. |
| `Microsoft.Fabric/locations/operationresults/read` | Retrieves the specified operation result. |
| `Microsoft.Fabric/locations/operationstatuses/read` | Retrieves the specified operation status. |
| `Microsoft.Fabric/operations/read` | Retrieves the information of operations. |
| `Microsoft.Fabric/privateLinkServicesForFabric/read|write|delete` | CRUD on Fabric Private Link Service. |
| `Microsoft.Fabric/privateLinkServicesForFabric/operationResults/read` · `/operationStatuses/read` | Private Link op result/status. |
| `Microsoft.Fabric/privateLinkServicesForFabric/privateEndpointConnections/read|write|delete` | Private endpoint connections. |
| `Microsoft.Fabric/privateLinkServicesForFabric/privateLinkResources/read` | Private link resources. |
| `Microsoft.Fabric/skus/read` | Retrieves the information of SKUs. |

**Built-in role → Fabric capability mapping (general Azure RBAC semantics):**
- **Reader** (`acdd72a7-3385-48ef-bd42-f606fba81ae7`) — all `*/read`, so `Microsoft.Fabric/capacities/read`, `.../skus/read`, `operations/read`. **This is the role the audit agent needs** to enumerate capacities, SKUs (F-SKU size), and status via ARM. No write/suspend/resume/delete.
- **Contributor** (`b24988ac-6189-42b9-9915-6d7c4f6cdc5b`) — full management *except* assigning RBAC roles → can `write/delete/suspend/resume` capacities. **Over-privileged for a read-only auditor — do NOT assign.**
- **Owner** (`8e3af657-a8ff-443c-a75c-2fe8c4bcb635`) — Contributor + `Microsoft.Authorization/*` (manage role assignments). **Avoid.**
- **Custom role** — for tightest least-privilege, define a role whose `Actions` = `["Microsoft.Fabric/capacities/read","Microsoft.Fabric/capacities/skus/read","Microsoft.Fabric/operations/read"]`, `NotActions: []`, with `AssignableScopes` set to the subscription/RG. (Pattern from Key Vault custom-role example in §8.)
**Assignment scope:** roles can be assigned at **management group / subscription / resource group / individual capacity resource**. Recommend assigning **Reader at the subscription** (or the RG containing the capacities) so the agent sees all capacities in audit scope; narrow to RG/resource for tighter blast radius.
**RP registration:** reading capacities requires the **Microsoft.Fabric** resource provider registered in the subscription (`Microsoft.Fabric/register/action`; needs Contributor/Owner once at setup — a one-time admin step, not the agent's runtime role).
**How it helps:** gives the agent a *direct, read-only ARM path* to capacity inventory/SKU/state that is independent of the Power BI/Fabric data-plane APIs (defense in depth + cross-check of capacity state).

General RBAC references:
- Azure built-in roles: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles
- Azure custom roles: https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles

---

## 6. Conditional Access for workload identities (SP hardening)

**TITLE:** Microsoft Entra Conditional Access for workload identities
**URL:** https://learn.microsoft.com/en-us/entra/identity/conditional-access/workload-identity
**Summary / exact rules:**
- CA can be scoped to **service principals owned by the org** ("Conditional Access for workload identities").
- **Single-tenant SPs registered in your tenant only.** Microsoft/third-party multitenant apps and **managed identities are NOT covered** (use access reviews for MIs).
- **Group membership is NOT honored** — "Conditional Access policies assigned to a group that contains a service principal are not enforced … it must be assigned directly to the policy as a workload identity."
- **Workloads can't do MFA**; the **only grant control is `Block access`.**
- Supported **conditions**: **Locations** (block outside known public IP ranges / named locations) and **Service principal risk** (via Entra ID Protection), optionally with authentication contexts.
- **License:** **Workload Identities Premium** required to create/modify these policies (existing policies keep working without the license but can't be edited).
- **objectID gotcha:** scope by the **enterprise application (service principal) Object ID** from *Entra ID > Enterprise apps > Overview*, **NOT** the App registration Object ID.
- Graph (beta) supports `clientApplications.includeServicePrincipals` (`ServicePrincipalsInMyTenant` or specific Object IDs) + `locations` + `grantControls.builtInControls:["block"]`.
**How it helps:** lock the audit SP so it can authenticate **only from the Databricks egress IP range** (named location) and **block on elevated SP risk** — strong containment if the SP credential (or even the WIF trust) is ever abused from elsewhere. Save in **Report-only** first; review under *Sign-in logs > Service principal sign-ins > Conditional Access*.

Related:
- Securing workload identities with ID Protection: https://learn.microsoft.com/en-us/entra/id-protection/concept-workload-identity-risk

---

## 7. App object vs Service Principal (identifier hygiene — affects §3 and §6)

**TITLE:** Apps & service principals in Microsoft Entra ID
**URL:** https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principals
**Summary:** One **application object** (in the home tenant, under *App registrations*) + a globally unique **app/client ID**; the **service principal** (under *Enterprise applications*) is the local instance that gets role assignments, consent, and CA policies.
**Identifier cheat-sheet for the agent:**
- **App registration Object ID** → used for **FIC create** (`az ad app federated-credential`, Graph `applications/{id}/federatedIdentityCredentials`).
- **Service principal (enterprise app) Object ID** → used for **Conditional Access** scoping and **Azure RBAC** assignment of the SP.
- **Application (client) ID** + **Directory (tenant) ID** → used in the Databricks job's auth config.
**How it helps:** prevents the classic mistake of pasting the wrong Object ID into a CA policy or role assignment (silent no-op).

---

## 8. Azure Key Vault — permission models, roles, references

**TITLE:** Azure RBAC vs. access policies (Key Vault)
**URL:** https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-access-policy
**Summary / exact facts:**
- **Use the RBAC permission model**, not access policies. **As of API version 2026-02-01, Azure RBAC is the DEFAULT for new key vaults.**
- Access-policy risk: anyone with `Contributor`, `Key Vault Contributor`, or any role with **`Microsoft.KeyVault/vaults/write`** can **grant themselves data-plane access** by editing an access policy → privilege escalation. RBAC instead restricts *granting* to **Owner / User Access Administrator** only.
- RBAC advantages: unified model; centralized; **PIM integration** (time-limited privileged access); **Deny assignments**; scope to **individual keys/secrets/certificates**.
- Legacy access policies lack PIM and have known weaknesses.
**Two planes:** control plane (`management.azure.com`, manage the vault) vs **data plane** (`<vault>.vault.azure.net`, read/write secrets). They're independent; reading a secret needs **data-plane** rights.

**TITLE:** Grant permission to applications to access Key Vault using Azure RBAC (built-in roles + IDs)
**URL:** https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide
**Built-in data-plane roles (name → what it grants → role ID):**
| Role | Grants | Role definition ID |
| --- | --- | --- |
| **Key Vault Secrets User** | **Read secret contents** (incl. secret portion of a cert w/ private key). **← the agent's role to read its cert/secret.** | `4633458b-17de-408a-b874-0445c86b69e6` |
| Key Vault Secrets Officer | Any action on secrets except manage permissions. | `b86a8fe4-44ce-4948-aee5-eccb2c155cd7` |
| Key Vault Certificates Officer | Any action on certificates except manage permissions. | `a4417e6f-fecd-4de8-b567-7b0420556985` |
| Key Vault Certificate User | Read entire certificate contents (incl. secret + key portion). | `db79e9a7-68ee-4b58-9aeb-b90e7c24fcba` |
| Key Vault Crypto User | Perform crypto ops using keys. | `12338af0-0e69-4776-bea7-57ae8d297424` |
| Key Vault Crypto Officer | Any action on keys except manage permissions. | `14b46e9e-c2b7-41b4-b07b-48a6ebf60603` |
| Key Vault Reader | Read **metadata** of vault + objects; **cannot** read secret values/key material. | `21090545-7ca7-4776-b22c-e363652d74d2` |
| Key Vault Administrator | All data-plane ops on all objects; cannot manage vault resource or role assignments. | `00482a5a-887f-4fb3-b363-3b7fe8e74483` |
| Key Vault Data Access Administrator | Add/remove the above KV role assignments (with ABAC constraint). | `8b54135c-b56d-4d72-a534-26097cfdc8d8` |
**Important:** `Key Vault Contributor` is **control-plane only** — it does **NOT** grant access to keys/secrets/certs. To read a secret the agent SP needs **Key Vault Secrets User** (or **Certificate User** if a cert is stored as a KV certificate object).
**Scopes:** management group / subscription / RG / **individual key/secret/certificate**. Recommend **vault-per-app-per-environment** with roles at **vault scope**; object-level assignment only for the listed edge cases (and gives **no isolation** for admin ops, which need vault-level perms).
**Enable RBAC on a vault:** set **`enableRbacAuthorization`** (portal toggle "Azure role-based access control"). Switching models **invalidates all access policies** — assign equivalent roles first to avoid outages. Changing the model needs unrestricted `Microsoft.Authorization/roleAssignments/write` (Owner / User Access Administrator).
**Custom role example (DataActions):** roles use `DataActions` such as `Microsoft.KeyVault/vaults/secrets/getSecret/action` / `Microsoft.KeyVault/vaults/keys/read` etc.; `NotDataActions` to subtract.
**Propagation:** "Allow several minutes for role assignments to refresh"; browser caching may require refresh.
**Assigning the SP (CLI/PS):** `az role assignment create --role "Key Vault Secrets User" --assignee <appId> --scope /subscriptions/<sub>/resourcegroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault>` · `New-AzRoleAssignment -RoleDefinitionName "Key Vault Secrets User" -ApplicationId <appId> -Scope <vault-scope>`.
**How it helps:** if the agent uses a certificate/secret fallback, store it in a vault with **RBAC model** and grant the audit SP **Key Vault Secrets User** at vault scope — least-privilege, auditable, PIM-eligible.

**TITLE:** Use Key Vault references as app settings — Azure App Service
**URL:** https://learn.microsoft.com/en-us/azure/app-service/app-service-key-vault-references
**Summary / exact syntax:**
- `@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/<name>)` (optionally `/<version>`).
- Or `@Microsoft.KeyVault(VaultName=<vault>;SecretName=<name>)`.
- Resolved by the host using the app's **managed identity** (system-assigned by default; user-assigned selectable), which needs **Key Vault Secrets User**. No code changes.
**How it helps / caveat:** Key Vault **references** are an App Service/Functions host feature — **Databricks does not resolve `@Microsoft.KeyVault(...)` automatically.** In Databricks the equivalents are: **Databricks-backed secret scope** (`dbutils.secrets`), an **Azure-Key-Vault-backed secret scope**, or read the vault at runtime via the **azure-keyvault-secrets** SDK + the Access Connector managed identity. The reference syntax is documented here for completeness and in case any agent component runs on App Service/Functions.

Supporting KV references:
- Secure your Azure Key Vault: https://learn.microsoft.com/en-us/azure/key-vault/general/security-features
- Migrate to Azure RBAC (Key Vault): https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-migration

---

## 9. How each item strengthens / simplifies the agent's auth

| Item | Effect on the agent |
| --- | --- |
| Single-tenant **app registration** | Minimal trust surface; one client ID + tenant ID in code. |
| **WIF / federated credential ("Other issuer")** trusting Databricks OIDC | **Secretless** — nothing to store/rotate/leak in Databricks; strongest posture. |
| **Certificate** (vs secret), stored in **Key Vault** | If WIF not available: short-lived assertions, cert never on the wire, KV-guarded. |
| **Key Vault RBAC** + **Key Vault Secrets User** at vault scope | Least-privilege secret/cert read; PIM-eligible; no accidental data-plane escalation. |
| **Security group** for the SP | Clean, auditable scoping of PBI/Fabric admin-API access; reusable across settings. |
| **Admin consent** (one-time) on read-only app permissions | Daemon SP can call PBI/Fabric/Graph without a signed-in user; least-privilege roles. |
| **Azure RBAC `Reader`** on `Microsoft.Fabric/capacities` (sub/RG) | Direct read-only ARM inventory/SKU/state path, independent of data-plane APIs. |
| **Conditional Access for workload identities** (Block outside Databricks IPs / on risk) | Containment if credential/trust abused; report-only rollout; needs WI Premium. |
| **Identifier hygiene** (app-object ID vs SP-object ID) | Avoids silent no-op role/CA misconfig. |

---

## Flat URL list

1. https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app
2. https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principals
3. https://learn.microsoft.com/en-us/entra/identity-platform/security-best-practices-for-app-registration
4. https://melmanm.github.io/misc/2023/12/02/article12-azure-ad-client-secret-vs-certificate.html
5. https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation
6. https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust
7. https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations
8. https://learn.microsoft.com/en-us/graph/api/resources/federatedidentitycredentials-overview?view=graph-rest-1.0
9. https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/azure-mi
10. https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/azure-managed-identities
11. https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/oauth-federation
12. https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/azure-sp
13. https://learn.microsoft.com/en-us/fabric/admin/enable-service-principal-admin-apis
14. https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent
15. https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/user-admin-consent-overview
16. https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-application-permissions
17. https://learn.microsoft.com/en-us/azure/role-based-access-control/resource-provider-operations
18. https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/analytics
19. https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles
20. https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles
21. https://learn.microsoft.com/en-us/entra/identity/conditional-access/workload-identity
22. https://learn.microsoft.com/en-us/entra/id-protection/concept-workload-identity-risk
23. https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-access-policy
24. https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide
25. https://learn.microsoft.com/en-us/azure/key-vault/general/security-features
26. https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-migration
27. https://learn.microsoft.com/en-us/azure/app-service/app-service-key-vault-references
</content>
</invoke>
