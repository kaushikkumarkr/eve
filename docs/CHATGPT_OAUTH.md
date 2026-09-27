# ChatGPT MCP OAuth setup (Auth0 Free + Entra sign-in)

The former Eve staging deployment used static shared bearer tokens for Codex CLI, not OAuth, and was retired on 2026-09-27. This file describes a future ChatGPT custom-MCP connection. ChatGPT's OAuth setup is stricter than a basic Entra-protected API integration and needs an authorization server that publishes the MCP OAuth metadata, supports PKCE, and handles the MCP resource indicator/client-registration flow.

For a small internal team, the lowest-effort free option is Auth0 Free as the OAuth authorization server, federated to the agency's existing Microsoft Entra tenant. Eve's application, PostgreSQL, and client-key vault remain in Azure. Auth0 handles identity login and issues the MCP access token. Auth0 currently lists Auth for MCP on Free, up to 25,000 monthly active users, and one enterprise connection; verify current plan terms before relying on those limits.

This is an optional, not-yet-deployed mode. Do not expose a new live app until the Auth0 tenant is configured and the acceptance checks below pass. The former staging bearer-token connection was Codex-only; the production target remains individual identity-based auth behind a private authenticated gateway.

## Auth0 configuration

1. Create an Auth0 Free B2B tenant. Add one Microsoft Entra enterprise connection for the agency directory. Use the callback URL Auth0 shows when registering its upstream application in Entra. Restrict the connection to the agency tenant and approved team members; enforce MFA in Entra. Do not enable an Auth0 username/password connection for this internal service.
2. In Auth0, create an API whose identifier is exactly the Eve MCP resource URL, including `/mcp`, for example `https://<new-private-gateway-host>/mcp`. Enable RS256 signing and the `operator` permission/scope. Enable RBAC and include granted API permissions in access tokens if that option is available.
3. Enable Auth0's **Resource Parameter Compatibility Profile** so the OAuth `resource` value becomes the API audience. The identifier must exactly match Eve's public MCP URL.
4. Configure MCP client registration in Auth0. Prefer Auth0's supported Client ID Metadata Documents (CIMD) flow where the ChatGPT connector presents it. If using Dynamic Client Registration (DCR), understand that registration is reachable by clients on the Internet; constrain sign-in to the Entra enterprise connection, monitor registrations, and use Auth0's available registration controls. Never treat registration as authorization: Eve still requires an approved role claim on the signed-in user.
5. Add a post-login Action that emits the namespaced role claim only from administrator-managed `app_metadata`. Example:

   ```js
   exports.onExecutePostLogin = async (event, api) => {
     const role = event.user.app_metadata?.eve_role;
     if (role === "Eve.Admin" || role === "Eve.Operator") {
       api.accessToken.setCustomClaim("https://eve.internal/roles", [role]);
     }
   };
   ```

   Assign `eve_role` to each approved person as `Eve.Admin` or `Eve.Operator` using Auth0's administrative interface. Do not derive admin rights from a user-editable profile field or email domain alone. Users without an approved role are denied by Eve.

## Eve configuration

Configure these non-secret application settings on the control app:

```text
AGENCY_MCP_AUTH_MODE=auth0
AGENCY_MCP_PUBLIC_URL=<the exact canonical Eve MCP URL>
AUTH0_DOMAIN=<Auth0 tenant host, e.g. eve-team.us.auth0.com>
AUTH0_AUDIENCE=<exact same value as AGENCY_MCP_PUBLIC_URL>
AUTH0_ROLE_CLAIM=https://eve.internal/roles
```

No ChatGPT client secret or Ads API key is needed for the MCP connection when Auth0 registration is handled by CIMD/DCR. The upstream Entra enterprise-connection credential belongs only in Auth0's protected connection settings. Keep Eve's client Ads/CAPI credentials in the existing Azure Key Vaults.

Auth0 changes each operator subject identifier. Re-create or migrate `ClientAccessGrant` rows using each Auth0 `sub` identity before operators access client workspaces. Only admins may approve/apply changes, and the Ads account remains in mock/paused mode until explicitly authorized by the client.

## Acceptance checks before switching the live deployment

- Verify Auth0 OIDC discovery reports the configured issuer, authorization/token endpoints, `S256` PKCE, and accepted token endpoint auth methods.
- Verify Auth0 resource-parameter compatibility produces an RS256 access token whose audience equals the Eve MCP URL and whose scope includes `operator`.
- Verify the protected-resource metadata advertises the Auth0 issuer and `operator` scope; the ChatGPT callback flow completes without a client secret.
- Test valid operator, valid admin, missing-role, wrong-audience, wrong-issuer, missing-scope, and expired-token cases. Every invalid case must be denied.
- Test an unassigned operator cannot access a client workspace, and an operator cannot approve or apply changes.
- Confirm ChatGPT custom MCP access is enabled for the account/workspace plan. OpenAI's current availability page lists full MCP for Business and Enterprise/Edu; Pro is limited to read/fetch, and Plus is not listed for full MCP.
- Only after all checks pass: deploy a new revision with Auth0 mode and verify it. Do not enable Ads API mode or campaign mutations as part of OAuth migration.

## Azure-only alternative

Microsoft publishes an experimental Azure APIM + Functions sample that brokers OAuth/PKCE and DCR to Entra. It demonstrates a fully Azure-hosted approach, but its documented sample uses APIM Basic v2 (fixed monthly cost) and state/cache logic. APIM Consumption has no fixed cost and includes a monthly free request allowance, but does not provide APIM's built-in cache; adapting the sample requires a durable state store and additional integration/testing. It is not a zero-effort or guaranteed-$0 deployment. Prefer that route only if keeping the identity broker entirely in Azure is a requirement and the additional engineering/operating cost is acceptable.

References: [OpenAI MCP OAuth requirements](https://developers.openai.com/plugins/build/auth), [Auth0 pricing](https://auth0.com/pricing), [Auth0 Auth for MCP](https://auth0.com/blog/auth0-auth-for-mcp-servers-generally-available/), [Auth0 resource parameter compatibility](https://support.auth0.com/center/s/article/mcp-audience-error-with-auth0), [Microsoft Azure MCP authorization sample](https://github.com/Azure-Samples/remote-mcp-apim-functions-python), [Azure APIM cost model](https://learn.microsoft.com/en-us/azure/api-management/plan-manage-costs).
