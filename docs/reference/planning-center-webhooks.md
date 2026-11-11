# Planning Center Webhooks API reference

Webhooks allow a third-party application to receive real-time data payloads when something changes in Planning
Center. In most cases, webhooks are a faster, more efficient alternative to polling the API for data changes.

The API endpoints documented here can be used to interact with our webhook system programatically,
allowing you to obtain information on webhook events, see the status of event deliveries, and create new webhook
subscriptions, among other things.

For general information on how to use Planning Center webhooks, please visit
https://developer.planning.center/docs/#/overview/webhooks.

Generated on 2026-11-11 from Planning Center's published API documentation (`https://api.planningcenteronline.com/webhooks/v2/documentation`), version `2022-10-20`. 5 resources. Shared rules (authentication, rate limits, versions, paging) are in [planning-center-api.md](planning-center-api.md).

- **Base URL:** `https://api.planningcenteronline.com/webhooks/v2`
- **Starting point:** [Organization](#organization) at `/webhooks/v2`
- **Version header:** `X-PCO-API-Version: 2022-10-20`

## How deliveries work

Summarised from Planning Center's webhooks overview (`/docs/overview/webhooks`). The resources further down are the API for managing subscriptions; this section is what a receiver has to implement.

- **Who can subscribe:** any user who can log in, at `https://api.planningcenteronline.com/webhooks` or through this API. A subscription belongs to that user and only carries data that user may see.
- **Request:** always `POST`. Answer with `200`. Any other status counts as a failure. Answering `410` switches the subscription off.
- **Envelope:** the body is `{"data": [ ... ]}`. Each entry has `type` `EventDelivery`, a unique `id`, and `attributes` with `name` (the event), `attempt` (starting at 1) and `payload`. **`payload` is a JSON string**, so it is parsed a second time; inside is the changed record in the usual `{"data": {...}}` shape.
- **Event names** look like `people.v2.events.person.updated`: app, API version, `events`, resource, action.
- **Rate limits:** none on deliveries at present.

### Checking a delivery is genuine

The `X-PCO-Webhooks-Authenticity` header is an HMAC-SHA256 hex digest of the raw request body, keyed with the subscription's `authenticity_secret`. Compare it before parsing anything, using the bytes exactly as received.

```python
import hashlib, hmac

def genuine(raw_body: bytes, header_value: str, authenticity_secret: str) -> bool:
    expected = hmac.new(authenticity_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value or "")
```

### Failures and retries

- A failed delivery is retried up to 16 times: after 30 seconds, then 1, 2, 4, 8, 16, 32 and 64 minutes, then about 2.1, 4.2, 8.5 and 17 hours, then once a day for up to 4 days.
- After 16 failures in a row (about 5.4 days) the subscription is switched off. A very high number of failures to one endpoint within 24 hours also switches it off.
- The owner is emailed after an hour of failures and again on the last try, but only for subscriptions they created themselves, not ones an OAuth application created for them.
- A `429` or `503` answer with a `Retry-After` header (seconds or an HTTP date) is honoured, capped at 1 hour, and never shorter than the normal back-off. It still counts as a failed attempt.
- Deliveries can be inspected and re-sent by hand on the webhooks page.

### Which events exist

The list is not in the public documentation. It comes from `GET /webhooks/v2/available_events` with a signed-in token ([AvailableEvent](#available_event) below), and the same list is shown when adding a subscription on the webhooks page. It was not captured here: the request needs a working token.

## Versions

| Version | Change |
|---|---|
| `2022-10-20` | Replace Subscription with WebhookSubscription |
| `2018-08-01` | The first available version of the API |

## Resources

| Resource | Path | What it is |
|---|---|---|
| [AvailableEvent](#available_event) | `/webhooks/v2/available_events` | An event supported by webhooks |
| [Delivery](#delivery) | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{event_id}/deliveries` |  |
| [Event](#event) | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events` |  |
| [Organization](#organization) | `/webhooks/v2` |  |
| [WebhookSubscription](#webhook_subscription) | `/webhooks/v2/webhook_subscriptions` |  |

<a id="available_event"></a>
## AvailableEvent

An event supported by webhooks

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/webhooks/v2/available_events` | List |

Collection only: there is no single-record endpoint.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `action` | string | `"string"` |  |
| `app` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `resource` | string | `"string"` |  |
| `type` | string | `"string"` |  |
| `version` | string | `"string"` |  |

**Reached from**

- [Organization](#organization) as `available_events`: `/webhooks/v2/available_events`

**Example record**

```json
{
  "type": "AvailableEvent",
  "id": "1",
  "attributes": {
    "action": "string",
    "app": "string",
    "name": "string",
    "resource": "string",
    "type": "string",
    "version": "string"
  },
  "relationships": {}
}
```

<a id="delivery"></a>
## Delivery

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{event_id}/deliveries` | List |
| GET | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{event_id}/deliveries/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `object_url` | string | `"string"` |  |
| `request_body` | string | `"string"` |  |
| `request_headers` | string | `"string"` |  |
| `response_body` | string | `"string"` |  |
| `response_headers` | string | `"string"` |  |
| `status` | integer | `1` |  |
| `timing` | float | `1.42` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `event` | to_one | [Event](#event) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Event](#event) as `deliveries`: `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{event_id}/deliveries`

**Example record**

```json
{
  "type": "Delivery",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "object_url": "string",
    "request_body": "string",
    "request_headers": "string",
    "response_body": "string",
    "response_headers": "string",
    "status": 1,
    "timing": 1.42,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "event": {
      "data": {
        "type": "Event",
        "id": "1"
      }
    }
  }
}
```

<a id="event"></a>
## Event

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events` | List |
| GET | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{id}` | Fetch one |
| POST | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{event_id}/ignore` | Action: ignore |
| POST | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{event_id}/redeliver` | Action: redeliver |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `payload` | string | `"string"` | A string encoded JSON object. E.G. `"{\"data\":{...}}"` |
| `status` | string | `"value"` | Possible values: `pending`, `delivered`, `failed`, `skipped`, `duplicated`, `ignored_failed`, `ignored_skipped`, or `ignored_duplicated` |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `uuid` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `subscription` | to_one | Subscription |  |
| `person` | to_one | Person |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`
- Query with `where[...]`:
  - `where[status]` (string), for example `?where[status]=value`
  - `where[uuid]` (string), for example `?where[uuid]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `deliveries` | [Delivery](#delivery) | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events/{event_id}/deliveries` |  |

**Reached from**

- [WebhookSubscription](#webhook_subscription) as `events`: `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events`

**Example record**

```json
{
  "type": "Event",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "payload": "string",
    "status": "value",
    "updated_at": "2000-01-01T12:00:00Z",
    "uuid": "string"
  },
  "relationships": {
    "subscription": {
      "data": {
        "type": "Subscription",
        "id": "1"
      }
    },
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="organization"></a>
## Organization

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/webhooks/v2` | List |
| GET | `/webhooks/v2/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `available_events` | [AvailableEvent](#available_event) | `/webhooks/v2/available_events` |  |
| `webhook_subscriptions` | [WebhookSubscription](#webhook_subscription) | `/webhooks/v2/webhook_subscriptions` |  |

**Example record**

```json
{
  "type": "Organization",
  "id": "1",
  "attributes": {},
  "relationships": {}
}
```

<a id="webhook_subscription"></a>
## WebhookSubscription

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/webhooks/v2/webhook_subscriptions` | List |
| GET | `/webhooks/v2/webhook_subscriptions/{id}` | Fetch one |
| POST | `/webhooks/v2/webhook_subscriptions` | Create |
| PATCH | `/webhooks/v2/webhook_subscriptions/{id}` | Update |
| DELETE | `/webhooks/v2/webhook_subscriptions/{id}` | Delete |
| POST | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/rotate_secret` | Action: rotate_secret |

Attributes that can be set on create: `name`, `url`, `active`

Attributes that can be set on update: `active`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `active` | boolean | `true` |  |
| `application_id` | string | `"string"` |  |
| `authenticity_secret` | string | `"string"` | Every delivery will include a header `X-PCO-Webhooks-Authenticity`. This header will be the `HMAC-SHA256` value of this the `authenticity_secret` used as the key, and the message as the webhook body. `hmac_sha256(authenticity_secret, webhook_body)` |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `url` | string | `"string"` |  |

**URL parameters**

- Query with `where[...]`:
  - `where[application_id]` (string), for example `?where[application_id]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `events` | [Event](#event) | `/webhooks/v2/webhook_subscriptions/{webhook_subscription_id}/events` |  |

**Reached from**

- [Organization](#organization) as `webhook_subscriptions`: `/webhooks/v2/webhook_subscriptions`

**Example record**

```json
{
  "type": "WebhookSubscription",
  "id": "1",
  "attributes": {
    "active": true,
    "application_id": "string",
    "authenticity_secret": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "name": "string",
    "updated_at": "2000-01-01T12:00:00Z",
    "url": "string"
  },
  "relationships": {}
}
```
