# Planning Center API: shared rules

How every Planning Center API behaves, summarised from the overview pages at <https://api.planningcenteronline.com/docs/overview/getting-started>. Checked on 2026-11-11.

Per-app references, generated from Planning Center's published documentation:

| App | Reference | Base URL |
|---|---|---|
| Webhooks | [planning-center-webhooks.md](planning-center-webhooks.md) | `https://api.planningcenteronline.com/webhooks/v2` |
| Services | [planning-center-services.md](planning-center-services.md) | `https://api.planningcenteronline.com/services/v2` |
| Check-Ins | [planning-center-check-ins.md](planning-center-check-ins.md) | `https://api.planningcenteronline.com/check-ins/v2` |
| People | [planning-center-people.md](planning-center-people.md) | `https://api.planningcenteronline.com/people/v2` |

Other apps (Calendar, Giving, Groups, Publishing, Registrations) follow the same rules and are not written up here.

## Authentication

Every request acts as one user and sees only what that user may see.

- **Personal Access Token:** for one church, on a machine you control. Send the application ID and secret as HTTP Basic credentials on every request. Not for use across several churches, and never from a web page.
- **OAuth 2.0:** for an integration used by several churches. Send the user to `/oauth/authorize` with `client_id`, `redirect_uri`, `response_type=code` and `scope`; exchange the returned code at `POST /oauth/token`; then send `Authorization: Bearer <access token>`. Access tokens last 2 hours and come with a refresh token.
- **Scopes:** each product is its own OAuth scope (`people`, `services`, `check_ins` and so on).
- Tokens found posted in public are disabled automatically.

## Rate limits

- The default is 100 requests per 20 seconds per user. Requests with an `offset` above 30,000 get 75 per 20 seconds. Single endpoints can differ, and the limits can change without notice, so read the headers.
- Every response carries `X-PCO-API-Request-Rate-Limit`, `X-PCO-API-Request-Rate-Period` (text such as `20 seconds`, not a bare number) and `X-PCO-API-Request-Rate-Count`.
- Over the limit the answer is `429` with `Retry-After` in seconds.

## Versions

- Each app is versioned by date. Send `X-PCO-API-Version: YYYY-MM-DD`, or set a default per app in the developer account. The header wins.
- A date between two versions uses the latest version on or before it.
- `LATEST` always means the newest version and can break without warning.
- A date before `2018-08-01` returns `400` with `unsupported_version_error`.

## Request and response shape

The APIs follow JSON:API 1.0.

- Every body is wrapped in `data`: a list for a collection, an object for one record. Each record has `type`, `id`, `attributes` and often `relationships` and `links`.
- Create and update send the same wrapper: `{"data": {"type": "...", "attributes": {...}}}`. Create is `POST` to the collection, update is `PATCH` to the record, delete is `DELETE`.
- `include=a,b` adds related records to the response under `included`.
- `where[attribute]=value` filters by equality. A URL-encoded `%` (`%25`) at the start or end matches part of a string. `where[attribute][gt|gte|lt|lte]=value` compares dates and times. `where[association][attribute]=value` filters on an included record.
- `filter=name` switches on a named filter where a resource offers one.
- `order=attribute`, or `-attribute` to reverse.
- `per_page` (usually 1 to 100, default 25) and `offset` page through a collection.

Each reference lists, per resource, exactly which of these it accepts.

## Dates and times

- ISO 8601: dates as `YYYY-MM-DD`, times as `YYYY-MM-DDTHH:MM:SSZ`.
- Responses are always in UTC.
- In a query, a date with no time is read in the organisation's own time zone.

## Errors

| Status | Meaning |
|---|---|
| 400 | Something in the request is wrong. |
| 401 | Missing or wrong token or secret. |
| 403 | The user may not see or change that record. |
| 404 | No such URL or record. |
| 409 | A duplicate create, usually two simultaneous requests. |
| 422 | An attribute value is invalid; the body names it. |
| 429 | Rate limit exceeded. |
| 500 | Fault on Planning Center's side. |
| 503 | Planning Center is down for maintenance. |

Error bodies are `{"errors": [{"code": "...", "detail": "..."}]}`.

## How to read the per-app references

- **Endpoints** are worked out from the documentation's own flags: list and fetch from the resource path, create, update and delete only where the documentation says the resource allows them. The user's own permissions still apply. Where a create or update is flagged but no settable attributes are listed, treat it as unconfirmed. A resource that exists once per parent (Services' Live, for example) is still shown with list and fetch rows; use the path under its parent.
- **Related resources from here** are the links leading out of a record. **Reached from** lists every other path that returns the same resource.
- **Visible to** on an attribute means it is only returned to users at that permission level.
