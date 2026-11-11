# Planning Center Check-Ins API reference

Check kids & volunteers in to events and track attendance.

Generated on 2026-11-11 from Planning Center's published API documentation (`https://api.planningcenteronline.com/check-ins/v2/documentation`), version `2025-05-28`. 24 resources. Shared rules (authentication, rate limits, versions, paging) are in [planning-center-api.md](planning-center-api.md).

- **Base URL:** `https://api.planningcenteronline.com/check-ins/v2`
- **Starting point:** [Organization](#organization) at `/check-ins/v2`
- **Version header:** `X-PCO-API-Version: 2025-05-28`

## Versions

| Version | Change |
|---|---|
| `2025-05-28` | Querying by registration event id now queries for new Registrations integrations |
| `2024-11-07` | Made Organization time zone more consistent |
| `2024-09-03` | Added automatic importing of people |
| `2023-04-05` | Improve peformance of station endpoint |
| `2019-07-17` | Added support for multiple locations for one check-in |
| `2018-08-01` | The first available version of the API |

## Resources

| Resource | Path | What it is |
|---|---|---|
| [AttendanceType](#attendance_type) | `/check-ins/v2/events/{event_id}/attendance_types` | A kind of attendee which is tracked by _headcount_, not by check-in. |
| [CheckIn](#check_in) | `/check-ins/v2/check_ins` | An attendance record for an event. If someone was checked out, `checked_out_at` will be present. You can scope check-ins in a few ways: - `regular`s, `guest`s, and `volunteer`s correspond to the option selected when checking in. - `attendee`s are `regular`s and `guest`s together. - `one_time_guest`s are check-ins which were created without a corresponding person record. - `not_one_time_guest`s are check-ins which had a corresponding person record when they were created. - `checked_out` are check-ins where `checked_out_at` is present (meaning they were checked out from a station). - `first_time`s are check-ins which are the person's first for a given event. (Label-only visitors are not included here.) |
| [CheckInGroup](#check_in_group) | `/check-ins/v2/stations/{station_id}/check_in_groups` | When one or more people check in, they're grouped in a `CheckInGroup`. These check-ins all have the same "checked-in by" person. |
| [CheckInTime](#check_in_time) | `/check-ins/v2/check_ins/{check_in_id}/check_in_times` | A CheckInTime combines an EventTime and a Location, and associates it with the parent CheckIn. |
| [Event](#event) | `/check-ins/v2/events` | A recurring event which people may attend. Each recurrence is an _event period_ (which often corresponds to a week). Event periods have _event times_ where people may actually check in. |
| [EventLabel](#event_label) | `/check-ins/v2/events/{event_id}/event_labels` | Says how many of a given label to print for this event and whether to print it for regulars, guests, and/or volunteers. |
| [EventPeriod](#event_period) | `/check-ins/v2/check_ins/{check_in_id}/event_period` | A recurrence of an event, sometimes called a "session". For weekly events, an event period is a week. For daily events, an event period is a day. An event period has event times, which is what people select when they actually check in. When new sessions are created, times are copied from one session to the next. |
| [EventTime](#event_time) | `/check-ins/v2/event_times` | A time that someone may check in. Times are copied from session to session. |
| [Headcount](#headcount) | `/check-ins/v2/headcounts` | A tally of attendees for a given event time and attendance type. If one does not exist, the count may have been zero. |
| [IntegrationLink](#integration_link) | `/check-ins/v2/integration_links` | A record linking another product's resource to a Check-Ins resource. |
| [Label](#label) | `/check-ins/v2/labels` | Labels can be set to print for events (through `EventLabel`s), locations (through `LocationLabel`s) or options. Label type (security label / name label) is expressed with the `prints_for` attribute. `prints_for="Person"` is a name label, `prints_for="Group"` is a security label. |
| [Location](#location) | `/check-ins/v2/check_ins/{check_in_id}/locations` | A place where people may check in to for a given event. Some locations have `kind="Folder"`, which means that people can't check-in here, but this location contains other locations. You can get its contents from the `locations` attribute. You can get a location's parent folder from the `parent` attribute. (If it's not in a folder, `parent` will be empty.) |
| [LocationEventPeriod](#location_event_period) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods` | Counts check-ins for a location during a certain event period. |
| [LocationEventTime](#location_event_time) | `/check-ins/v2/event_times/{event_time_id}/location_event_times` | Counts check-ins for a location for a given event time. This is useful for checking occupancy. |
| [LocationLabel](#location_label) | `/check-ins/v2/labels/{label_id}/location_labels` | Says how many of a given label to print for this location and whether to print it for regulars, guests, and/or volunteers. |
| [Option](#option) | `/check-ins/v2/options` | An option which an attendee may select when checking in. Options may have extra labels associated with them, denoted by `label` and `quantity`. |
| [Organization](#organization) | `/check-ins/v2` | An organization which has people and events. This contains its date format & time zone preferences. |
| [Pass](#pass) | `/check-ins/v2/passes` | Enables quick lookup of a person via barcode reader. |
| [Person](#person) | `/check-ins/v2/people` | An attendee, volunteer or administrator. _Usually_, a person who checked in will be present as a `Person`. In some cases, they may not be present: - The person was manually deleted from the admin interface - The check-in was created as a "Visitor - Label Only" (which doesn't create a corresponding person record) |
| [PersonEvent](#person_event) | `/check-ins/v2/events/{event_id}/person_events` | Counts a person's attendence for a given event. |
| [PreCheck](#pre_check) | `/check-ins/v2` |  |
| [RosterListPerson](#roster_list_person) | `/check-ins/v2` |  |
| [Station](#station) | `/check-ins/v2/stations` | A device where people can be checked in. A device may also be connected to a printer and print labels for itself or other stations. |
| [Theme](#theme) | `/check-ins/v2/themes` | A custom style which may be applied to stations. |

<a id="attendance_type"></a>
## AttendanceType

A kind of attendee which is tracked by _headcount_, not by check-in.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/events/{event_id}/attendance_types` | List |
| GET | `/check-ins/v2/events/{event_id}/attendance_types/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `color` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `limit` | integer | `1` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `event` | to_one | [Event](#event) |  |

**URL parameters**

- `include`: `event`
- Query with `where[...]`:
  - `where[id]` (primary_key), for example `?where[id]=primary_key`
  - `where[name]` (string), for example `?where[name]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `event` | [Event](#event) | `/check-ins/v2/events/{event_id}/attendance_types/{attendance_type_id}/event` |  |
| `headcounts` | [Headcount](#headcount) | `/check-ins/v2/events/{event_id}/attendance_types/{attendance_type_id}/headcounts` |  |

**Reached from**

- [Event](#event) as `attendance_types`: `/check-ins/v2/events/{event_id}/attendance_types`
- [Headcount](#headcount) as `attendance_type`: `/check-ins/v2/headcounts/{headcount_id}/attendance_type`

**Example record**

```json
{
  "type": "AttendanceType",
  "id": "1",
  "attributes": {
    "color": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "limit": 1,
    "name": "string",
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

<a id="check_in"></a>
## CheckIn

An attendance record for an event.

If someone was checked out, `checked_out_at` will be present.

You can scope check-ins in a few ways:

- `regular`s, `guest`s, and `volunteer`s correspond to the option selected when checking in.
- `attendee`s are `regular`s and `guest`s together.
- `one_time_guest`s are check-ins which were created without a corresponding person record.
- `not_one_time_guest`s are check-ins which had a corresponding person record when they were created.
- `checked_out` are check-ins where `checked_out_at` is present (meaning they were checked out from a station).
- `first_time`s are check-ins which are the person's first for a given event. (Label-only visitors are not included here.)

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/check_ins` | List |
| GET | `/check-ins/v2/check_ins/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `checked_out_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `confirmed_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `emergency_contact_name` | string | `"string"` | Visible to: `non_self_station`. |
| `emergency_contact_phone_number` | string | `"string"` | Visible to: `non_self_station`. |
| `first_name` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `kind` | string | `"string"` |  |
| `last_name` | string | `"string"` |  |
| `medical_notes` | string | `"string"` | Visible to: `non_self_station`. |
| `number` | integer | `1` |  |
| `one_time_guest` | boolean | `true` |  |
| `security_code` | string | `"string"` | Visible to: `non_self_station`. |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `event_period` | to_one | [EventPeriod](#event_period) |  |
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `check_in_times`, `checked_in_at`, `checked_in_by`, `checked_out_by`, `event`, `event_period`, `event_times`, `locations`, `options`, `person`
- `order` (prefix with `-` to reverse): `checked_out_at`, `created_at`, `first_name`, `last_name`, `number`, `updated_at`
- Query with `where[...]`:
  - `where[account_center_person_id]` (integer), for example `?where[account_center_person_id]=1`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[security_code]` (string), for example `?where[security_code]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_in_group` | [CheckInGroup](#check_in_group) | `/check-ins/v2/check_ins/{check_in_id}/check_in_group` |  |
| `check_in_times` | [CheckInTime](#check_in_time) | `/check-ins/v2/check_ins/{check_in_id}/check_in_times` |  |
| `checked_in_at` | [Station](#station) | `/check-ins/v2/check_ins/{check_in_id}/checked_in_at` |  |
| `checked_in_by` | [Person](#person) | `/check-ins/v2/check_ins/{check_in_id}/checked_in_by` |  |
| `checked_out_by` | [Person](#person) | `/check-ins/v2/check_ins/{check_in_id}/checked_out_by` |  |
| `event` | [Event](#event) | `/check-ins/v2/check_ins/{check_in_id}/event` |  |
| `event_period` | [EventPeriod](#event_period) | `/check-ins/v2/check_ins/{check_in_id}/event_period` |  |
| `event_times` | [EventTime](#event_time) | `/check-ins/v2/check_ins/{check_in_id}/event_times` |  |
| `locations` | [Location](#location) | `/check-ins/v2/check_ins/{check_in_id}/locations` |  |
| `options` | [Option](#option) | `/check-ins/v2/check_ins/{check_in_id}/options` |  |
| `person` | [Person](#person) | `/check-ins/v2/check_ins/{check_in_id}/person` |  |

**Reached from**

- [CheckInGroup](#check_in_group) as `check_ins`: `/check-ins/v2/stations/{station_id}/check_in_groups/{check_in_group_id}/check_ins`
- [Event](#event) as `check_ins`: `/check-ins/v2/events/{event_id}/check_ins`
- [EventPeriod](#event_period) as `check_ins`: `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/check_ins`
- [EventTime](#event_time) as `check_ins`: `/check-ins/v2/event_times/{event_time_id}/check_ins`
- [Location](#location) as `check_ins`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/check_ins`
- [LocationEventPeriod](#location_event_period) as `check_ins`: `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods/{location_event_period_id}/check_ins`
- [LocationEventTime](#location_event_time) as `check_ins`: `/check-ins/v2/event_times/{event_time_id}/location_event_times/{location_event_time_id}/check_ins`
- [Option](#option) as `check_ins`: `/check-ins/v2/options/{option_id}/check_ins`
- [Organization](#organization) as `check_ins`: `/check-ins/v2/check_ins`
- [Person](#person) as `check_ins`: `/check-ins/v2/people/{person_id}/check_ins`
- [PersonEvent](#person_event) as `first_check_in`: `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/first_check_in`
- [PersonEvent](#person_event) as `last_check_in`: `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/last_check_in`
- [Station](#station) as `checked_in_at_check_ins`: `/check-ins/v2/stations/{station_id}/checked_in_at_check_ins`

**Example record**

```json
{
  "type": "CheckIn",
  "id": "1",
  "attributes": {
    "checked_out_at": "2000-01-01T12:00:00Z",
    "confirmed_at": "2000-01-01T12:00:00Z",
    "created_at": "2000-01-01T12:00:00Z",
    "emergency_contact_name": "string",
    "emergency_contact_phone_number": "string",
    "first_name": "string",
    "kind": "string",
    "last_name": "string",
    "medical_notes": "string",
    "number": 1,
    "one_time_guest": true,
    "security_code": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "event_period": {
      "data": {
        "type": "EventPeriod",
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

<a id="check_in_group"></a>
## CheckInGroup

When one or more people check in, they're grouped in a `CheckInGroup`.
These check-ins all have the same "checked-in by" person.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/stations/{station_id}/check_in_groups` | List |
| GET | `/check-ins/v2/stations/{station_id}/check_in_groups/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `check_ins_count` | integer | `1` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name_labels_count` | integer | `1` |  |
| `print_status` | string | `"string"` | Possible values: - `ready`: This group isn't printed or canceled yet - `printed`: This group was successfully printed at a station - `canceled`: This group was canceled at a station - `skipped`: This group had no labels to print, so it was never printed. |
| `security_labels_count` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `check_ins`, `event_period`, `print_station`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/stations/{station_id}/check_in_groups/{check_in_group_id}/check_ins` |  |
| `event_period` | [EventPeriod](#event_period) | `/check-ins/v2/stations/{station_id}/check_in_groups/{check_in_group_id}/event_period` |  |
| `print_station` | [Station](#station) | `/check-ins/v2/stations/{station_id}/check_in_groups/{check_in_group_id}/print_station` |  |

**Reached from**

- [CheckIn](#check_in) as `check_in_group`: `/check-ins/v2/check_ins/{check_in_id}/check_in_group`
- [Station](#station) as `check_in_groups`: `/check-ins/v2/stations/{station_id}/check_in_groups`

**Example record**

```json
{
  "type": "CheckInGroup",
  "id": "1",
  "attributes": {
    "check_ins_count": 1,
    "created_at": "2000-01-01T12:00:00Z",
    "name_labels_count": 1,
    "print_status": "string",
    "security_labels_count": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="check_in_time"></a>
## CheckInTime

A CheckInTime combines an EventTime and a Location, and associates it with
the parent CheckIn.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/check_ins/{check_in_id}/check_in_times` | List |
| GET | `/check-ins/v2/check_ins/{check_in_id}/check_in_times/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `alerts` | array | `[]` | Visible to: `station`. |
| `has_validated` | boolean | `true` | Visible to: `station`. |
| `id` | primary_key | `"primary_key"` |  |
| `kind` | string | `"string"` |  |
| `services_integrated` | boolean | `true` | Visible to: `station`. |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `event_time` | to_one | [EventTime](#event_time) |  |
| `location` | to_one | [Location](#location) |  |
| `check_in` | to_one | [CheckIn](#check_in) |  |
| `pre_check` | to_one | [PreCheck](#pre_check) |  |

**Reached from**

- [CheckIn](#check_in) as `check_in_times`: `/check-ins/v2/check_ins/{check_in_id}/check_in_times`

**Example record**

```json
{
  "type": "CheckInTime",
  "id": "1",
  "attributes": {
    "alerts": [],
    "has_validated": true,
    "kind": "string",
    "services_integrated": true
  },
  "relationships": {
    "event_time": {
      "data": {
        "type": "EventTime",
        "id": "1"
      }
    },
    "location": {
      "data": {
        "type": "Location",
        "id": "1"
      }
    },
    "check_in": {
      "data": {
        "type": "CheckIn",
        "id": "1"
      }
    },
    "pre_check": {
      "data": {
        "type": "PreCheck",
        "id": "1"
      }
    }
  }
}
```

<a id="event"></a>
## Event

A recurring event which people may attend.

Each recurrence is an _event period_ (which often corresponds to a week).

Event periods have _event times_ where people may actually check in.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/events` | List |
| GET | `/check-ins/v2/events/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `app_source` | string | `"string"` | Only available when requested with the `?fields` param |
| `archived_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `enable_services_integration` | boolean | `true` |  |
| `frequency` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `integration_key` | string | `"string"` |  |
| `location_times_enabled` | boolean | `true` |  |
| `name` | string | `"string"` |  |
| `pre_select_enabled` | boolean | `true` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `campuses` | to_many | Campus |  |

**URL parameters**

- `include`: `attendance_types`
- `order` (prefix with `-` to reverse): `created_at`, `name`
- Query with `where[...]`:
  - `where[id]` (primary_key), for example `?where[id]=primary_key`
  - `where[name]` (string), for example `?where[name]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `attendance_types` | [AttendanceType](#attendance_type) | `/check-ins/v2/events/{event_id}/attendance_types` |  |
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/events/{event_id}/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `current_event_times` | [EventTime](#event_time) | `/check-ins/v2/events/{event_id}/current_event_times` |  |
| `event_labels` | [EventLabel](#event_label) | `/check-ins/v2/events/{event_id}/event_labels` |  |
| `event_periods` | [EventPeriod](#event_period) | `/check-ins/v2/events/{event_id}/event_periods` |  |
| `integration_links` | [IntegrationLink](#integration_link) | `/check-ins/v2/events/{event_id}/integration_links` |  |
| `locations` | [Location](#location) | `/check-ins/v2/events/{event_id}/locations` | `locations`, `root` |
| `person_events` | [PersonEvent](#person_event) | `/check-ins/v2/events/{event_id}/person_events` |  |

**Reached from**

- [AttendanceType](#attendance_type) as `event`: `/check-ins/v2/events/{event_id}/attendance_types/{attendance_type_id}/event`
- [CheckIn](#check_in) as `event`: `/check-ins/v2/check_ins/{check_in_id}/event`
- [EventLabel](#event_label) as `event`: `/check-ins/v2/events/{event_id}/event_labels/{event_label_id}/event`
- [EventPeriod](#event_period) as `event`: `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/event`
- [EventTime](#event_time) as `event`: `/check-ins/v2/event_times/{event_time_id}/event`
- [Location](#location) as `event`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/event`
- [Organization](#organization) as `events`: `/check-ins/v2/events`
- [PersonEvent](#person_event) as `event`: `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/event`
- [Station](#station) as `event`: `/check-ins/v2/stations/{station_id}/event`

**Example record**

```json
{
  "type": "Event",
  "id": "1",
  "attributes": {
    "app_source": "string",
    "archived_at": "2000-01-01T12:00:00Z",
    "created_at": "2000-01-01T12:00:00Z",
    "enable_services_integration": true,
    "frequency": "string",
    "integration_key": "string",
    "location_times_enabled": true,
    "name": "string",
    "pre_select_enabled": true,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "campuses": {
      "data": [
        {
          "type": "Campus",
          "id": "1"
        }
      ]
    }
  }
}
```

<a id="event_label"></a>
## EventLabel

Says how many of a given label to print for this event and
whether to print it for regulars, guests, and/or volunteers.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/events/{event_id}/event_labels` | List |
| GET | `/check-ins/v2/events/{event_id}/event_labels/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `for_guest` | boolean | `true` |  |
| `for_regular` | boolean | `true` |  |
| `for_volunteer` | boolean | `true` |  |
| `id` | primary_key | `"primary_key"` |  |
| `quantity` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `event`, `label`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `event` | [Event](#event) | `/check-ins/v2/events/{event_id}/event_labels/{event_label_id}/event` |  |
| `label` | [Label](#label) | `/check-ins/v2/events/{event_id}/event_labels/{event_label_id}/label` |  |

**Reached from**

- [Event](#event) as `event_labels`: `/check-ins/v2/events/{event_id}/event_labels`
- [Label](#label) as `event_labels`: `/check-ins/v2/labels/{label_id}/event_labels`

**Example record**

```json
{
  "type": "EventLabel",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "for_guest": true,
    "for_regular": true,
    "for_volunteer": true,
    "quantity": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="event_period"></a>
## EventPeriod

A recurrence of an event, sometimes called a "session".
For weekly events, an event period is a week. For daily events, an event period is a day.
An event period has event times, which is what people select
when they actually check in. When new sessions are created, times
are copied from one session to the next.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/check_ins/{check_in_id}/event_period` | List |
| GET | `/check-ins/v2/check_ins/{check_in_id}/event_period/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `ends_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `guest_count` | integer | `1` |  |
| `id` | primary_key | `"primary_key"` |  |
| `note` | string | `"string"` |  |
| `regular_count` | integer | `1` |  |
| `starts_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `unique_guest_count` | integer | `1` | Deduplicated people with a Guest check-in. Only available when requested with the `?fields` param |
| `unique_regular_count` | integer | `1` | Deduplicated people with a Regular check-in. Only available when requested with the `?fields` param |
| `unique_total_count` | integer | `1` | Deduplicated people with any check-in. Only available when requested with the `?fields` param |
| `unique_volunteer_count` | integer | `1` | Deduplicated people with a Volunteer check-in. Only available when requested with the `?fields` param |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `volunteer_count` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `event` | to_one | [Event](#event) |  |

**URL parameters**

- `include`: `event`, `event_times`
- `order` (prefix with `-` to reverse): `starts_at`
- Query with `where[...]`:
  - `where[ends_at]` (date_time), for example `?where[ends_at]=2000-01-01T12:00:00Z`
  - `where[starts_at]` (date_time), for example `?where[starts_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `event` | [Event](#event) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/event` |  |
| `event_times` | [EventTime](#event_time) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/event_times` | `available` |
| `location_event_periods` | [LocationEventPeriod](#location_event_period) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods` |  |

**Reached from**

- [CheckIn](#check_in) as `event_period`: `/check-ins/v2/check_ins/{check_in_id}/event_period`
- [CheckInGroup](#check_in_group) as `event_period`: `/check-ins/v2/stations/{station_id}/check_in_groups/{check_in_group_id}/event_period`
- [Event](#event) as `event_periods`: `/check-ins/v2/events/{event_id}/event_periods`
- [EventTime](#event_time) as `event_period`: `/check-ins/v2/event_times/{event_time_id}/event_period`
- [LocationEventPeriod](#location_event_period) as `event_period`: `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods/{location_event_period_id}/event_period`

**Example record**

```json
{
  "type": "EventPeriod",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "ends_at": "2000-01-01T12:00:00Z",
    "guest_count": 1,
    "note": "string",
    "regular_count": 1,
    "starts_at": "2000-01-01T12:00:00Z",
    "unique_guest_count": 1,
    "unique_regular_count": 1,
    "unique_total_count": 1,
    "unique_volunteer_count": 1,
    "updated_at": "2000-01-01T12:00:00Z",
    "volunteer_count": 1
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

<a id="event_time"></a>
## EventTime

A time that someone may check in. Times are copied from session to session.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/event_times` | List |
| GET | `/check-ins/v2/event_times/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `day_of_week` | integer | `1` |  |
| `guest_count` | integer | `1` |  |
| `hides_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `hour` | integer | `1` |  |
| `id` | primary_key | `"primary_key"` |  |
| `minute` | integer | `1` |  |
| `name` | string | `"string"` |  |
| `regular_count` | integer | `1` |  |
| `shows_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `starts_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `total_count` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `volunteer_count` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `event` | to_one | [Event](#event) |  |
| `event_period` | to_one | [EventPeriod](#event_period) |  |

**URL parameters**

- `include`: `event`, `event_period`, `headcounts`
- `order` (prefix with `-` to reverse): `shows_at`, `starts_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `available_locations` | [Location](#location) | `/check-ins/v2/event_times/{event_time_id}/available_locations` |  |
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/event_times/{event_time_id}/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `event` | [Event](#event) | `/check-ins/v2/event_times/{event_time_id}/event` |  |
| `event_period` | [EventPeriod](#event_period) | `/check-ins/v2/event_times/{event_time_id}/event_period` |  |
| `headcounts` | [Headcount](#headcount) | `/check-ins/v2/event_times/{event_time_id}/headcounts` |  |
| `location_event_times` | [LocationEventTime](#location_event_time) | `/check-ins/v2/event_times/{event_time_id}/location_event_times` |  |

**Reached from**

- [CheckIn](#check_in) as `event_times`: `/check-ins/v2/check_ins/{check_in_id}/event_times`
- [Event](#event) as `current_event_times`: `/check-ins/v2/events/{event_id}/current_event_times`
- [EventPeriod](#event_period) as `event_times`: `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/event_times`
- [Headcount](#headcount) as `event_time`: `/check-ins/v2/headcounts/{headcount_id}/event_time`
- [LocationEventTime](#location_event_time) as `event_time`: `/check-ins/v2/event_times/{event_time_id}/location_event_times/{location_event_time_id}/event_time`
- [Organization](#organization) as `event_times`: `/check-ins/v2/event_times`

**Example record**

```json
{
  "type": "EventTime",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "day_of_week": 1,
    "guest_count": 1,
    "hides_at": "2000-01-01T12:00:00Z",
    "hour": 1,
    "minute": 1,
    "name": "string",
    "regular_count": 1,
    "shows_at": "2000-01-01T12:00:00Z",
    "starts_at": "2000-01-01T12:00:00Z",
    "total_count": 1,
    "updated_at": "2000-01-01T12:00:00Z",
    "volunteer_count": 1
  },
  "relationships": {
    "event": {
      "data": {
        "type": "Event",
        "id": "1"
      }
    },
    "event_period": {
      "data": {
        "type": "EventPeriod",
        "id": "1"
      }
    }
  }
}
```

<a id="headcount"></a>
## Headcount

A tally of attendees for a given event time and attendance type.
If one does not exist, the count may have been zero.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/headcounts` | List |
| GET | `/check-ins/v2/headcounts/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `total` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `event_time` | to_one | [EventTime](#event_time) |  |
| `attendance_type` | to_one | [AttendanceType](#attendance_type) |  |

**URL parameters**

- `include`: `attendance_type`, `event_time`
- `order` (prefix with `-` to reverse): `created_at`, `total`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `attendance_type` | [AttendanceType](#attendance_type) | `/check-ins/v2/headcounts/{headcount_id}/attendance_type` |  |
| `event_time` | [EventTime](#event_time) | `/check-ins/v2/headcounts/{headcount_id}/event_time` |  |

**Reached from**

- [AttendanceType](#attendance_type) as `headcounts`: `/check-ins/v2/events/{event_id}/attendance_types/{attendance_type_id}/headcounts`
- [EventTime](#event_time) as `headcounts`: `/check-ins/v2/event_times/{event_time_id}/headcounts`
- [Organization](#organization) as `headcounts`: `/check-ins/v2/headcounts`

**Example record**

```json
{
  "type": "Headcount",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "total": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "event_time": {
      "data": {
        "type": "EventTime",
        "id": "1"
      }
    },
    "attendance_type": {
      "data": {
        "type": "AttendanceType",
        "id": "1"
      }
    }
  }
}
```

<a id="integration_link"></a>
## IntegrationLink

A record linking another product's resource to a Check-Ins resource.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/integration_links` | List |
| GET | `/check-ins/v2/integration_links/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `remote_app` | string | `"string"` |  |
| `remote_gid` | string | `"string"` | The Global ID for the external resource. Formatted as `gid://<app>/<type>/<id>`. |
| `remote_id` | string | `"string"` |  |
| `remote_type` | string | `"string"` |  |
| `sync_future_assignment_types` | boolean | `true` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `local` | to_one | [Event](#event) | Type will be the type of Check-Ins resource it is linked to. Polymorphic. |

**URL parameters**

- Query with `where[...]`:
  - `where[remote_gid]` (string), for example `?where[remote_gid]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Event](#event) as `integration_links`: `/check-ins/v2/events/{event_id}/integration_links`
- [Location](#location) as `integration_links`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/integration_links`
- [Organization](#organization) as `integration_links`: `/check-ins/v2/integration_links`

**Example record**

```json
{
  "type": "IntegrationLink",
  "id": "1",
  "attributes": {
    "remote_app": "string",
    "remote_gid": "string",
    "remote_id": "string",
    "remote_type": "string",
    "sync_future_assignment_types": true
  },
  "relationships": {
    "local": {
      "data": {
        "type": "Event",
        "id": "1"
      }
    }
  }
}
```

<a id="label"></a>
## Label

Labels can be set to print for events (through `EventLabel`s),
locations (through `LocationLabel`s) or options.
Label type (security label / name label) is expressed with the
`prints_for` attribute. `prints_for="Person"` is a name label,
`prints_for="Group"` is a security label.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/labels` | List |
| GET | `/check-ins/v2/labels/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `prints_for` | string | `"string"` |  |
| `roll` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `xml` | string | `"string"` |  |

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `event_labels` | [EventLabel](#event_label) | `/check-ins/v2/labels/{label_id}/event_labels` |  |
| `location_labels` | [LocationLabel](#location_label) | `/check-ins/v2/labels/{label_id}/location_labels` |  |

**Reached from**

- [EventLabel](#event_label) as `label`: `/check-ins/v2/events/{event_id}/event_labels/{event_label_id}/label`
- [LocationLabel](#location_label) as `label`: `/check-ins/v2/labels/{label_id}/location_labels/{location_label_id}/label`
- [Option](#option) as `label`: `/check-ins/v2/options/{option_id}/label`
- [Organization](#organization) as `labels`: `/check-ins/v2/labels`

**Example record**

```json
{
  "type": "Label",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "name": "string",
    "prints_for": "string",
    "roll": "string",
    "updated_at": "2000-01-01T12:00:00Z",
    "xml": "string"
  },
  "relationships": {}
}
```

<a id="location"></a>
## Location

A place where people may check in to for a given event.
Some locations have `kind="Folder"`, which means that people
can't check-in here, but this location contains other locations.
You can get its contents from the `locations` attribute.
You can get a location's parent folder from the `parent` attribute.
(If it's not in a folder, `parent` will be empty.)

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/check_ins/{check_in_id}/locations` | List |
| GET | `/check-ins/v2/check_ins/{check_in_id}/locations/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `age_max_in_months` | integer | `1` |  |
| `age_min_in_months` | integer | `1` |  |
| `age_on` | date | `"2000-01-01"` |  |
| `age_range_by` | string | `"string"` |  |
| `attendees_per_volunteer` | integer | `1` |  |
| `child_or_adult` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `effective_date` | date | `"2000-01-01"` |  |
| `gender` | string | `"string"` |  |
| `grade_max` | integer | `1` |  |
| `grade_min` | integer | `1` |  |
| `id` | primary_key | `"primary_key"` |  |
| `kind` | string | `"string"` |  |
| `max_occupancy` | integer | `1` |  |
| `milestone` | string | `"string"` |  |
| `min_volunteers` | integer | `1` |  |
| `name` | string | `"string"` |  |
| `opened` | boolean | `true` |  |
| `position` | integer | `1` |  |
| `questions` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `parent` | to_one | Parent |  |

**URL parameters**

- `include`: `event`, `locations`, `options`, `parent`
- `order` (prefix with `-` to reverse): `kind`, `name`, `position`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `event` | [Event](#event) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/event` |  |
| `integration_links` | [IntegrationLink](#integration_link) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/integration_links` |  |
| `location_event_periods` | [LocationEventPeriod](#location_event_period) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/location_event_periods` |  |
| `location_event_times` | [LocationEventTime](#location_event_time) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/location_event_times` |  |
| `location_labels` | [LocationLabel](#location_label) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/location_labels` |  |
| `locations` | [Location](#location) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/locations` |  |
| `options` | [Option](#option) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/options` |  |
| `parent` | [Location](#location) | `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/parent` |  |

**Reached from**

- [CheckIn](#check_in) as `locations`: `/check-ins/v2/check_ins/{check_in_id}/locations`
- [Event](#event) as `locations`: `/check-ins/v2/events/{event_id}/locations`
- [EventTime](#event_time) as `available_locations`: `/check-ins/v2/event_times/{event_time_id}/available_locations`
- [LocationEventPeriod](#location_event_period) as `location`: `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods/{location_event_period_id}/location`
- [LocationEventTime](#location_event_time) as `location`: `/check-ins/v2/event_times/{event_time_id}/location_event_times/{location_event_time_id}/location`
- [LocationLabel](#location_label) as `location`: `/check-ins/v2/labels/{label_id}/location_labels/{location_label_id}/location`
- [Location](#location) as `locations`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/locations`
- [Location](#location) as `parent`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/parent`
- [Station](#station) as `location`: `/check-ins/v2/stations/{station_id}/location`

**Example record**

```json
{
  "type": "Location",
  "id": "1",
  "attributes": {
    "age_max_in_months": 1,
    "age_min_in_months": 1,
    "age_on": "2000-01-01",
    "age_range_by": "string",
    "attendees_per_volunteer": 1,
    "child_or_adult": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "effective_date": "2000-01-01",
    "gender": "string",
    "grade_max": 1,
    "grade_min": 1,
    "kind": "string",
    "max_occupancy": 1,
    "milestone": "string",
    "min_volunteers": 1,
    "name": "string",
    "opened": true,
    "position": 1,
    "questions": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "parent": {
      "data": {
        "type": "Parent",
        "id": "1"
      }
    }
  }
}
```

<a id="location_event_period"></a>
## LocationEventPeriod

Counts check-ins for a location during a certain event period.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods` | List |
| GET | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `guest_count` | integer | `1` |  |
| `id` | primary_key | `"primary_key"` |  |
| `regular_count` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `volunteer_count` | integer | `1` |  |

**URL parameters**

- `include`: `event_period`, `location`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods/{location_event_period_id}/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `event_period` | [EventPeriod](#event_period) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods/{location_event_period_id}/event_period` |  |
| `location` | [Location](#location) | `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods/{location_event_period_id}/location` |  |

**Reached from**

- [EventPeriod](#event_period) as `location_event_periods`: `/check-ins/v2/check_ins/{check_in_id}/event_period/{event_period_id}/location_event_periods`
- [Location](#location) as `location_event_periods`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/location_event_periods`

**Example record**

```json
{
  "type": "LocationEventPeriod",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "guest_count": 1,
    "regular_count": 1,
    "updated_at": "2000-01-01T12:00:00Z",
    "volunteer_count": 1
  },
  "relationships": {}
}
```

<a id="location_event_time"></a>
## LocationEventTime

Counts check-ins for a location for a given event time.
This is useful for checking occupancy.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/event_times/{event_time_id}/location_event_times` | List |
| GET | `/check-ins/v2/event_times/{event_time_id}/location_event_times/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `guest_count` | integer | `1` |  |
| `id` | primary_key | `"primary_key"` |  |
| `regular_count` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `volunteer_count` | integer | `1` |  |

**URL parameters**

- `include`: `event_time`, `location`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/event_times/{event_time_id}/location_event_times/{location_event_time_id}/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `event_time` | [EventTime](#event_time) | `/check-ins/v2/event_times/{event_time_id}/location_event_times/{location_event_time_id}/event_time` |  |
| `location` | [Location](#location) | `/check-ins/v2/event_times/{event_time_id}/location_event_times/{location_event_time_id}/location` |  |

**Reached from**

- [EventTime](#event_time) as `location_event_times`: `/check-ins/v2/event_times/{event_time_id}/location_event_times`
- [Location](#location) as `location_event_times`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/location_event_times`

**Example record**

```json
{
  "type": "LocationEventTime",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "guest_count": 1,
    "regular_count": 1,
    "updated_at": "2000-01-01T12:00:00Z",
    "volunteer_count": 1
  },
  "relationships": {}
}
```

<a id="location_label"></a>
## LocationLabel

Says how many of a given label to print for this location and
whether to print it for regulars, guests, and/or volunteers.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/labels/{label_id}/location_labels` | List |
| GET | `/check-ins/v2/labels/{label_id}/location_labels/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `for_guest` | boolean | `true` |  |
| `for_regular` | boolean | `true` |  |
| `for_volunteer` | boolean | `true` |  |
| `id` | primary_key | `"primary_key"` |  |
| `quantity` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `label`, `location`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `label` | [Label](#label) | `/check-ins/v2/labels/{label_id}/location_labels/{location_label_id}/label` |  |
| `location` | [Location](#location) | `/check-ins/v2/labels/{label_id}/location_labels/{location_label_id}/location` |  |

**Reached from**

- [Label](#label) as `location_labels`: `/check-ins/v2/labels/{label_id}/location_labels`
- [Location](#location) as `location_labels`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/location_labels`

**Example record**

```json
{
  "type": "LocationLabel",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "for_guest": true,
    "for_regular": true,
    "for_volunteer": true,
    "quantity": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="option"></a>
## Option

An option which an attendee may select when checking in.

Options may have extra labels associated with them, denoted by `label` and `quantity`.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/options` | List |
| GET | `/check-ins/v2/options/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `body` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `quantity` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `label`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/options/{option_id}/check_ins` |  |
| `label` | [Label](#label) | `/check-ins/v2/options/{option_id}/label` |  |

**Reached from**

- [CheckIn](#check_in) as `options`: `/check-ins/v2/check_ins/{check_in_id}/options`
- [Location](#location) as `options`: `/check-ins/v2/check_ins/{check_in_id}/locations/{location_id}/options`
- [Organization](#organization) as `options`: `/check-ins/v2/options`

**Example record**

```json
{
  "type": "Option",
  "id": "1",
  "attributes": {
    "body": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "quantity": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="organization"></a>
## Organization

An organization which has people and events.
This contains its date format & time zone preferences.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2` | List |
| GET | `/check-ins/v2/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `avatar_url` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `daily_check_ins` | integer | `1` |  |
| `date_format_pattern` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `time_zone` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `event_times` | [EventTime](#event_time) | `/check-ins/v2/event_times` |  |
| `events` | [Event](#event) | `/check-ins/v2/events` | `archived`, `for_campus`, `for_headcounts`, `for_registrations`, `not_archived` |
| `headcounts` | [Headcount](#headcount) | `/check-ins/v2/headcounts` |  |
| `integration_links` | [IntegrationLink](#integration_link) | `/check-ins/v2/integration_links` |  |
| `labels` | [Label](#label) | `/check-ins/v2/labels` |  |
| `options` | [Option](#option) | `/check-ins/v2/options` |  |
| `passes` | [Pass](#pass) | `/check-ins/v2/passes` |  |
| `people` | [Person](#person) | `/check-ins/v2/people` |  |
| `stations` | [Station](#station) | `/check-ins/v2/stations` |  |
| `themes` | [Theme](#theme) | `/check-ins/v2/themes` |  |

**Reached from**

- [Person](#person) as `organization`: `/check-ins/v2/people/{person_id}/organization`

**Example record**

```json
{
  "type": "Organization",
  "id": "1",
  "attributes": {
    "avatar_url": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "daily_check_ins": 1,
    "date_format_pattern": "string",
    "name": "string",
    "time_zone": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="pass"></a>
## Pass

Enables quick lookup of a person via barcode reader.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/passes` | List |
| GET | `/check-ins/v2/passes/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `code` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `kind` | string | `"string"` | Possible values: `barcode` or `pkpass`. Using the `pkpass` value creates a mobile pass and sends an email to the associated person. |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `person`
- Query with `where[...]`:
  - `where[code]` (string), for example `?where[code]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/check-ins/v2/passes/{pass_id}/person` |  |

**Reached from**

- [Organization](#organization) as `passes`: `/check-ins/v2/passes`
- [Person](#person) as `passes`: `/check-ins/v2/people/{person_id}/passes`

**Example record**

```json
{
  "type": "Pass",
  "id": "1",
  "attributes": {
    "code": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "kind": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="person"></a>
## Person

An attendee, volunteer or administrator.

_Usually_, a person who checked in will be present as a `Person`. In some cases, they may not be present:
- The person was manually deleted from the admin interface
- The check-in was created as a "Visitor - Label Only" (which doesn't create a corresponding person record)

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/people` | List |
| GET | `/check-ins/v2/people/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `addresses` | array | `[]` |  |
| `avatar_url` | string | `"string"` |  |
| `birthdate` | date | `"2000-01-01"` |  |
| `check_in_count` | integer | `1` |  |
| `child` | boolean | `true` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `demographic_avatar_url` | string | `"string"` |  |
| `email_addresses` | array | `[]` |  |
| `first_name` | string | `"string"` |  |
| `gender` | string | `"string"` |  |
| `grade` | integer | `1` |  |
| `headcounter` | boolean | `true` |  |
| `id` | primary_key | `"primary_key"` |  |
| `ignore_filters` | boolean | `true` |  |
| `last_checked_in_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `last_name` | string | `"string"` |  |
| `medical_notes` | string | `"string"` |  |
| `middle_name` | string | `"string"` |  |
| `name` | string | `"string"` |  |
| `name_prefix` | string | `"string"` |  |
| `name_suffix` | string | `"string"` |  |
| `passed_background_check` | boolean | `true` |  |
| `permission` | string | `"string"` |  |
| `phone_numbers` | array | `[]` |  |
| `top_permission` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `organization`
- `order` (prefix with `-` to reverse): `check_in_count`, `first_name`, `last_checked_in_at`, `last_name`
- Query with `where[...]`:
  - `where[headcounter]` (boolean), for example `?where[headcounter]=true`
  - `where[ignore_filters]` (boolean), for example `?where[ignore_filters]=true`
  - `where[permission]` (string), for example `?where[permission]=string`
  - `where[search_name]` (string), for example `?where[search_name]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_ins` | [CheckIn](#check_in) | `/check-ins/v2/people/{person_id}/check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `organization` | [Organization](#organization) | `/check-ins/v2/people/{person_id}/organization` |  |
| `passes` | [Pass](#pass) | `/check-ins/v2/people/{person_id}/passes` |  |
| `person_events` | [PersonEvent](#person_event) | `/check-ins/v2/people/{person_id}/person_events` |  |

**Reached from**

- [CheckIn](#check_in) as `checked_in_by`: `/check-ins/v2/check_ins/{check_in_id}/checked_in_by`
- [CheckIn](#check_in) as `checked_out_by`: `/check-ins/v2/check_ins/{check_in_id}/checked_out_by`
- [CheckIn](#check_in) as `person`: `/check-ins/v2/check_ins/{check_in_id}/person`
- [Organization](#organization) as `people`: `/check-ins/v2/people`
- [Pass](#pass) as `person`: `/check-ins/v2/passes/{pass_id}/person`
- [PersonEvent](#person_event) as `person`: `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/person`

**Example record**

```json
{
  "type": "Person",
  "id": "1",
  "attributes": {
    "addresses": [],
    "avatar_url": "string",
    "birthdate": "2000-01-01",
    "check_in_count": 1,
    "child": true,
    "created_at": "2000-01-01T12:00:00Z",
    "demographic_avatar_url": "string",
    "email_addresses": [],
    "first_name": "string",
    "gender": "string",
    "grade": 1,
    "headcounter": true,
    "ignore_filters": true,
    "last_checked_in_at": "2000-01-01T12:00:00Z",
    "last_name": "string",
    "medical_notes": "string",
    "middle_name": "string",
    "name": "string",
    "name_prefix": "string",
    "name_suffix": "string",
    "passed_background_check": true,
    "permission": "string",
    "phone_numbers": [],
    "top_permission": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="person_event"></a>
## PersonEvent

Counts a person's attendence for a given event.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/events/{event_id}/person_events` | List |
| GET | `/check-ins/v2/events/{event_id}/person_events/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `check_in_count` | integer | `1` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `event`, `first_check_in`, `last_check_in`, `person`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `event` | [Event](#event) | `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/event` |  |
| `first_check_in` | [CheckIn](#check_in) | `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/first_check_in` |  |
| `last_check_in` | [CheckIn](#check_in) | `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/last_check_in` |  |
| `person` | [Person](#person) | `/check-ins/v2/events/{event_id}/person_events/{person_event_id}/person` |  |

**Reached from**

- [Event](#event) as `person_events`: `/check-ins/v2/events/{event_id}/person_events`
- [Person](#person) as `person_events`: `/check-ins/v2/people/{person_id}/person_events`

**Example record**

```json
{
  "type": "PersonEvent",
  "id": "1",
  "attributes": {
    "check_in_count": 1,
    "created_at": "2000-01-01T12:00:00Z",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="pre_check"></a>
## PreCheck

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2` | List |
| GET | `/check-ins/v2/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |

**Example record**

```json
{
  "type": "PreCheck",
  "id": "1",
  "attributes": {},
  "relationships": {}
}
```

<a id="roster_list_person"></a>
## RosterListPerson

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2` | List |
| GET | `/check-ins/v2/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `birthdate` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `demographic_avatar_url` | string | `"string"` |  |
| `first_name` | string | `"string"` |  |
| `gender` | string | `"string"` |  |
| `grade` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `ignore_filters` | boolean | `true` |  |
| `last_name` | string | `"string"` |  |
| `medical_notes` | string | `"string"` |  |
| `name` | string | `"string"` |  |

**Example record**

```json
{
  "type": "RosterListPerson",
  "id": "1",
  "attributes": {
    "birthdate": "2000-01-01T12:00:00Z",
    "demographic_avatar_url": "string",
    "first_name": "string",
    "gender": "string",
    "grade": "string",
    "ignore_filters": true,
    "last_name": "string",
    "medical_notes": "string",
    "name": "string"
  },
  "relationships": {}
}
```

<a id="station"></a>
## Station

A device where people can be checked in.
A device may also be connected to a printer
and print labels for itself or other stations.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/stations` | List |
| GET | `/check-ins/v2/stations/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `check_in_count` | integer | `1` | Only available when requested with the `?fields` param |
| `closes_at` | date_time | `"2000-01-01T12:00:00Z"` | Only available when requested with the `?fields` param |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `input_type` | string | `"value"` | Possible values: `scanner` or `keypad` |
| `input_type_options` | string | `"value"` | Possible values: `all_input_types`, `only_keypad`, or `only_scanner` |
| `mode` | integer | `1` |  |
| `name` | string | `"string"` |  |
| `next_shows_at` | date_time | `"2000-01-01T12:00:00Z"` | Only available when requested with the `?fields` param |
| `online` | boolean | `true` | Only available when requested with the `?fields` param |
| `open_for_check_in` | boolean | `true` | Only available when requested with the `?fields` param |
| `seconds_until_closes_at` | integer | `1` | Only available when requested with the `?fields` param |
| `seconds_until_next_shows_at` | integer | `1` | Only available when requested with the `?fields` param |
| `timeout_seconds` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `event`, `location`, `print_station`, `theme`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `check_in_groups` | [CheckInGroup](#check_in_group) | `/check-ins/v2/stations/{station_id}/check_in_groups` | `canceled`, `printed`, `ready`, `skipped` |
| `checked_in_at_check_ins` | [CheckIn](#check_in) | `/check-ins/v2/stations/{station_id}/checked_in_at_check_ins` | `attendee`, `checked_out`, `first_time`, `guest`, `not_checked_out`, `not_one_time_guest`, `one_time_guest`, `regular`, `volunteer` |
| `event` | [Event](#event) | `/check-ins/v2/stations/{station_id}/event` |  |
| `location` | [Location](#location) | `/check-ins/v2/stations/{station_id}/location` |  |
| `print_station` | [Station](#station) | `/check-ins/v2/stations/{station_id}/print_station` |  |
| `theme` | [Theme](#theme) | `/check-ins/v2/stations/{station_id}/theme` |  |

**Reached from**

- [CheckIn](#check_in) as `checked_in_at`: `/check-ins/v2/check_ins/{check_in_id}/checked_in_at`
- [CheckInGroup](#check_in_group) as `print_station`: `/check-ins/v2/stations/{station_id}/check_in_groups/{check_in_group_id}/print_station`
- [Organization](#organization) as `stations`: `/check-ins/v2/stations`
- [Station](#station) as `print_station`: `/check-ins/v2/stations/{station_id}/print_station`

**Example record**

```json
{
  "type": "Station",
  "id": "1",
  "attributes": {
    "check_in_count": 1,
    "closes_at": "2000-01-01T12:00:00Z",
    "created_at": "2000-01-01T12:00:00Z",
    "input_type": "value",
    "input_type_options": "value",
    "mode": 1,
    "name": "string",
    "next_shows_at": "2000-01-01T12:00:00Z",
    "online": true,
    "open_for_check_in": true,
    "seconds_until_closes_at": 1,
    "seconds_until_next_shows_at": 1,
    "timeout_seconds": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="theme"></a>
## Theme

A custom style which may be applied to stations.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/check-ins/v2/themes` | List |
| GET | `/check-ins/v2/themes/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `background_color` | string | `"string"` |  |
| `color` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `image` | string | `"string"` |  |
| `image_thumbnail` | string | `"string"` |  |
| `mode` | string | `"string"` |  |
| `name` | string | `"string"` |  |
| `text_color` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Reached from**

- [Organization](#organization) as `themes`: `/check-ins/v2/themes`
- [Station](#station) as `theme`: `/check-ins/v2/stations/{station_id}/theme`

**Example record**

```json
{
  "type": "Theme",
  "id": "1",
  "attributes": {
    "background_color": "string",
    "color": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "image": "string",
    "image_thumbnail": "string",
    "mode": "string",
    "name": "string",
    "text_color": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```
