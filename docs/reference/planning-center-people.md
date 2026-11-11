# Planning Center People API reference

Planning Center People helps you manage contact data, membership info, and everything you need to know about your people with confidence.

Generated on 2026-11-11 from Planning Center's published API documentation (`https://api.planningcenteronline.com/people/v2/documentation`), version `2026-10-05`. 67 resources. Shared rules (authentication, rate limits, versions, paging) are in [planning-center-api.md](planning-center-api.md).

- **Base URL:** `https://api.planningcenteronline.com/people/v2`
- **Starting point:** [Organization](#organization) at `/people/v2`
- **Version header:** `X-PCO-API-Version: 2026-10-05`

## Versions

| Version | Change |
|---|---|
| `2026-10-05` | Replace `resource_permission_flags` attribute with `selective_access` in `Person` |
| `2026-06-04` | Remove `avatar_url` attribute from `Campus` |
| `2025-11-10` | Remove `gender` relationship from `Person` |
| `2025-07-17` | Set 'name' attribute as auto-generated name if no manually set name is present |
| `2025-07-02` | Remove `note_category` relationship and allow creating profile note by assigning `note_category_id` |
| `2025-03-20` | Prevent archived workflows from the `Workflow` association |
| `2024-09-12` | Excludes total counts from `meta` section for single resources in `List`,`Form` and `Workflow` |
| `2023-03-21` | First public version of the Background Checks API |
| `2023-02-15` | Change `display_value` attribute in `FormSubmissionValue` |
| `2022-07-14` | Change `gender` attribute in `Person` |
| `2022-01-28` | Remove `slug` as an assignable attribute from `Tab` |
| `2022-01-05` | Remove `all` filter from `Message`'s `Person` association |
| `2021-08-17` | Remove `note_count` and `note_person_count` attributes from `NoteCategory` |
| `2020-07-22` | Change `school_type` attribute in `Person` |
| `2020-04-06` | Remove `total_snoozed_card_count` attribute from `WorkflowStep` |
| `2019-10-10` | Remove `owner` include from `List` |
| `2019-01-14` | Change `time_zone` attribute in `Campus` |
| `2018-08-01` | The first available version of the API |

## Resources

| Resource | Path | What it is |
|---|---|---|
| [Address](#address) | `/people/v2/addresses` | An address represents a physical and/or mailing address for a person. |
| [App](#app) | `/people/v2/apps` | An app is one of the handful of apps that Planning Center offers that organizations can subscribe to, e.g. Services, Registrations, etc. |
| [BackgroundCheck](#background_check) | `/people/v2/background_checks` | Background Checks for a Person |
| [BirthdayPeople](#birthday_people) | `/people/v2/birthday_people` | Returns upcoming birthdays for the organization. Note: This endpoint will always only return the first 15 people having a birthday in the next 30 days, ordered by birthday, ascending. |
| [Campus](#campus) | `/people/v2/campuses` | A Campus is a location belonging to an Organization |
| [Carrier](#carrier) | `/people/v2/carriers` |  |
| [Condition](#condition) | `/people/v2/lists/{list_id}/rules/{rule_id}/conditions` | A condition is an individual criterion used by a List Rule. |
| [ConnectedPerson](#connected_person) | `/people/v2/people/{person_id}/connected_people` | A Connected Person is an account from a different organization linked to an account in this organization. |
| [CustomSender](#custom_sender) | `/people/v2` | A custom sender that can be used when sending emails. |
| [Email](#email) | `/people/v2/emails` | An email represents an email address and location. |
| [FieldDatum](#field_datum) | `/people/v2/field_data` | A field datum is an individual piece of data for a custom field. |
| [FieldDefinition](#field_definition) | `/people/v2/field_definitions` | A field definition represents a custom field -- its name, data type, etc. |
| [FieldOption](#field_option) | `/people/v2/field_definitions/{field_definition_id}/field_options` | A field option represents an individual option for a custom field of type "select" or "checkboxes". |
| [Form](#form) | `/people/v2/forms` | A custom form for people to fill out. |
| [FormCategory](#form_category) | `/people/v2/form_categories` | A Form Category |
| [FormField](#form_field) | `/people/v2/forms/{form_id}/fields` | A field in a custom form. |
| [FormFieldCondition](#form_field_condition) | `/people/v2/forms/{form_id}/fields/{field_id}/form_field_conditions` | A rule that conditionally shows a form field based on the answer given to another (trigger) field. A condition belongs to the `form_field` it shows, and points at a `trigger_form_field` whose answer determines whether that field is displayed. A form field can have at most one condition. The trigger is matched in one of two ways: - `trigger_form_field_option` — the condition is met when the answer to the trigger field is this option (used for option-based fields such as dropdowns, gender, campus, etc.). - `trigger_form_field_option_value` — the condition is met when the answer to the trigger field equals this string (used for boolean custom fields). When neither is set, the condition is met whenever the trigger field has any answer. Conditions are created, updated, and destroyed under the form field they belong to (`POST`/`PATCH`/`DELETE .../fields/{id}/form_field_conditions`). The `form_field` and `trigger_form_field` must both belong to the same form, and the `trigger_form_field_option` must belong to the `trigger_form_field`. Writing requires permission to manage the form. |
| [FormFieldOption](#form_field_option) | `/people/v2/forms/{form_id}/fields/{field_id}/options` | A field option on a custom form field. |
| [FormSubmission](#form_submission) | `/people/v2/forms/{form_id}/form_submissions` | A form submission represents a single completed entry submitted through a Planning Center form. ### Creating submissions `POST` is supported so external integrations can record submissions captured outside of Church Center — for example, paper forms run through OCR. Creating a submission requires the access token to belong to a user who (1) has overall People permissions of Editor or above and (2) can manage the target form (a Manager, or a user with a `manage` `FormShare`). ### Identifying the submitter Provide either `person_id` (an existing person in your organization) or `person_attributes`. When `person_attributes` is supplied, the API matches against existing profiles by first name (or given name or nickname) + last name + email address. If no match is found, a new person is created automatically. `person_attributes` accepts `first_name`, `last_name`, and `emails_attributes` (an array with at least one `{ "location": "...", "address": "..." }` hash). ### Form values Pass each answer as an `included` resource of type `FormSubmissionValue`, with `form_field_id` referencing a field on the form. The shape of `value` depends on the field's `field_type`: plain fields take a string, phone and address fields take an object, and file fields take an array of uploaded file references. See the `FormSubmissionValue` vertex for the full list of shapes and for how to upload files. ``` { "data": { "attributes": { "person_id": "1" } }, "included": [ { "type": "FormSubmissionValue", "attributes": { "form_field_id": "10", "value": "Blue" } }, { "type": "FormSubmissionValue", "attributes": { "form_field_id": "11", "value": { "number": "555-555-1234", "location": "Mobile" } } }, { "type": "FormSubmissionValue", "attributes": { "form_field_id": "12", "value": { "location": "Home", "street": "123 Main St", "city": "Carlsbad", "state": "CA", "zip": "92008", "country_code": "US" } } }, { "type": "FormSubmissionValue", "attributes": { "form_field_id": "13", "value": [{ "fileID": "us4-2b1f6c0e-5d3a-4f7e-9c21-8a6d0e4b7f10", "fileName": "waiver.pdf" }] } } ] } ``` One important note: if the form field has options (for example, a checkboxes or dropdown field), the submitted `value` should be the ID of the selected option, not the option's text. ### Side effects A submission triggers the same side effects as a Church Center submission: notification emails to the form's subscribers, a confirmation email to the submitter (if the form is configured to send one), workflow card creation for workflow answer fields, and profile updates for contact info, birthdate, gender, household, custom fields, and other profile data. The access token's user is recorded as the submitter, unless they are the person the submission is for. Profile updates from the submission are attributed to that user in the person's history. When the resolved person is brand new (created by this submission), profile updates happen immediately and the submission is auto-verified. When the resolved person already exists and the form contains verification-required fields, a verification email is sent first; profile updates apply only after the submitter clicks the verification link — matching the behavior of an unauthenticated Church Center submission. |
| [FormSubmissionValue](#form_submission_value) | `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form_submission_values` | A single answer to a single field within a form submission. When creating a `FormSubmission`, supply one `FormSubmissionValue` per answered field as a JSON:API `included` resource. Each must reference a `form_field_id` belonging to the parent form; values referencing fields on other forms are silently discarded. Blank values are also discarded. `value` is write-only. It is accepted when creating a submission but is never returned when reading one. Read answers back through `display_value` and, for file fields, `attachments`. ### Uploading files File answers reference files that have already been uploaded to the Planning Center upload service. Upload each file first with a multipart `POST` to `https://upload.planningcenteronline.com/v2/files`, one file per request. The request authenticates like any other Planning Center API request: a personal access token as HTTP Basic, or an OAuth access token as a `Bearer` token. It does not have to be the same credential that later creates the submission. ``` curl -X POST https://upload.planningcenteronline.com/v2/files -u <application id>:<secret> -F "file=@waiver.pdf" ``` The response lists the uploaded file under `data`. Use `data[0].id` as `fileID` and `data[0].attributes.name` as `fileName`. `fileID` must be sent exactly as returned, including its datacenter prefix (such as `us4-`). Uploads are temporary. `data[0].attributes.expires_at` is 60 minutes after the upload, so create the submission soon after uploading. People copies the file into its own storage when the submission is created, and that copy is unaffected by the upload expiring. A file answer that is malformed, lacks the prefix, or points at an expired or unknown upload fails the request with a `422` validation error on `value` that says which. ``` { "data": { "attributes": { "person_id": "1" } }, "included": [ { "type": "FormSubmissionValue", "attributes": { "form_field_id": "42", "value": [{ "fileID": "us4-2b1f6c0e-5d3a-4f7e-9c21-8a6d0e4b7f10", "fileName": "waiver.pdf" }] } } ] } ``` |
| [Grade](#grade) | `/people/v2` |  |
| [Household](#household) | `/people/v2/households` | A household links people together and can have a primary contact. To add a person to an existing household, use the HouseholdMemberships endpoint. |
| [HouseholdMembership](#household_membership) | `/people/v2/households/{household_id}/household_memberships` | A household membership is the linking record between a household and a person. |
| [InactiveReason](#inactive_reason) | `/people/v2/inactive_reasons` | An inactive reason is a small bit of text indicating why a member is no longer active. |
| [List](#list) | `/people/v2/lists` | A list is a powerful tool for finding and grouping people together using any criteria imaginable. |
| [ListCategory](#list_category) | `/people/v2/list_categories` | A List Category |
| [ListResult](#list_result) | `/people/v2/lists/{list_id}/list_results` | A list result |
| [ListShare](#list_share) | `/people/v2/lists/{list_id}/shares` | A list share indicates who has access to edit a list. |
| [ListStar](#list_star) | `/people/v2/lists/{list_id}/star` | A starred list for a person indicates it is special in some way |
| [MailchimpSyncStatus](#mailchimp_sync_status) | `/people/v2/lists/{list_id}/mailchimp_sync_status` | The status of syncing a List with Mailchimp. |
| [MaritalStatus](#marital_status) | `/people/v2/marital_statuses` | A martial status represents a member's current status, e.g. married, single, etc. |
| [MembershipType](#membership_type) | `/people/v2/membership_types` | A profile's membership type, e.g. Visitor, Regular Attender, Member, etc. |
| [Message](#message) | `/people/v2/messages` | A message is an individual email or sms text sent to a member. Every message has a parent message group. |
| [MessageGroup](#message_group) | `/people/v2/message_groups` | A message group represents one or more emails or text messages sent from one of the Planning Center apps. The message group indicates the from person, app, etc. |
| [NamePrefix](#name_prefix) | `/people/v2/name_prefixes` | A name prefix is one of Mr., Mrs., etc. |
| [NameSuffix](#name_suffix) | `/people/v2/name_suffixes` | A name suffix is one of Sr., Jr., etc. |
| [Note](#note) | `/people/v2/notes` | A note is text with a category connected to a person’s profile. |
| [NoteCategory](#note_category) | `/people/v2/note_categories` | A Note Category |
| [NoteCategoryShare](#note_category_share) | `/people/v2/note_categories/{note_category_id}/shares` | A note category share defines who can view notes in a category. |
| [NoteCategorySubscription](#note_category_subscription) | `/people/v2/note_category_subscriptions` | A subscription for note categories |
| [Organization](#organization) | `/people/v2` | The organization represents a single church. Every other resource is scoped to this record. |
| [OrganizationStatistics](#organization_statistics) | `/people/v2/organization_statistics` | Returns statistics for the organization. |
| [PeopleImport](#people_import) | `/people/v2/people_imports` | A PeopleImport is a record of an ongoing or previous import from a CSV file. |
| [PeopleImportConflict](#people_import_conflict) | `/people/v2/people_imports/{people_import_id}/conflicts` | A PeopleImportConflict is a record of change that will occur if the parent PeopleImport is completed. |
| [PeopleImportHistory](#people_import_history) | `/people/v2/people_imports/{people_import_id}/histories` | A PeopleImportHistory is a record of change that occurred when the parent PeopleImport was completed. |
| [Person](#person) | `/people/v2/people` | A person record represents a single member/user of the application. Each person has different permissions that determine how the user can use this app (if at all). |
| [PersonApp](#person_app) | `/people/v2/people/{person_id}/person_apps` | A Person App is the relationship between a Person and an App. |
| [PersonMerger](#person_merger) | `/people/v2/person_mergers` | A Person Merger is the history of profiles that were merged into other profiles. |
| [PhoneNumber](#phone_number) | `/people/v2/phone_numbers` | A phone number represents a single telephone number and location. |
| [PlatformNotification](#platform_notification) | `/people/v2/people/{person_id}/platform_notifications` | A Platform Notification is a suite-wide notification that shows at the top of each application's screen until dismissed by the user. |
| [Report](#report) | `/people/v2/reports` | A report is editable liquid syntax that provides a powerful tool for presenting your Lists however you want. |
| [Rule](#rule) | `/people/v2/lists/{list_id}/rules` | A rule belongs to a List and groups conditions together. |
| [SchoolOption](#school_option) | `/people/v2/school_options` | A school option represents a school name, school type, grades, etc. and can be selected for a person. |
| [ServiceTime](#service_time) | `/people/v2/campuses/{campus_id}/service_times` | A ServiceTime Resource |
| [SocialProfile](#social_profile) | `/people/v2/social_profiles` | A social profile represents a members's Twitter, Facebook, or other social media account. |
| [SpamEmailAddress](#spam_email_address) | `/people/v2/spam_email_addresses` | An email address that is marked as spam |
| [Tab](#tab) | `/people/v2/tabs` | A tab is a custom tab and groups like field definitions. |
| [Workflow](#workflow) | `/people/v2/workflows` | A Workflow |
| [WorkflowAssigneeSummary](#workflow_assignee_summary) | `/people/v2/workflow_assignee_summaries` | Aggregated card counts for an assignee across all visible workflows |
| [WorkflowAssigneeWorkflowSummary](#workflow_assignee_workflow_summary) | `/people/v2/people/{person_id}/workflow_assignee_workflow_summaries` | Per-workflow card counts for a specific assignee |
| [WorkflowCard](#workflow_card) | `/people/v2/people/{person_id}/workflow_cards` | A Card |
| [WorkflowCardActivity](#workflow_card_activity) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/activities` | Workflow Card Activity is a record of an action performed on a card |
| [WorkflowCardNote](#workflow_card_note) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/notes` | Workflow Note is a note that has been made on a Workflow Card |
| [WorkflowCategory](#workflow_category) | `/people/v2/workflows/{workflow_id}/category` | A Workflow Category |
| [WorkflowShare](#workflow_share) | `/people/v2/people/{person_id}/workflow_shares` | A workflow share defines who can access a workflow. |
| [WorkflowStep](#workflow_step) | `/people/v2/workflows/{workflow_id}/steps` | A Step |
| [WorkflowStepAssigneeSummary](#workflow_step_assignee_summary) | `/people/v2/workflows/{workflow_id}/steps/{step_id}/assignee_summaries` | The ready and snoozed count for an assignee & step |

<a id="address"></a>
## Address

An address represents a physical and/or mailing address for a person.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/addresses` | List |
| GET | `/people/v2/addresses/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/addresses` | Create |
| PATCH | `/people/v2/addresses/{id}` | Update |
| DELETE | `/people/v2/addresses/{id}` | Delete |

Attributes that can be set on create: `city`, `state`, `zip`, `country_code`, `location`, `primary`, `street_line_1`, `street_line_2`

Attributes that can be set on update: `city`, `state`, `zip`, `country_code`, `location`, `primary`, `street_line_1`, `street_line_2`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `city` | string | `"string"` |  |
| `country_code` | string | `"string"` |  |
| `country_name` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `location` | string | `"string"` |  |
| `primary` | boolean | `true` |  |
| `state` | string | `"string"` |  |
| `street_line_1` | string | `"string"` |  |
| `street_line_2` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `zip` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `city`, `country_code`, `created_at`, `location`, `primary`, `state`, `street_line_1`, `street_line_2`, `updated_at`, `zip`
- Query with `where[...]`:
  - `where[city]` (string), for example `?where[city]=string`
  - `where[country_code]` (string), for example `?where[country_code]=string`
  - `where[location]` (string), for example `?where[location]=string`
  - `where[primary]` (boolean), for example `?where[primary]=true`
  - `where[state]` (string), for example `?where[state]=string`
  - `where[street_line_1]` (string), for example `?where[street_line_1]=string`
  - `where[street_line_2]` (string), for example `?where[street_line_2]=string`
  - `where[zip]` (string), for example `?where[zip]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `addresses`: `/people/v2/addresses`
- [Person](#person) as `addresses`: `/people/v2/people/{person_id}/addresses`

**Example record**

```json
{
  "type": "Address",
  "id": "1",
  "attributes": {
    "city": "string",
    "country_code": "string",
    "country_name": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "location": "string",
    "primary": true,
    "state": "string",
    "street_line_1": "string",
    "street_line_2": "string",
    "updated_at": "2000-01-01T12:00:00Z",
    "zip": "string"
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="app"></a>
## App

An app is one of the handful of apps that Planning Center offers that organizations can subscribe to, e.g. Services, Registrations, etc.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/apps` | List |
| GET | `/people/v2/apps/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `url` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `name`, `url`
- Query with `where[...]`:
  - `where[name]` (string), for example `?where[name]=string`
  - `where[url]` (string), for example `?where[url]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [MessageGroup](#message_group) as `app`: `/people/v2/message_groups/{message_group_id}/app`
- [Organization](#organization) as `apps`: `/people/v2/apps`
- [PersonApp](#person_app) as `app`: `/people/v2/people/{person_id}/person_apps/{person_app_id}/app`
- [Person](#person) as `apps`: `/people/v2/people/{person_id}/apps`

**Example record**

```json
{
  "type": "App",
  "id": "1",
  "attributes": {
    "name": "string",
    "url": "string"
  },
  "relationships": {}
}
```

<a id="background_check"></a>
## BackgroundCheck

Background Checks for a Person

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/background_checks` | List |
| GET | `/people/v2/background_checks/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/background_checks` | Create |
| PATCH | `/people/v2/background_checks/{id}` | Update |
| DELETE | `/people/v2/background_checks/{id}` | Delete |

Attributes that can be set on create: `note`, `status`, `report_url`, `expires_on`, `completed_at`

Attributes that can be set on update: `note`, `status`, `report_url`, `expires_on`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `completed_at` | date_time | `"2000-01-01T12:00:00Z"` | Returns `null` until background check reaches a completed status. When creating completed background checks, leave blank to use current date and time. |
| `current` | boolean | `true` |  |
| `expires_on` | date | `"2000-01-01"` | Leave blank to use organization's expiration policy |
| `id` | primary_key | `"primary_key"` |  |
| `note` | string | `"string"` |  |
| `report_url` | string | `"string"` | Must be an absolute, percent-encoded `http` or `https` URL. This URL should require authentication |
| `status` | string | `"string"` | Possible values: `awaiting_applicant`, `expired_invitation`, `report_processing`, `needs_review`, `complete_clear`, `complete_not_clear`, `canceled` |
| `status_updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |
| `created_by` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `created_by`, `person`
- `order` (prefix with `-` to reverse): `expires_on`, `status_updated_at`
- Query with `where[...]`:
  - `where[expires_on]` (date), for example `?where[expires_on]=2000-01-01`
  - `where[status_updated_at]` (date_time), for example `?where[status_updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `created_by` | [Person](#person) | `/people/v2/background_checks/{background_check_id}/created_by` |  |
| `person` | [Person](#person) | `/people/v2/background_checks/{background_check_id}/person` |  |

**Reached from**

- [Organization](#organization) as `background_checks`: `/people/v2/background_checks`
- [Person](#person) as `background_checks`: `/people/v2/people/{person_id}/background_checks`

**Example record**

```json
{
  "type": "BackgroundCheck",
  "id": "1",
  "attributes": {
    "completed_at": "2000-01-01T12:00:00Z",
    "current": true,
    "expires_on": "2000-01-01",
    "note": "string",
    "report_url": "string",
    "status": "string",
    "status_updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "created_by": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="birthday_people"></a>
## BirthdayPeople

Returns upcoming birthdays for the organization.

Note: This endpoint will always only return the first 15 people having a birthday in the next 30 days, ordered by birthday, ascending.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/birthday_people` | List |

Collection only: there is no single-record endpoint.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |

**Reached from**

- [Organization](#organization) as `birthday_people`: `/people/v2/birthday_people`

**Example record**

```json
{
  "type": "BirthdayPeople",
  "id": "1",
  "attributes": {},
  "relationships": {}
}
```

<a id="campus"></a>
## Campus

A Campus is a location belonging to an Organization

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/campuses` | List |
| GET | `/people/v2/campuses/{id}` | Fetch one |
| POST | `/people/v2/campuses` | Create |
| PATCH | `/people/v2/campuses/{id}` | Update |
| DELETE | `/people/v2/campuses/{id}` | Delete |

Attributes that can be set on create: `latitude`, `longitude`, `description`, `street`, `city`, `state`, `zip`, `country`, `phone_number`, `website`, `twenty_four_hour_time`, `date_format`, `church_center_enabled`, `contact_email_address`, `time_zone`, `geolocation_set_manually`, `name`

Attributes that can be set on update: `latitude`, `longitude`, `description`, `street`, `city`, `state`, `zip`, `country`, `phone_number`, `website`, `twenty_four_hour_time`, `date_format`, `church_center_enabled`, `contact_email_address`, `time_zone`, `geolocation_set_manually`, `name`

Permission to create: Must be an Organization Administrator

Permission to update: Must be an Organization Administrator

Permission to destroy: Must be an Organization Administrator

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `church_center_enabled` | boolean | `true` |  |
| `city` | string | `"string"` |  |
| `contact_email_address` | string | `"string"` |  |
| `country` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `date_format` | integer | `1` |  |
| `description` | string | `"string"` |  |
| `geolocation_set_manually` | boolean | `true` |  |
| `id` | primary_key | `"primary_key"` |  |
| `latitude` | string | `"string"` |  |
| `longitude` | string | `"string"` |  |
| `name` | string | `"string"` |  |
| `phone_number` | string | `"string"` |  |
| `state` | string | `"string"` |  |
| `street` | string | `"string"` |  |
| `time_zone` | string | `"string"` |  |
| `time_zone_raw` | string | `"string"` | Only available when requested with the `?fields` param |
| `twenty_four_hour_time` | boolean | `true` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `website` | string | `"string"` |  |
| `zip` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `organization` | to_one | [Organization](#organization) |  |

**URL parameters**

- `include`: `lists`, `service_times`
- `order` (prefix with `-` to reverse): `created_at`, `name`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[id]` (primary_key), for example `?where[id]=primary_key`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `lists` | [List](#list) | `/people/v2/campuses/{campus_id}/lists` |  |
| `service_times` | [ServiceTime](#service_time) | `/people/v2/campuses/{campus_id}/service_times` |  |

**Reached from**

- [Form](#form) as `campus`: `/people/v2/forms/{form_id}/campus`
- [List](#list) as `campus`: `/people/v2/lists/{list_id}/campus`
- [Organization](#organization) as `campuses`: `/people/v2/campuses`
- [Person](#person) as `primary_campus`: `/people/v2/people/{person_id}/primary_campus`

**Example record**

```json
{
  "type": "Campus",
  "id": "1",
  "attributes": {
    "church_center_enabled": true,
    "city": "string",
    "contact_email_address": "string",
    "country": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "date_format": 1,
    "description": "string",
    "geolocation_set_manually": true,
    "latitude": "string",
    "longitude": "string",
    "name": "string",
    "phone_number": "string",
    "state": "string",
    "street": "string",
    "time_zone": "string",
    "time_zone_raw": "string",
    "twenty_four_hour_time": true,
    "updated_at": "2000-01-01T12:00:00Z",
    "website": "string",
    "zip": "string"
  },
  "relationships": {
    "organization": {
      "data": {
        "type": "Organization",
        "id": "1"
      }
    }
  }
}
```

<a id="carrier"></a>
## Carrier

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/carriers` | List |

Collection only: there is no single-record endpoint.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `international` | boolean | `true` |  |
| `name` | string | `"string"` |  |
| `value` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `international`, `name`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `carriers`: `/people/v2/carriers`

**Example record**

```json
{
  "type": "Carrier",
  "id": "1",
  "attributes": {
    "international": true,
    "name": "string",
    "value": "string"
  },
  "relationships": {}
}
```

<a id="condition"></a>
## Condition

A condition is an individual criterion used by a List Rule.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/lists/{list_id}/rules/{rule_id}/conditions` | List |
| GET | `/people/v2/lists/{list_id}/rules/{rule_id}/conditions/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `application` | string | `"string"` |  |
| `comparison` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `definition_class` | string | `"string"` |  |
| `definition_identifier` | string | `"string"` |  |
| `description` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `settings` | json | `{}` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `created_by` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `created_by`
- `order` (prefix with `-` to reverse): `application`, `comparison`, `created_at`, `definition_class`, `definition_identifier`, `description`, `settings`, `updated_at`
- Query with `where[...]`:
  - `where[application]` (string), for example `?where[application]=string`
  - `where[comparison]` (string), for example `?where[comparison]=string`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[definition_class]` (string), for example `?where[definition_class]=string`
  - `where[definition_identifier]` (string), for example `?where[definition_identifier]=string`
  - `where[description]` (string), for example `?where[description]=string`
  - `where[settings]` (json), for example `?where[settings]={}`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `created_by` | [Person](#person) | `/people/v2/lists/{list_id}/rules/{rule_id}/conditions/{condition_id}/created_by` |  |

**Reached from**

- [Rule](#rule) as `conditions`: `/people/v2/lists/{list_id}/rules/{rule_id}/conditions`

**Example record**

```json
{
  "type": "Condition",
  "id": "1",
  "attributes": {
    "application": "string",
    "comparison": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "definition_class": "string",
    "definition_identifier": "string",
    "description": "string",
    "settings": {},
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "created_by": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="connected_person"></a>
## ConnectedPerson

A Connected Person is an account from a different organization linked to an account in this organization.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/connected_people` | List |
| GET | `/people/v2/people/{person_id}/connected_people/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `first_name` | string | `"string"` |  |
| `gender` | string | `"string"` |  |
| `given_name` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `last_name` | string | `"string"` |  |
| `middle_name` | string | `"string"` |  |
| `nickname` | string | `"string"` |  |
| `organization_id` | primary_key | `"primary_key"` |  |
| `organization_name` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `organization` | to_one | [Organization](#organization) |  |

**Reached from**

- [Person](#person) as `connected_people`: `/people/v2/people/{person_id}/connected_people`

**Example record**

```json
{
  "type": "ConnectedPerson",
  "id": "1",
  "attributes": {
    "first_name": "string",
    "gender": "string",
    "given_name": "string",
    "last_name": "string",
    "middle_name": "string",
    "nickname": "string",
    "organization_id": "primary_key",
    "organization_name": "string"
  },
  "relationships": {
    "organization": {
      "data": {
        "type": "Organization",
        "id": "1"
      }
    }
  }
}
```

<a id="custom_sender"></a>
## CustomSender

A custom sender that can be used when sending emails.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2` | List |
| GET | `/people/v2/{id}` | Fetch one |
| POST | `/people/v2` | Action: Begins the verification process when a custom sender's email address is created or updated. |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `email_address` | string | `"string"` |  |
| `expired` | boolean | `true` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `verification_requested_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `verification_status` | string | `"string"` |  |
| `verified` | boolean | `true` |  |
| `verified_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `custom_sender_shares` | to_one | CustomSenderShare |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`, `name`, `updated_at`, `verification_requested_at`, `verified_at`
- `per_page` (1 to 100, default 25) and `offset`

**Example record**

```json
{
  "type": "CustomSender",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "email_address": "string",
    "expired": true,
    "name": "string",
    "updated_at": "2000-01-01T12:00:00Z",
    "verification_requested_at": "2000-01-01T12:00:00Z",
    "verification_status": "string",
    "verified": true,
    "verified_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "custom_sender_shares": {
      "data": {
        "type": "CustomSenderShare",
        "id": "1"
      }
    }
  }
}
```

<a id="email"></a>
## Email

An email represents an email address and location.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/emails` | List |
| GET | `/people/v2/emails/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/emails` | Create |
| PATCH | `/people/v2/emails/{id}` | Update |
| DELETE | `/people/v2/emails/{id}` | Delete |

Attributes that can be set on create: `address`, `location`, `primary`

Attributes that can be set on update: `address`, `location`, `primary`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `address` | string | `"string"` |  |
| `blocked` | boolean | `true` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `location` | string | `"string"` |  |
| `primary` | boolean | `true` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `verified` | boolean | `true` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `address`, `created_at`, `location`, `primary`, `updated_at`
- Query with `where[...]`:
  - `where[address]` (string), for example `?where[address]=string`
  - `where[blocked]` (boolean), for example `?where[blocked]=true`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[location]` (string), for example `?where[location]=string`
  - `where[primary]` (boolean), for example `?where[primary]=true`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/emails/{email_id}/person` |  |

**Reached from**

- [Organization](#organization) as `emails`: `/people/v2/emails`
- [Person](#person) as `emails`: `/people/v2/people/{person_id}/emails`

**Example record**

```json
{
  "type": "Email",
  "id": "1",
  "attributes": {
    "address": "string",
    "blocked": true,
    "created_at": "2000-01-01T12:00:00Z",
    "location": "string",
    "primary": true,
    "updated_at": "2000-01-01T12:00:00Z",
    "verified": true
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="field_datum"></a>
## FieldDatum

A field datum is an individual piece of data for a custom field.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/field_data` | List |
| GET | `/people/v2/field_data/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/field_data` | Create |
| PATCH | `/people/v2/field_data/{id}` | Update |
| DELETE | `/people/v2/field_data/{id}` | Delete |

Attributes that can be set on create: `value`, `field_definition_id`

Attributes that can be set on update: `value`, `field_definition_id`

Permission to update: Given a person ID and a field definition ID with a data type of 'checkboxes', in order to 'check' the box for that profile, you want to do a POST request to `/people/v2/people/<person_id>/field_data`. The payload must contain the `field_definition_id` and the string `value`. ``` { "data": { "attributes": { "field_definition_id": <field_definition_id>, "value": "Financial/Bills" } } } ``` Note that if you POST with a value that does not already correspond to an existing field_option value for that field_definition, then the API will create a new one with that value. You can GET all the existing checkbox values at `/people/v2/field_definitions/<field_definition_id>/field_options`. To 'uncheck' the value of the checkbox, you can issue a DELETE to `/people/v2/people/<person_id>/field_data/<field_datum_id>`.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `file` | json | `{}` |  |
| `file_content_type` | string | `"string"` |  |
| `file_name` | string | `"string"` |  |
| `file_size` | integer | `1` |  |
| `id` | primary_key | `"primary_key"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `value` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `field_definition` | to_one | [FieldDefinition](#field_definition) |  |
| `customizable` | to_one | Customizable | Currently, the only customizable relationship is with `Person`. |

**URL parameters**

- `include`: `field_definition`, `field_option`, `tab`
- `order` (prefix with `-` to reverse): `created_at`, `file`, `file_content_type`, `file_name`, `file_size`, `updated_at`, `value`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[field_definition_id]` (integer), for example `?where[field_definition_id]=1`
  - `where[file]` (json), for example `?where[file]={}`
  - `where[file_content_type]` (string), for example `?where[file_content_type]=string`
  - `where[file_name]` (string), for example `?where[file_name]=string`
  - `where[file_size]` (integer), for example `?where[file_size]=1`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `field_definition` | [FieldDefinition](#field_definition) | `/people/v2/field_data/{field_datum_id}/field_definition` |  |
| `field_option` | [FieldOption](#field_option) | `/people/v2/field_data/{field_datum_id}/field_option` |  |
| `person` | [Person](#person) | `/people/v2/field_data/{field_datum_id}/person` |  |
| `tab` | [Tab](#tab) | `/people/v2/field_data/{field_datum_id}/tab` |  |

**Reached from**

- [Organization](#organization) as `field_data`: `/people/v2/field_data`
- [Person](#person) as `field_data`: `/people/v2/people/{person_id}/field_data`

**Example record**

```json
{
  "type": "FieldDatum",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "file": {},
    "file_content_type": "string",
    "file_name": "string",
    "file_size": 1,
    "updated_at": "2000-01-01T12:00:00Z",
    "value": "string"
  },
  "relationships": {
    "field_definition": {
      "data": {
        "type": "FieldDefinition",
        "id": "1"
      }
    },
    "customizable": {
      "data": {
        "type": "Customizable",
        "id": "1"
      }
    }
  }
}
```

<a id="field_definition"></a>
## FieldDefinition

A field definition represents a custom field -- its name, data type, etc.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/field_definitions` | List |
| GET | `/people/v2/field_definitions/{id}` | Fetch one |
| POST | `/people/v2/tabs/{tab_id}/field_definitions` | Create |
| PATCH | `/people/v2/field_definitions/{id}` | Update |
| DELETE | `/people/v2/field_definitions/{id}` | Delete |

Attributes that can be set on create: `data_type`, `name`, `sequence`, `slug`, `deleted_at`, `config`

Attributes that can be set on update: `data_type`, `name`, `sequence`, `slug`, `deleted_at`, `config`

Permission to create: If you want to create a `number` type field, and you want to set a minimum or maximum value, you can pass a hash into the `config` attribute with the `min` and/or `max` keys: ``` "data": { "attributes": { "data_type": "number", "name": "My Number Field", "config": { "min": "0", "max": "100" } } } ``` This example will create a number field that requires its value to be between 0 and 100.

Permission to update: Similarly to creating a `number` type field definition, you can update the `config` object of a `number` field definition to set a minimum or maximum value. ``` "data": { "attributes": { "config": { "min": "0", "max": "100" } } } ``` This will update the field definition to require its value to be between 0 and 100.

Permission to destroy: Deleting a field definition internally sets its `deleted_at` attribute to the current time.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `config` | json | `{}` |  |
| `data_type` | string | `"string"` |  |
| `deleted_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `sequence` | integer | `1` |  |
| `slug` | string | `"string"` |  |
| `tab_id` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `tab` | to_one | [Tab](#tab) |  |

**URL parameters**

- `include`: `field_options`, `tab`
- `order` (prefix with `-` to reverse): `config`, `data_type`, `deleted_at`, `name`, `sequence`, `slug`, `tab_id`
- Query with `where[...]`:
  - `where[config]` (json), for example `?where[config]={}`
  - `where[data_type]` (string), for example `?where[data_type]=string`
  - `where[deleted_at]` (date_time), for example `?where[deleted_at]=2000-01-01T12:00:00Z`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[sequence]` (integer), for example `?where[sequence]=1`
  - `where[slug]` (string), for example `?where[slug]=string`
  - `where[tab_id]` (integer), for example `?where[tab_id]=1`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `field_options` | [FieldOption](#field_option) | `/people/v2/field_definitions/{field_definition_id}/field_options` |  |
| `tab` | [Tab](#tab) | `/people/v2/field_definitions/{field_definition_id}/tab` |  |

**Reached from**

- [FieldDatum](#field_datum) as `field_definition`: `/people/v2/field_data/{field_datum_id}/field_definition`
- [Organization](#organization) as `field_definitions`: `/people/v2/field_definitions`
- [Tab](#tab) as `field_definitions`: `/people/v2/tabs/{tab_id}/field_definitions`

**Example record**

```json
{
  "type": "FieldDefinition",
  "id": "1",
  "attributes": {
    "config": {},
    "data_type": "string",
    "deleted_at": "2000-01-01T12:00:00Z",
    "name": "string",
    "sequence": 1,
    "slug": "string",
    "tab_id": 1
  },
  "relationships": {
    "tab": {
      "data": {
        "type": "Tab",
        "id": "1"
      }
    }
  }
}
```

<a id="field_option"></a>
## FieldOption

A field option represents an individual option for a custom field of type "select" or "checkboxes".

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/field_definitions/{field_definition_id}/field_options` | List |
| GET | `/people/v2/field_definitions/{field_definition_id}/field_options/{id}` | Fetch one |
| POST | `/people/v2/field_definitions/{field_definition_id}/field_options` | Create |
| PATCH | `/people/v2/field_definitions/{field_definition_id}/field_options/{id}` | Update |
| DELETE | `/people/v2/field_definitions/{field_definition_id}/field_options/{id}` | Delete |

Attributes that can be set on create: `value`, `sequence`

Attributes that can be set on update: `value`, `sequence`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `sequence` | integer | `1` |  |
| `value` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `field_definition` | to_one | [FieldDefinition](#field_definition) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `sequence`, `value`
- Query with `where[...]`:
  - `where[sequence]` (integer), for example `?where[sequence]=1`
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [FieldDatum](#field_datum) as `field_option`: `/people/v2/field_data/{field_datum_id}/field_option`
- [FieldDefinition](#field_definition) as `field_options`: `/people/v2/field_definitions/{field_definition_id}/field_options`
- [Tab](#tab) as `field_options`: `/people/v2/tabs/{tab_id}/field_options`

**Example record**

```json
{
  "type": "FieldOption",
  "id": "1",
  "attributes": {
    "sequence": 1,
    "value": "string"
  },
  "relationships": {
    "field_definition": {
      "data": {
        "type": "FieldDefinition",
        "id": "1"
      }
    }
  }
}
```

<a id="form"></a>
## Form

A custom form for people to fill out.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/forms` | List |
| GET | `/people/v2/forms/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `active` | boolean | `true` |  |
| `allow_submitting_for_household_members` | boolean | `true` | Only available if the 'Submit for household member' feature is enabled. |
| `archived` | boolean | `true` |  |
| `archived_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `can_manage` | boolean | `true` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `deleted_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `description` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `login_required` | boolean | `true` |  |
| `name` | string | `"string"` |  |
| `public_url` | string | `"string"` |  |
| `recently_viewed` | boolean | `true` |  |
| `send_submission_notification_to_submitter` | boolean | `true` |  |
| `submission_count` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `campus` | to_one | [Campus](#campus) |  |
| `form_category` | to_one | [FormCategory](#form_category) |  |

**URL parameters**

- `include`: `campus`, `category`
- `order` (prefix with `-` to reverse): `active`, `archived_at`, `campus`, `category`, `created_at`, `deleted_at`, `description`, `last_submitted_at`, `name`, `recently_viewed`, `submission_count`, `updated_at`
- Query with `where[...]`:
  - `where[active]` (boolean), for example `?where[active]=true`
  - `where[id]` (primary_key), for example `?where[id]=primary_key`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `campus` | [Campus](#campus) | `/people/v2/forms/{form_id}/campus` |  |
| `category` | [FormCategory](#form_category) | `/people/v2/forms/{form_id}/category` |  |
| `fields` | [FormField](#form_field) | `/people/v2/forms/{form_id}/fields` |  |
| `form_submissions` | [FormSubmission](#form_submission) | `/people/v2/forms/{form_id}/form_submissions` |  |

**Reached from**

- [FormSubmission](#form_submission) as `form`: `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form`
- [Organization](#organization) as `forms`: `/people/v2/forms`

**Example record**

```json
{
  "type": "Form",
  "id": "1",
  "attributes": {
    "active": true,
    "allow_submitting_for_household_members": true,
    "archived": true,
    "archived_at": "2000-01-01T12:00:00Z",
    "can_manage": true,
    "created_at": "2000-01-01T12:00:00Z",
    "deleted_at": "2000-01-01T12:00:00Z",
    "description": "string",
    "login_required": true,
    "name": "string",
    "public_url": "string",
    "recently_viewed": true,
    "send_submission_notification_to_submitter": true,
    "submission_count": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "campus": {
      "data": {
        "type": "Campus",
        "id": "1"
      }
    },
    "form_category": {
      "data": {
        "type": "FormCategory",
        "id": "1"
      }
    }
  }
}
```

<a id="form_category"></a>
## FormCategory

A Form Category

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/form_categories` | List |
| GET | `/people/v2/form_categories/{id}` | Fetch one |
| POST | `/people/v2/form_categories` | Create |
| PATCH | `/people/v2/form_categories/{id}` | Update |
| DELETE | `/people/v2/form_categories/{id}` | Delete |

Attributes that can be set on create: `name`

Attributes that can be set on update: `name`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`, `name`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Form](#form) as `category`: `/people/v2/forms/{form_id}/category`
- [Organization](#organization) as `form_categories`: `/people/v2/form_categories`

**Example record**

```json
{
  "type": "FormCategory",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "name": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="form_field"></a>
## FormField

A field in a custom form.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/forms/{form_id}/fields` | List |
| GET | `/people/v2/forms/{form_id}/fields/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `description` | string | `"string"` |  |
| `field_type` | string | `"value"` | Possible values: `string`, `text`, `checkboxes`, `dropdown`, `date`, `phone_number`, `address`, `birthday`, `gender`, `custom_field`, `note`, `workflow`, `heading`, `number`, `boolean`, `file`, `medical`, `workflow_checkbox`, `workflow_checkboxes`, `workflow_dropdown`, `marital_status`, `anniversary`, `grade`, `primary_campus`, `school`, or `household` |
| `id` | primary_key | `"primary_key"` |  |
| `label` | string | `"string"` |  |
| `required` | boolean | `true` |  |
| `sequence` | integer | `1` |  |
| `settings` | json | `{}` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `form` | to_one | [Form](#form) |  |
| `fieldable` | to_one | Fieldable | Polymorphic. Fieldable can be any of the following: FieldDefinition, NoteCategory, or Workflow. |
| `options` | to_one | [FormFieldOption](#form_field_option) |  |
| `form_field_conditions` | to_many | [FormFieldCondition](#form_field_condition) |  |

**URL parameters**

- `include`: `form_field_conditions`, `options`
- `order` (prefix with `-` to reverse): `created_at`, `sequence`, `updated_at`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `form_field_conditions` | [FormFieldCondition](#form_field_condition) | `/people/v2/forms/{form_id}/fields/{form_field_id}/form_field_conditions` |  |
| `options` | [FormFieldOption](#form_field_option) | `/people/v2/forms/{form_id}/fields/{form_field_id}/options` |  |

**Reached from**

- [Form](#form) as `fields`: `/people/v2/forms/{form_id}/fields`
- [FormSubmission](#form_submission) as `form_fields`: `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form_fields`

**Example record**

```json
{
  "type": "FormField",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "description": "string",
    "field_type": "value",
    "label": "string",
    "required": true,
    "sequence": 1,
    "settings": {},
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "form": {
      "data": {
        "type": "Form",
        "id": "1"
      }
    },
    "fieldable": {
      "data": {
        "type": "Fieldable",
        "id": "1"
      }
    },
    "options": {
      "data": {
        "type": "FormFieldOption",
        "id": "1"
      }
    },
    "form_field_conditions": {
      "data": [
        {
          "type": "FormFieldCondition",
          "id": "1"
        }
      ]
    }
  }
}
```

<a id="form_field_condition"></a>
## FormFieldCondition

A rule that conditionally shows a form field based on the answer given to another (trigger) field.

A condition belongs to the `form_field` it shows, and points at a `trigger_form_field` whose answer determines whether that field is displayed. A form field can have at most one condition. The trigger is matched in one of two ways:

- `trigger_form_field_option` — the condition is met when the answer to the trigger field is this option (used for option-based fields such as dropdowns, gender, campus, etc.).
- `trigger_form_field_option_value` — the condition is met when the answer to the trigger field equals this string (used for boolean custom fields).

When neither is set, the condition is met whenever the trigger field has any answer.

Conditions are created, updated, and destroyed under the form field they belong to (`POST`/`PATCH`/`DELETE .../fields/{id}/form_field_conditions`). The `form_field` and `trigger_form_field` must both belong to the same form, and the `trigger_form_field_option` must belong to the `trigger_form_field`. Writing requires permission to manage the form.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/forms/{form_id}/fields/{field_id}/form_field_conditions` | List |
| GET | `/people/v2/forms/{form_id}/fields/{field_id}/form_field_conditions/{id}` | Fetch one |
| POST | `/people/v2/forms/{form_id}/fields/{form_field_id}/form_field_conditions` | Create |
| PATCH | `/people/v2/forms/{form_id}/fields/{field_id}/form_field_conditions/{id}` | Update |
| DELETE | `/people/v2/forms/{form_id}/fields/{field_id}/form_field_conditions/{id}` | Delete |

Attributes that can be set on create: `form_field_id`, `trigger_form_field_id`, `trigger_form_field_option_id`, `trigger_form_field_option_value`

Attributes that can be set on update: `form_field_id`, `trigger_form_field_id`, `trigger_form_field_option_id`, `trigger_form_field_option_value`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `trigger_form_field_option_value` | string | `"string"` | Set when the condition is triggered by a specific text value (e.g. a boolean custom field) instead of a `trigger_form_field_option`. |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `form_field` | to_one | [FormField](#form_field) | The field that is shown when the condition is met. |
| `trigger_form_field` | to_one | [FormField](#form_field) | The field whose answer is evaluated to determine whether the condition is met. |
| `trigger_form_field_option` | to_one | [FormFieldOption](#form_field_option) | The option of the trigger field that meets the condition, when the trigger is an option rather than a value. |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [FormField](#form_field) as `form_field_conditions`: `/people/v2/forms/{form_id}/fields/{form_field_id}/form_field_conditions`

**Example record**

```json
{
  "type": "FormFieldCondition",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "trigger_form_field_option_value": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "form_field": {
      "data": {
        "type": "FormField",
        "id": "1"
      }
    },
    "trigger_form_field": {
      "data": {
        "type": "FormField",
        "id": "1"
      }
    },
    "trigger_form_field_option": {
      "data": {
        "type": "FormFieldOption",
        "id": "1"
      }
    }
  }
}
```

<a id="form_field_option"></a>
## FormFieldOption

A field option on a custom form field.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/forms/{form_id}/fields/{field_id}/options` | List |
| GET | `/people/v2/forms/{form_id}/fields/{field_id}/options/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `label` | string | `"string"` |  |
| `sequence` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `form_field` | to_one | [FormField](#form_field) |  |
| `optionable` | to_one | Optionable |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`, `sequence`, `updated_at`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [FormField](#form_field) as `options`: `/people/v2/forms/{form_id}/fields/{form_field_id}/options`

**Example record**

```json
{
  "type": "FormFieldOption",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "label": "string",
    "sequence": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "form_field": {
      "data": {
        "type": "FormField",
        "id": "1"
      }
    },
    "optionable": {
      "data": {
        "type": "Optionable",
        "id": "1"
      }
    }
  }
}
```

<a id="form_submission"></a>
## FormSubmission

A form submission represents a single completed entry submitted through a Planning Center form.

### Creating submissions

`POST` is supported so external integrations can record submissions captured outside of Church Center — for example, paper forms run through OCR. Creating a submission requires the access token to belong to a user who (1) has overall People permissions of Editor or above and (2) can manage the target form (a Manager, or a user with a `manage` `FormShare`).

### Identifying the submitter

Provide either `person_id` (an existing person in your organization) or `person_attributes`. When `person_attributes` is supplied, the API matches against existing profiles by first name (or given name or nickname) + last name + email address. If no match is found, a new person is created automatically.

`person_attributes` accepts `first_name`, `last_name`, and `emails_attributes` (an array with at least one `{ "location": "...", "address": "..." }` hash).

### Form values

Pass each answer as an `included` resource of type `FormSubmissionValue`, with `form_field_id` referencing a field on the form. The shape of `value` depends on the field's `field_type`: plain fields take a string, phone and address fields take an object, and file fields take an array of uploaded file references. See the `FormSubmissionValue` vertex for the full list of shapes and for how to upload files.

```
{
  "data": { "attributes": { "person_id": "1" } },
  "included": [
    { "type": "FormSubmissionValue", "attributes": { "form_field_id": "10", "value": "Blue" } },
    { "type": "FormSubmissionValue", "attributes": { "form_field_id": "11", "value": { "number": "555-555-1234", "location": "Mobile" } } },
    { "type": "FormSubmissionValue", "attributes": { "form_field_id": "12", "value": { "location": "Home", "street": "123 Main St", "city": "Carlsbad", "state": "CA", "zip": "92008", "country_code": "US" } } },
    { "type": "FormSubmissionValue", "attributes": { "form_field_id": "13", "value": [{ "fileID": "us4-2b1f6c0e-5d3a-4f7e-9c21-8a6d0e4b7f10", "fileName": "waiver.pdf" }] } }
  ]
}
```

One important note: if the form field has options (for example, a checkboxes or dropdown field), the submitted `value` should be the ID of the selected option, not the option's text.

### Side effects

A submission triggers the same side effects as a Church Center submission: notification emails to the form's subscribers, a confirmation email to the submitter (if the form is configured to send one), workflow card creation for workflow answer fields, and profile updates for contact info, birthdate, gender, household, custom fields, and other profile data.

The access token's user is recorded as the submitter, unless they are the person the submission is for. Profile updates from the submission are attributed to that user in the person's history.

When the resolved person is brand new (created by this submission), profile updates happen immediately and the submission is auto-verified. When the resolved person already exists and the form contains verification-required fields, a verification email is sent first; profile updates apply only after the submitter clicks the verification link — matching the behavior of an unauthenticated Church Center submission.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/forms/{form_id}/form_submissions` | List |
| GET | `/people/v2/forms/{form_id}/form_submissions/{id}` | Fetch one |
| POST | `/people/v2/forms/{form_id}/form_submissions` | Create |

Attributes that can be set on create: `person_id`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `requires_verification` | boolean | `true` | Only available when requested with the `?fields` param True if the form has fields requiring verification (contact info or household) and the submission is for an existing, untrusted person. Use `?fields[FormSubmission]=requires_verification` to opt into this attribute. |
| `selected_options_by_field` | json | `{}` | Only available when requested with the `?fields` param |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `verified` | boolean | `true` | True once the submitter has confirmed any verification-required answers. Auto-set to true when the submission creates a brand-new person; otherwise false until the submitter clicks the verification email. |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |
| `form` | to_one | [Form](#form) |  |

**URL parameters**

- `include`: `form`, `form_fields`, `form_submission_values`, `person`
- `order` (prefix with `-` to reverse): `created_at`, `person.first_name`, `person.last_name`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `form` | [Form](#form) | `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form` |  |
| `form_fields` | [FormField](#form_field) | `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form_fields` |  |
| `form_submission_values` | [FormSubmissionValue](#form_submission_value) | `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form_submission_values` |  |
| `person` | [Person](#person) | `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/person` |  |

**Reached from**

- [Form](#form) as `form_submissions`: `/people/v2/forms/{form_id}/form_submissions`

**Example record**

```json
{
  "type": "FormSubmission",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "requires_verification": true,
    "selected_options_by_field": {},
    "updated_at": "2000-01-01T12:00:00Z",
    "verified": true
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "form": {
      "data": {
        "type": "Form",
        "id": "1"
      }
    }
  }
}
```

<a id="form_submission_value"></a>
## FormSubmissionValue

A single answer to a single field within a form submission.

When creating a `FormSubmission`, supply one `FormSubmissionValue` per answered field as a JSON:API `included` resource. Each must reference a `form_field_id` belonging to the parent form; values referencing fields on other forms are silently discarded. Blank values are also discarded.

`value` is write-only. It is accepted when creating a submission but is never returned when reading one. Read answers back through `display_value` and, for file fields, `attachments`.

### Uploading files

File answers reference files that have already been uploaded to the Planning Center upload service. Upload each file first with a multipart `POST` to `https://upload.planningcenteronline.com/v2/files`, one file per request. The request authenticates like any other Planning Center API request: a personal access token as HTTP Basic, or an OAuth access token as a `Bearer` token. It does not have to be the same credential that later creates the submission.

```
curl -X POST https://upload.planningcenteronline.com/v2/files   -u <application id>:<secret>   -F "file=@waiver.pdf"
```

The response lists the uploaded file under `data`. Use `data[0].id` as `fileID` and `data[0].attributes.name` as `fileName`. `fileID` must be sent exactly as returned, including its datacenter prefix (such as `us4-`).

Uploads are temporary. `data[0].attributes.expires_at` is 60 minutes after the upload, so create the submission soon after uploading. People copies the file into its own storage when the submission is created, and that copy is unaffected by the upload expiring.

A file answer that is malformed, lacks the prefix, or points at an expired or unknown upload fails the request with a `422` validation error on `value` that says which.

```
{
  "data": { "attributes": { "person_id": "1" } },
  "included": [
    {
      "type": "FormSubmissionValue",
      "attributes": {
        "form_field_id": "42",
        "value": [{ "fileID": "us4-2b1f6c0e-5d3a-4f7e-9c21-8a6d0e4b7f10", "fileName": "waiver.pdf" }]
      }
    }
  ]
}
```

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form_submission_values` | List |
| GET | `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form_submission_values/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `attachments` | array | `[]` |  |
| `display_value` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `value` | string | `"string"` | Write-only. Accepted when creating a submission and never returned. The submitted answer. The shape depends on the parent `FormField`'s `field_type`: - **string, text, number, note, medical**: the literal value as a string - **date, birthday, anniversary**: an ISO 8601 date as a string - **boolean**: `"true"` or `"false"` - **gender, marital_status, school, primary_campus**: the option's id as a string (look up via `/people/v2/genders`, `/people/v2/marital_statuses`, `/people/v2/school_options`, `/people/v2/campuses`) - **grade**: the grade number as a string, such as `"3"` - **checkboxes, dropdown, workflow_checkbox, workflow_checkboxes, workflow_dropdown**: the `FormFieldOption` id as a string, not the option's text. A `checkboxes` or `workflow_checkboxes` field takes one `included` `FormSubmissionValue` per selected option. To answer the field's Other option, send `{ "other": "…" }` instead. For the `workflow_*` types, submitting the answer creates a `WorkflowCard` in the linked workflow. - **phone_number**: an object like `{ "number": "555-555-1234", "location": "Mobile" }`. `location` defaults to `"Mobile"` when omitted. - **address**: an object like `{ "location": "Home", "street": "123 Main St", "city": "Carlsbad", "state": "CA", "zip": "92008", "country_code": "US" }`. `location` defaults to `"Home"` when omitted. `street` is a single string, not the `street_line_1`/`street_line_2` pair the `Address` resource uses; put `\n` before a second line. `country_code` is an uppercase ISO 3166-1 alpha-2 code. When the field is required, every property except `location` must be filled in. - **file**: an array of `{ "fileID": "us4-…", "fileName": "waiver.pdf" }` objects. Each object must have exactly those two keys and no others. `fileID` is the full id returned by the upload service, including its datacenter prefix (see "Uploading files" above). Single-file fields accept one object; fields whose `settings.multiple_files` is true accept up to 5. Each file may be at most 10 MB, and common executable and script file types are rejected. The `fileName` key is required, but its value is ignored: the stored file keeps the name it was uploaded with. - **custom_field**: follows the linked `FieldDefinition`'s `data_type` — usually a string, or the file array above when `data_type` is `file`. The example value shown here is a placeholder — the real shape depends on the field's `field_type`. |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `form_field` | to_one | [FormField](#form_field) |  |
| `form_field_option` | to_one | [FormFieldOption](#form_field_option) |  |
| `form_submission` | to_one | [FormSubmission](#form_submission) |  |

**Reached from**

- [FormSubmission](#form_submission) as `form_submission_values`: `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/form_submission_values`

**Example record**

```json
{
  "type": "FormSubmissionValue",
  "id": "1",
  "attributes": {
    "attachments": [],
    "display_value": "string",
    "value": "string"
  },
  "relationships": {
    "form_field": {
      "data": {
        "type": "FormField",
        "id": "1"
      }
    },
    "form_field_option": {
      "data": {
        "type": "FormFieldOption",
        "id": "1"
      }
    },
    "form_submission": {
      "data": {
        "type": "FormSubmission",
        "id": "1"
      }
    }
  }
}
```

<a id="grade"></a>
## Grade

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2` | List |

Collection only: there is no single-record endpoint.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `key` | integer | `1` |  |
| `value` | string | `"string"` |  |

**Example record**

```json
{
  "type": "Grade",
  "id": "1",
  "attributes": {
    "key": 1,
    "value": "string"
  },
  "relationships": {}
}
```

<a id="household"></a>
## Household

A household links people together and can have a primary contact. To add a person to an existing household, use the HouseholdMemberships endpoint.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/households` | List |
| GET | `/people/v2/households/{id}` | Fetch one |
| POST | `/people/v2/households` | Create |
| PATCH | `/people/v2/households/{id}` | Update |
| DELETE | `/people/v2/households/{id}` | Delete |

Attributes that can be set on create: `name`, `member_count`, `avatar`, `primary_contact_id`

Attributes that can be set on update: `name`, `member_count`, `avatar`, `primary_contact_id`

Permission to create: To create a new household, you must specify the primary contact and the people as relationships: `{"data":{"attributes":{"name":"Smith"},"relationships":{"people":{"data":[{"type":"Person","id":"1"},{"type":"Person","id":"2"}]},"primary_contact":{"data":{"type":"Person","id":"1"}}}}}`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `avatar` | string | `"string"` | File UUID (see [File Uploads](https://api.planningcenteronline.com/docs/overview/file-uploads) section) |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `member_count` | integer | `1` |  |
| `name` | string | `"string"` |  |
| `primary_contact_id` | primary_key | `"primary_key"` |  |
| `primary_contact_name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `primary_contact` | to_one | [Person](#person) |  |
| `people` | to_many | [Person](#person) |  |

**URL parameters**

- `include`: `people`
- `order` (prefix with `-` to reverse): `created_at`, `member_count`, `name`, `primary_contact_name`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[member_count]` (integer), for example `?where[member_count]=1`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[primary_contact_name]` (string), for example `?where[primary_contact_name]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `household_memberships` | [HouseholdMembership](#household_membership) | `/people/v2/households/{household_id}/household_memberships` |  |
| `people` | [Person](#person) | `/people/v2/households/{household_id}/people` | `non_pending`, `without_deceased` |

**Reached from**

- [HouseholdMembership](#household_membership) as `household`: `/people/v2/households/{household_id}/household_memberships/{household_membership_id}/household`
- [Organization](#organization) as `households`: `/people/v2/households`
- [PeopleImportHistory](#people_import_history) as `household`: `/people/v2/people_imports/{people_import_id}/histories/{people_import_history_id}/household`
- [Person](#person) as `households`: `/people/v2/people/{person_id}/households`

**Example record**

```json
{
  "type": "Household",
  "id": "1",
  "attributes": {
    "avatar": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "member_count": 1,
    "name": "string",
    "primary_contact_id": "primary_key",
    "primary_contact_name": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "primary_contact": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "people": {
      "data": [
        {
          "type": "Person",
          "id": "1"
        }
      ]
    }
  }
}
```

<a id="household_membership"></a>
## HouseholdMembership

A household membership is the linking record between a household and a person.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/households/{household_id}/household_memberships` | List |
| GET | `/people/v2/households/{household_id}/household_memberships/{id}` | Fetch one |
| POST | `/people/v2/households/{household_id}/household_memberships` | Create |
| PATCH | `/people/v2/households/{household_id}/household_memberships/{id}` | Update |
| DELETE | `/people/v2/households/{household_id}/household_memberships/{id}` | Delete |

Attributes that can be set on create: `person_id`, `pending`, `household_role`

Attributes that can be set on update: `person_id`, `pending`, `household_role`

Permission to create: To add someone to a household, you must specify the person as a relationship: `{"data":{"attributes":{},"relationships":{"person":{"data":{"type":"Person","id":"1"}}}}}`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `household_role` | string | `"string"` | The role of the person within the household. Possible values are: `adult`, `child_or_dependent`, `other_adult`or`parent_guardian`. |
| `id` | primary_key | `"primary_key"` |  |
| `pending` | boolean | `true` | False when a person's membership in a household is unverified. |
| `person_name` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `household`, `person`
- `order` (prefix with `-` to reverse): `pending`, `person_name`
- Query with `where[...]`:
  - `where[pending]` (boolean), for example `?where[pending]=true`
  - `where[person_name]` (string), for example `?where[person_name]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `household` | [Household](#household) | `/people/v2/households/{household_id}/household_memberships/{household_membership_id}/household` |  |
| `person` | [Person](#person) | `/people/v2/households/{household_id}/household_memberships/{household_membership_id}/person` |  |

**Reached from**

- [Household](#household) as `household_memberships`: `/people/v2/households/{household_id}/household_memberships`
- [Person](#person) as `household_memberships`: `/people/v2/people/{person_id}/household_memberships`

**Example record**

```json
{
  "type": "HouseholdMembership",
  "id": "1",
  "attributes": {
    "household_role": "string",
    "pending": true,
    "person_name": "string"
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="inactive_reason"></a>
## InactiveReason

An inactive reason is a small bit of text indicating why a member is no longer active.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/inactive_reasons` | List |
| GET | `/people/v2/inactive_reasons/{id}` | Fetch one |
| POST | `/people/v2/inactive_reasons` | Create |
| PATCH | `/people/v2/inactive_reasons/{id}` | Update |
| DELETE | `/people/v2/inactive_reasons/{id}` | Delete |

Attributes that can be set on create: `value`

Attributes that can be set on update: `value`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `value` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `value`
- Query with `where[...]`:
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `inactive_reasons`: `/people/v2/inactive_reasons`
- [Person](#person) as `inactive_reason`: `/people/v2/people/{person_id}/inactive_reason`

**Example record**

```json
{
  "type": "InactiveReason",
  "id": "1",
  "attributes": {
    "value": "string"
  },
  "relationships": {}
}
```

<a id="list"></a>
## List

A list is a powerful tool for finding and grouping people together using any criteria imaginable.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/lists` | List |
| GET | `/people/v2/lists/{id}` | Fetch one |
| PATCH | `/people/v2/lists/{id}` | Update (flagged as allowed, but no settable attributes are listed) |
| POST | `/people/v2/lists/{list_id}/mailchimp_sync` | Action: Sync a List to Mailchimp. (Mailchimp integration must already be configured for this organization.) |
| POST | `/people/v2/lists/{list_id}/run` | Action: Run a List to update its results. |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `active_automations_count` | integer | `1` |  |
| `auto_generated_name` | boolean | `true` |  |
| `auto_refresh` | boolean | `true` |  |
| `auto_refresh_frequency` | string | `"string"` |  |
| `automations_active` | boolean | `true` |  |
| `automations_count` | integer | `1` |  |
| `batch_completed_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `description` | string | `"string"` |  |
| `has_inactive_results` | boolean | `true` |  |
| `id` | primary_key | `"primary_key"` |  |
| `include_inactive` | boolean | `true` |  |
| `invalid` | boolean | `true` |  |
| `name` | string | `"string"` |  |
| `name_or_description` | string | `"string"` |  |
| `paused_automations_count` | integer | `1` |  |
| `recently_viewed` | boolean | `true` |  |
| `refreshed_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `return_original_if_none` | boolean | `true` |  |
| `returns` | string | `"string"` |  |
| `starred` | boolean | `true` |  |
| `status` | string | `"string"` |  |
| `subset` | string | `"string"` |  |
| `total_people` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `campus`, `category`, `created_by`, `mailchimp_sync_status`, `people`, `rules`, `shares`, `updated_by`
- `order` (prefix with `-` to reverse): `active_automations_count`, `auto_refresh_frequency`, `automations_count`, `batch_completed_at`, `campus_id`, `campuses.name`, `created_at`, `list_categories.name`, `list_category_id`, `name`, `name_or_description`, `refreshed_at`, `starred`, `total_people`, `updated_at`
- Query with `where[...]`:
  - `where[batch_completed_at]` (date_time), for example `?where[batch_completed_at]=2000-01-01T12:00:00Z`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[id]` (primary_key), for example `?where[id]=primary_key`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `campus` | [Campus](#campus) | `/people/v2/lists/{list_id}/campus` |  |
| `category` | [ListCategory](#list_category) | `/people/v2/lists/{list_id}/category` |  |
| `created_by` | [Person](#person) | `/people/v2/lists/{list_id}/created_by` |  |
| `list_results` | [ListResult](#list_result) | `/people/v2/lists/{list_id}/list_results` |  |
| `mailchimp_sync_status` | [MailchimpSyncStatus](#mailchimp_sync_status) | `/people/v2/lists/{list_id}/mailchimp_sync_status` |  |
| `people` | [Person](#person) | `/people/v2/lists/{list_id}/people` |  |
| `rules` | [Rule](#rule) | `/people/v2/lists/{list_id}/rules` |  |
| `shares` | [ListShare](#list_share) | `/people/v2/lists/{list_id}/shares` |  |
| `star` | [ListStar](#list_star) | `/people/v2/lists/{list_id}/star` |  |
| `updated_by` | [Person](#person) | `/people/v2/lists/{list_id}/updated_by` |  |

**Reached from**

- [Campus](#campus) as `lists`: `/people/v2/campuses/{campus_id}/lists`
- [ListCategory](#list_category) as `lists`: `/people/v2/list_categories/{list_category_id}/lists`
- [Organization](#organization) as `lists`: `/people/v2/lists`

**Example record**

```json
{
  "type": "List",
  "id": "1",
  "attributes": {
    "active_automations_count": 1,
    "auto_generated_name": true,
    "auto_refresh": true,
    "auto_refresh_frequency": "string",
    "automations_active": true,
    "automations_count": 1,
    "batch_completed_at": "2000-01-01T12:00:00Z",
    "created_at": "2000-01-01T12:00:00Z",
    "description": "string",
    "has_inactive_results": true,
    "include_inactive": true,
    "invalid": true,
    "name": "string",
    "name_or_description": "string",
    "paused_automations_count": 1,
    "recently_viewed": true,
    "refreshed_at": "2000-01-01T12:00:00Z",
    "return_original_if_none": true,
    "returns": "string",
    "starred": true,
    "status": "string",
    "subset": "string",
    "total_people": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="list_category"></a>
## ListCategory

A List Category

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/list_categories` | List |
| GET | `/people/v2/list_categories/{id}` | Fetch one |
| POST | `/people/v2/list_categories` | Create |
| PATCH | `/people/v2/list_categories/{id}` | Update |
| DELETE | `/people/v2/list_categories/{id}` | Delete |

Attributes that can be set on create: `name`

Attributes that can be set on update: `name`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `organization_id` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `organization` | to_one | [Organization](#organization) |  |

**URL parameters**

- `include`: `lists`
- `order` (prefix with `-` to reverse): `created_at`, `name`, `organization_id`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[organization_id]` (integer), for example `?where[organization_id]=1`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `lists` | [List](#list) | `/people/v2/list_categories/{list_category_id}/lists` |  |

**Reached from**

- [List](#list) as `category`: `/people/v2/lists/{list_id}/category`
- [Organization](#organization) as `list_categories`: `/people/v2/list_categories`

**Example record**

```json
{
  "type": "ListCategory",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "name": "string",
    "organization_id": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "organization": {
      "data": {
        "type": "Organization",
        "id": "1"
      }
    }
  }
}
```

<a id="list_result"></a>
## ListResult

A list result

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/lists/{list_id}/list_results` | List |
| GET | `/people/v2/lists/{list_id}/list_results/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |
| `list` | to_one | [List](#list) |  |

**URL parameters**

- `include`: `person`
- `order` (prefix with `-` to reverse): `created_at`, `updated_at`
- Query with `where[...]`:
  - `where[person_id]` (integer), for example `?where[person_id]=1`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/lists/{list_id}/list_results/{list_result_id}/person` |  |

**Reached from**

- [List](#list) as `list_results`: `/people/v2/lists/{list_id}/list_results`

**Example record**

```json
{
  "type": "ListResult",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "list": {
      "data": {
        "type": "List",
        "id": "1"
      }
    }
  }
}
```

<a id="list_share"></a>
## ListShare

A list share indicates who has access to edit a list.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/lists/{list_id}/shares` | List |
| GET | `/people/v2/lists/{list_id}/shares/{id}` | Fetch one |
| POST | `/people/v2/lists/{list_id}/shares` | Create |
| PATCH | `/people/v2/lists/{list_id}/shares/{id}` | Update |
| DELETE | `/people/v2/lists/{list_id}/shares/{id}` | Delete |

Attributes that can be set on create: `permission`, `group`, `person_id`

Attributes that can be set on update: `permission`, `group`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `group` | string | `"value"` | Possible values: `No Access`, `Viewer`, `Editor`, or `Manager` |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `permission` | string | `"value"` | Possible values: `view` or `manage` |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `person`
- `order` (prefix with `-` to reverse): `created_at`, `group`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[group]` (string), for example `?where[group]=value`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[permission]` (string), for example `?where[permission]=value`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/lists/{list_id}/shares/{list_share_id}/person` |  |

**Reached from**

- [List](#list) as `shares`: `/people/v2/lists/{list_id}/shares`

**Example record**

```json
{
  "type": "ListShare",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "group": "value",
    "name": "string",
    "permission": "value"
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="list_star"></a>
## ListStar

A starred list for a person indicates it is special in some way

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/lists/{list_id}/star` | List |
| GET | `/people/v2/lists/{list_id}/star/{id}` | Fetch one |
| POST | `/people/v2/lists/{list_id}/star` | Create (flagged as allowed, but no settable attributes are listed) |
| DELETE | `/people/v2/lists/{list_id}/star/{id}` | Delete |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |

**Reached from**

- [List](#list) as `star`: `/people/v2/lists/{list_id}/star`

**Example record**

```json
{
  "type": "ListStar",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="mailchimp_sync_status"></a>
## MailchimpSyncStatus

The status of syncing a List with Mailchimp.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/lists/{list_id}/mailchimp_sync_status` | List |
| GET | `/people/v2/lists/{list_id}/mailchimp_sync_status/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `completed_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `error` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `progress` | integer | `1` |  |
| `segment_id` | integer | `1` |  |
| `status` | string | `"string"` |  |

**Reached from**

- [List](#list) as `mailchimp_sync_status`: `/people/v2/lists/{list_id}/mailchimp_sync_status`

**Example record**

```json
{
  "type": "MailchimpSyncStatus",
  "id": "1",
  "attributes": {
    "completed_at": "2000-01-01T12:00:00Z",
    "error": "string",
    "progress": 1,
    "segment_id": 1,
    "status": "string"
  },
  "relationships": {}
}
```

<a id="marital_status"></a>
## MaritalStatus

A martial status represents a member's current status, e.g. married, single, etc.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/marital_statuses` | List |
| GET | `/people/v2/marital_statuses/{id}` | Fetch one |
| POST | `/people/v2/marital_statuses` | Create |
| PATCH | `/people/v2/marital_statuses/{id}` | Update |
| DELETE | `/people/v2/marital_statuses/{id}` | Delete |

Attributes that can be set on create: `value`

Attributes that can be set on update: `value`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `value` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `sequence`, `value`
- Query with `where[...]`:
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `marital_statuses`: `/people/v2/marital_statuses`
- [Person](#person) as `marital_status`: `/people/v2/people/{person_id}/marital_status`

**Example record**

```json
{
  "type": "MaritalStatus",
  "id": "1",
  "attributes": {
    "value": "string"
  },
  "relationships": {}
}
```

<a id="membership_type"></a>
## MembershipType

A profile's membership type, e.g. Visitor, Regular Attender, Member, etc.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/membership_types` | List |
| GET | `/people/v2/membership_types/{id}` | Fetch one |
| POST | `/people/v2/membership_types` | Create |
| PATCH | `/people/v2/membership_types/{id}` | Update |
| DELETE | `/people/v2/membership_types/{id}` | Delete |

Attributes that can be set on create: `value`

Attributes that can be set on update: `value`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `value` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `sequence`, `value`
- Query with `where[...]`:
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `membership_types`: `/people/v2/membership_types`

**Example record**

```json
{
  "type": "MembershipType",
  "id": "1",
  "attributes": {
    "value": "string"
  },
  "relationships": {}
}
```

<a id="message"></a>
## Message

A message is an individual email or sms text sent to a member. Every message has a parent message group.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/messages` | List |
| GET | `/people/v2/messages/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `app_name` | string | `"string"` |  |
| `bounced_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `delivery_status` | string | `"string"` |  |
| `file` | json | `{}` | Visible to: `private`. |
| `from_address` | string | `"string"` |  |
| `from_name` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `kind` | string | `"value"` | Possible values: `email`, `sms`, or `church_center_message` |
| `message_type` | string | `"string"` |  |
| `read_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `reject_reason` | string | `"string"` |  |
| `rejection_notification_sent_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `sent_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `subject` | string | `"string"` |  |
| `to_addresses` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `from` | to_one | [Person](#person) |  |
| `to` | to_one | [Person](#person) |  |
| `message_group` | to_one | [MessageGroup](#message_group) |  |

**URL parameters**

- `include`: `message_group`, `to`
- `order` (prefix with `-` to reverse): `app_name`, `bounced_at`, `created_at`, `delivery_status`, `file`, `from_address`, `from_name`, `kind`, `reject_reason`, `rejection_notification_sent_at`, `sent_at`, `subject`, `to_addresses`
- Query with `where[...]`:
  - `where[app_name]` (string), for example `?where[app_name]=string`
  - `where[bounced_at]` (date_time), for example `?where[bounced_at]=2000-01-01T12:00:00Z`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[delivery_status]` (string), for example `?where[delivery_status]=string`
  - `where[file]` (json), for example `?where[file]={}`
  - `where[from_address]` (string), for example `?where[from_address]=string`
  - `where[kind]` (string), for example `?where[kind]=value`
  - `where[reject_reason]` (string), for example `?where[reject_reason]=string`
  - `where[rejection_notification_sent_at]` (date_time), for example `?where[rejection_notification_sent_at]=2000-01-01T12:00:00Z`
  - `where[sent_at]` (date_time), for example `?where[sent_at]=2000-01-01T12:00:00Z`
  - `where[subject]` (string), for example `?where[subject]=string`
  - `where[to_addresses]` (string), for example `?where[to_addresses]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `message_group` | [MessageGroup](#message_group) | `/people/v2/messages/{message_id}/message_group` |  |
| `to` | [Person](#person) | `/people/v2/messages/{message_id}/to` |  |

**Reached from**

- [MessageGroup](#message_group) as `messages`: `/people/v2/message_groups/{message_group_id}/messages`
- [Organization](#organization) as `messages`: `/people/v2/messages`
- [Person](#person) as `messages`: `/people/v2/people/{person_id}/messages`

**Example record**

```json
{
  "type": "Message",
  "id": "1",
  "attributes": {
    "app_name": "string",
    "bounced_at": "2000-01-01T12:00:00Z",
    "created_at": "2000-01-01T12:00:00Z",
    "delivery_status": "string",
    "file": {},
    "from_address": "string",
    "from_name": "string",
    "kind": "value",
    "message_type": "string",
    "read_at": "2000-01-01T12:00:00Z",
    "reject_reason": "string",
    "rejection_notification_sent_at": "2000-01-01T12:00:00Z",
    "sent_at": "2000-01-01T12:00:00Z",
    "subject": "string",
    "to_addresses": "string"
  },
  "relationships": {
    "from": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "to": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "message_group": {
      "data": {
        "type": "MessageGroup",
        "id": "1"
      }
    }
  }
}
```

<a id="message_group"></a>
## MessageGroup

A message group represents one or more emails or text messages sent from one of the Planning Center apps. The message group indicates the from person, app, etc.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/message_groups` | List |
| GET | `/people/v2/message_groups/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `contains_user_generated_content` | boolean | `true` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `from_address` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `message_count` | integer | `1` |  |
| `message_type` | string | `"string"` |  |
| `reply_to_address` | string | `"string"` |  |
| `reply_to_name` | string | `"string"` |  |
| `subject` | string | `"string"` |  |
| `system_message` | boolean | `true` |  |
| `transactional_message` | boolean | `true` |  |
| `uuid` | string | `"string"` |  |

**URL parameters**

- `include`: `app`, `from`, `messages`
- `order` (prefix with `-` to reverse): `contains_user_generated_content`, `created_at`, `from_address`, `message_count`, `message_type`, `reply_to_address`, `reply_to_name`, `subject`, `system_message`, `transactional_message`, `uuid`
- Query with `where[...]`:
  - `where[contains_user_generated_content]` (boolean), for example `?where[contains_user_generated_content]=true`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[from_address]` (string), for example `?where[from_address]=string`
  - `where[message_count]` (integer), for example `?where[message_count]=1`
  - `where[message_type]` (string), for example `?where[message_type]=string`
  - `where[reply_to_address]` (string), for example `?where[reply_to_address]=string`
  - `where[reply_to_name]` (string), for example `?where[reply_to_name]=string`
  - `where[subject]` (string), for example `?where[subject]=string`
  - `where[system_message]` (boolean), for example `?where[system_message]=true`
  - `where[transactional_message]` (boolean), for example `?where[transactional_message]=true`
  - `where[uuid]` (string), for example `?where[uuid]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `app` | [App](#app) | `/people/v2/message_groups/{message_group_id}/app` |  |
| `from` | [Person](#person) | `/people/v2/message_groups/{message_group_id}/from` |  |
| `messages` | [Message](#message) | `/people/v2/message_groups/{message_group_id}/messages` |  |

**Reached from**

- [Message](#message) as `message_group`: `/people/v2/messages/{message_id}/message_group`
- [Organization](#organization) as `message_groups`: `/people/v2/message_groups`
- [Person](#person) as `message_groups`: `/people/v2/people/{person_id}/message_groups`

**Example record**

```json
{
  "type": "MessageGroup",
  "id": "1",
  "attributes": {
    "contains_user_generated_content": true,
    "created_at": "2000-01-01T12:00:00Z",
    "from_address": "string",
    "message_count": 1,
    "message_type": "string",
    "reply_to_address": "string",
    "reply_to_name": "string",
    "subject": "string",
    "system_message": true,
    "transactional_message": true,
    "uuid": "string"
  },
  "relationships": {}
}
```

<a id="name_prefix"></a>
## NamePrefix

A name prefix is one of Mr., Mrs., etc.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/name_prefixes` | List |
| GET | `/people/v2/name_prefixes/{id}` | Fetch one |
| POST | `/people/v2/name_prefixes` | Create |
| PATCH | `/people/v2/name_prefixes/{id}` | Update |
| DELETE | `/people/v2/name_prefixes/{id}` | Delete |

Attributes that can be set on create: `value`

Attributes that can be set on update: `value`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `value` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `value`
- Query with `where[...]`:
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `name_prefixes`: `/people/v2/name_prefixes`
- [Person](#person) as `name_prefix`: `/people/v2/people/{person_id}/name_prefix`

**Example record**

```json
{
  "type": "NamePrefix",
  "id": "1",
  "attributes": {
    "value": "string"
  },
  "relationships": {}
}
```

<a id="name_suffix"></a>
## NameSuffix

A name suffix is one of Sr., Jr., etc.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/name_suffixes` | List |
| GET | `/people/v2/name_suffixes/{id}` | Fetch one |
| POST | `/people/v2/name_suffixes` | Create |
| PATCH | `/people/v2/name_suffixes/{id}` | Update |
| DELETE | `/people/v2/name_suffixes/{id}` | Delete |

Attributes that can be set on create: `value`

Attributes that can be set on update: `value`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `value` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `value`
- Query with `where[...]`:
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `name_suffixes`: `/people/v2/name_suffixes`
- [Person](#person) as `name_suffix`: `/people/v2/people/{person_id}/name_suffix`

**Example record**

```json
{
  "type": "NameSuffix",
  "id": "1",
  "attributes": {
    "value": "string"
  },
  "relationships": {}
}
```

<a id="note"></a>
## Note

A note is text with a category connected to a person’s profile.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/notes` | List |
| GET | `/people/v2/notes/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/notes` | Create |
| PATCH | `/people/v2/notes/{id}` | Update |
| DELETE | `/people/v2/notes/{id}` | Delete |

Attributes that can be set on create: `note`, `created_at`, `updated_at`, `display_date`, `note_category_id`

Attributes that can be set on update: `note`, `created_at`, `updated_at`, `display_date`, `note_category_id`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `created_by_id` | integer | `1` |  |
| `display_date` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `note` | string | `"string"` |  |
| `note_category_id` | integer | `1` |  |
| `organization_id` | integer | `1` |  |
| `person_id` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `note_category` | to_one | [NoteCategory](#note_category) |  |
| `organization` | to_one | [Organization](#organization) |  |
| `person` | to_one | [Person](#person) |  |
| `created_by` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `category`, `created_by`, `person`
- `order` (prefix with `-` to reverse): `created_at`, `display_date`, `id`, `note`, `note_category_id`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[note]` (string), for example `?where[note]=string`
  - `where[note_category_id]` (integer), for example `?where[note_category_id]=1`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `category` | [NoteCategory](#note_category) | `/people/v2/notes/{note_id}/category` |  |
| `created_by` | [Person](#person) | `/people/v2/notes/{note_id}/created_by` |  |
| `person` | [Person](#person) | `/people/v2/notes/{note_id}/person` |  |

**Reached from**

- [Organization](#organization) as `notes`: `/people/v2/notes`
- [Person](#person) as `notes`: `/people/v2/people/{person_id}/notes`

**Example record**

```json
{
  "type": "Note",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "created_by_id": 1,
    "display_date": "2000-01-01T12:00:00Z",
    "note": "string",
    "note_category_id": 1,
    "organization_id": 1,
    "person_id": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "note_category": {
      "data": {
        "type": "NoteCategory",
        "id": "1"
      }
    },
    "organization": {
      "data": {
        "type": "Organization",
        "id": "1"
      }
    },
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "created_by": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="note_category"></a>
## NoteCategory

A Note Category

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/note_categories` | List |
| GET | `/people/v2/note_categories/{id}` | Fetch one |
| POST | `/people/v2/note_categories` | Create |
| PATCH | `/people/v2/note_categories/{id}` | Update |
| DELETE | `/people/v2/note_categories/{id}` | Delete |

Attributes that can be set on create: `name`

Attributes that can be set on update: `name`

Permission to destroy: Deleting a note category will also delete all associated notes

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `locked` | boolean | `true` |  |
| `name` | string | `"string"` |  |
| `organization_id` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `organization` | to_one | [Organization](#organization) |  |

**URL parameters**

- `include`: `shares`, `subscribers`, `subscriptions`
- `order` (prefix with `-` to reverse): `created_at`, `locked`, `name`, `organization_id`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[locked]` (boolean), for example `?where[locked]=true`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[organization_id]` (integer), for example `?where[organization_id]=1`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `shares` | [NoteCategoryShare](#note_category_share) | `/people/v2/note_categories/{note_category_id}/shares` |  |
| `subscribers` | [Person](#person) | `/people/v2/note_categories/{note_category_id}/subscribers` |  |
| `subscriptions` | [NoteCategorySubscription](#note_category_subscription) | `/people/v2/note_categories/{note_category_id}/subscriptions` |  |

**Reached from**

- [Note](#note) as `category`: `/people/v2/notes/{note_id}/category`
- [Organization](#organization) as `note_categories`: `/people/v2/note_categories`

**Example record**

```json
{
  "type": "NoteCategory",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "locked": true,
    "name": "string",
    "organization_id": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "organization": {
      "data": {
        "type": "Organization",
        "id": "1"
      }
    }
  }
}
```

<a id="note_category_share"></a>
## NoteCategoryShare

A note category share defines who can view notes in a category.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/note_categories/{note_category_id}/shares` | List |
| GET | `/people/v2/note_categories/{note_category_id}/shares/{id}` | Fetch one |
| POST | `/people/v2/note_categories/{note_category_id}/shares` | Create |
| PATCH | `/people/v2/note_categories/{note_category_id}/shares/{id}` | Update |
| DELETE | `/people/v2/note_categories/{note_category_id}/shares/{id}` | Delete |

Attributes that can be set on create: `group`, `permission`, `person_id`

Attributes that can be set on update: `group`, `permission`, `person_id`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `group` | string | `"value"` | Possible values: `No Access`, `Viewer`, `Editor`, or `Manager` |
| `id` | primary_key | `"primary_key"` |  |
| `permission` | string | `"value"` | Possible values: `view`, `view_create`, or `manage` |
| `person_id` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |
| `note_category` | to_one | [NoteCategory](#note_category) |  |

**URL parameters**

- `include`: `person`
- Query with `where[...]`:
  - `where[permission]` (string), for example `?where[permission]=value`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/note_categories/{note_category_id}/shares/{note_category_share_id}/person` |  |

**Reached from**

- [NoteCategory](#note_category) as `shares`: `/people/v2/note_categories/{note_category_id}/shares`

**Example record**

```json
{
  "type": "NoteCategoryShare",
  "id": "1",
  "attributes": {
    "group": "value",
    "permission": "value",
    "person_id": 1
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "note_category": {
      "data": {
        "type": "NoteCategory",
        "id": "1"
      }
    }
  }
}
```

<a id="note_category_subscription"></a>
## NoteCategorySubscription

A subscription for note categories

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/note_category_subscriptions` | List |
| GET | `/people/v2/note_category_subscriptions/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |
| `note_category` | to_one | [NoteCategory](#note_category) |  |

**URL parameters**

- `include`: `person`
- `order` (prefix with `-` to reverse): `created_at`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/note_category_subscriptions/{note_category_subscription_id}/person` |  |

**Reached from**

- [NoteCategory](#note_category) as `subscriptions`: `/people/v2/note_categories/{note_category_id}/subscriptions`
- [Organization](#organization) as `note_category_subscriptions`: `/people/v2/note_category_subscriptions`

**Example record**

```json
{
  "type": "NoteCategorySubscription",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "note_category": {
      "data": {
        "type": "NoteCategory",
        "id": "1"
      }
    }
  }
}
```

<a id="organization"></a>
## Organization

The organization represents a single church. Every other resource is scoped to this record.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2` | List |
| GET | `/people/v2/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `avatar_url` | string | `"string"` |  |
| `church_center_subdomain` | string | `"string"` |  |
| `contact_website` | string | `"string"` |  |
| `country_code` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `date_format` | string | `"string"` |  |
| `grades` | array | `[]` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `time_zone` | string | `"string"` |  |

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `addresses` | [Address](#address) | `/people/v2/addresses` |  |
| `apps` | [App](#app) | `/people/v2/apps` |  |
| `background_checks` | [BackgroundCheck](#background_check) | `/people/v2/background_checks` | `awaiting_applicant`, `canceled`, `complete_clear`, `complete_not_clear`, `current`, `expired`, `expired_invitation`, `needs_review`, `not_expired`, `report_processing` |
| `birthday_people` | [BirthdayPeople](#birthday_people) | `/people/v2/birthday_people` |  |
| `campuses` | [Campus](#campus) | `/people/v2/campuses` |  |
| `carriers` | [Carrier](#carrier) | `/people/v2/carriers` |  |
| `emails` | [Email](#email) | `/people/v2/emails` |  |
| `field_data` | [FieldDatum](#field_datum) | `/people/v2/field_data` |  |
| `field_definitions` | [FieldDefinition](#field_definition) | `/people/v2/field_definitions` | `include_deleted` |
| `form_categories` | [FormCategory](#form_category) | `/people/v2/form_categories` |  |
| `forms` | [Form](#form) | `/people/v2/forms` | `archived`, `closed`, `not_archived`, `open`, `recently_deleted`, `recently_viewed`, `unassigned`, `with_recoverable` |
| `households` | [Household](#household) | `/people/v2/households` |  |
| `inactive_reasons` | [InactiveReason](#inactive_reason) | `/people/v2/inactive_reasons` |  |
| `list_categories` | [ListCategory](#list_category) | `/people/v2/list_categories` |  |
| `lists` | [List](#list) | `/people/v2/lists` | `auto_refresh_enabled`, `can_manage`, `has_active_automations`, `mailchimp_connected`, `needs_attention`, `recently_viewed`, `starred`, `unassigned` |
| `marital_statuses` | [MaritalStatus](#marital_status) | `/people/v2/marital_statuses` |  |
| `membership_types` | [MembershipType](#membership_type) | `/people/v2/membership_types` |  |
| `message_groups` | [MessageGroup](#message_group) | `/people/v2/message_groups` |  |
| `messages` | [Message](#message) | `/people/v2/messages` | `created_after` |
| `name_prefixes` | [NamePrefix](#name_prefix) | `/people/v2/name_prefixes` |  |
| `name_suffixes` | [NameSuffix](#name_suffix) | `/people/v2/name_suffixes` |  |
| `note_categories` | [NoteCategory](#note_category) | `/people/v2/note_categories` | `view_creatable` |
| `note_category_subscriptions` | [NoteCategorySubscription](#note_category_subscription) | `/people/v2/note_category_subscriptions` |  |
| `notes` | [Note](#note) | `/people/v2/notes` |  |
| `people` | [Person](#person) | `/people/v2/people` | `admins`, `created_since`, `organization_admins` |
| `people_imports` | [PeopleImport](#people_import) | `/people/v2/people_imports` |  |
| `person_mergers` | [PersonMerger](#person_merger) | `/people/v2/person_mergers` |  |
| `phone_numbers` | [PhoneNumber](#phone_number) | `/people/v2/phone_numbers` |  |
| `reports` | [Report](#report) | `/people/v2/reports` |  |
| `school_options` | [SchoolOption](#school_option) | `/people/v2/school_options` |  |
| `social_profiles` | [SocialProfile](#social_profile) | `/people/v2/social_profiles` |  |
| `spam_email_addresses` | [SpamEmailAddress](#spam_email_address) | `/people/v2/spam_email_addresses` |  |
| `stats` | [OrganizationStatistics](#organization_statistics) | `/people/v2/stats` |  |
| `tabs` | [Tab](#tab) | `/people/v2/tabs` | `with_field_definitions` |
| `workflow_assignee_summaries` | [WorkflowAssigneeSummary](#workflow_assignee_summary) | `/people/v2/workflow_assignee_summaries` |  |
| `workflows` | [Workflow](#workflow) | `/people/v2/workflows` | `archived`, `has_my_cards`, `manage_cards_allowed`, `needs_attention`, `not_archived`, `only_deleted`, `recently_deleted`, `recently_viewed`, `unassigned`, `with_deleted`, `with_recoverable`, `with_steps` |

- `background_checks` filter `current`: filter background checks to only those considered "current"
- `field_definitions` filter `include_deleted`: By default, deleted fields are not included. Pass filter=include_deleted to include them.
- `people` filter `created_since`: filter people created in the last 24 hours; pass an additional `time` parameter in ISO 8601 format to specify your own timeframe

**Reached from**

- [Person](#person) as `organization`: `/people/v2/people/{person_id}/organization`

**Example record**

```json
{
  "type": "Organization",
  "id": "1",
  "attributes": {
    "avatar_url": "string",
    "church_center_subdomain": "string",
    "contact_website": "string",
    "country_code": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "date_format": "string",
    "grades": [],
    "name": "string",
    "time_zone": "string"
  },
  "relationships": {}
}
```

<a id="organization_statistics"></a>
## OrganizationStatistics

Returns statistics for the organization.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/organization_statistics` | List |

Collection only: there is no single-record endpoint.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |

**Reached from**

- [Organization](#organization) as `stats`: `/people/v2/stats`

**Example record**

```json
{
  "type": "OrganizationStatistics",
  "id": "1",
  "attributes": {},
  "relationships": {}
}
```

<a id="people_import"></a>
## PeopleImport

A PeopleImport is a record of an ongoing or previous import from a CSV file.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people_imports` | List |
| GET | `/people/v2/people_imports/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `attribs` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `processed_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `status` | string | `"value"` | Possible values: `matching`, `processing_preview`, `previewing`, `processing_import`, `complete`, `undone`, or `undoing` |
| `undone_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `created_by` | to_one | [Person](#person) |  |
| `undone_by` | to_one | [Person](#person) |  |

**URL parameters**

- Query with `where[...]`:
  - `where[status]` (string), for example `?where[status]=value`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `conflicts` | [PeopleImportConflict](#people_import_conflict) | `/people/v2/people_imports/{people_import_id}/conflicts` | `creates`, `creates_and_updates`, `errors`, `household_creates`, `household_updates`, `identical`, `ignored`, `not_ignored`, `updates` |
| `histories` | [PeopleImportHistory](#people_import_history) | `/people/v2/people_imports/{people_import_id}/histories` | `creates`, `household_creates`, `household_updates`, `identical`, `updates` |

**Reached from**

- [Organization](#organization) as `people_imports`: `/people/v2/people_imports`

**Example record**

```json
{
  "type": "PeopleImport",
  "id": "1",
  "attributes": {
    "attribs": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "processed_at": "2000-01-01T12:00:00Z",
    "status": "value",
    "undone_at": "2000-01-01T12:00:00Z",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "created_by": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "undone_by": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="people_import_conflict"></a>
## PeopleImportConflict

A PeopleImportConflict is a record of change that will occur if the parent PeopleImport is completed.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people_imports/{people_import_id}/conflicts` | List |
| GET | `/people/v2/people_imports/{people_import_id}/conflicts/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `conflicting_changes` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `data` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `ignore` | boolean | `true` |  |
| `kind` | string | `"string"` |  |
| `message` | string | `"string"` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- Query with `where[...]`:
  - `where[kind]` (string), for example `?where[kind]=string`
  - `where[name]` (string), for example `?where[name]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [PeopleImport](#people_import) as `conflicts`: `/people/v2/people_imports/{people_import_id}/conflicts`

**Example record**

```json
{
  "type": "PeopleImportConflict",
  "id": "1",
  "attributes": {
    "conflicting_changes": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "data": "string",
    "ignore": true,
    "kind": "string",
    "message": "string",
    "name": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="people_import_history"></a>
## PeopleImportHistory

A PeopleImportHistory is a record of change that occurred when the parent PeopleImport was completed.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people_imports/{people_import_id}/histories` | List |
| GET | `/people/v2/people_imports/{people_import_id}/histories/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `conflicting_changes` | hash | `{}` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `kind` | string | `"string"` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `household`, `person`
- Query with `where[...]`:
  - `where[name]` (string), for example `?where[name]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `household` | [Household](#household) | `/people/v2/people_imports/{people_import_id}/histories/{people_import_history_id}/household` |  |
| `person` | [Person](#person) | `/people/v2/people_imports/{people_import_id}/histories/{people_import_history_id}/person` |  |

**Reached from**

- [PeopleImport](#people_import) as `histories`: `/people/v2/people_imports/{people_import_id}/histories`

**Example record**

```json
{
  "type": "PeopleImportHistory",
  "id": "1",
  "attributes": {
    "conflicting_changes": {},
    "created_at": "2000-01-01T12:00:00Z",
    "kind": "string",
    "name": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="person"></a>
## Person

A person record represents a single member/user of the application. Each person has different permissions that determine how the user can use this app (if at all).

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people` | List |
| GET | `/people/v2/people/{id}` | Fetch one |
| POST | `/people/v2/people` | Create |
| PATCH | `/people/v2/people/{id}` | Update |
| DELETE | `/people/v2/people/{id}` | Delete |

Attributes that can be set on create: `child`, `accounting_administrator`, `anniversary`, `birthdate`, `given_name`, `grade`, `graduation_year`, `middle_name`, `nickname`, `people_permissions`, `site_administrator`, `gender`, `inactivated_at`, `medical_notes`, `membership`, `stripe_customer_identifier`, `stripe_account_identifier`, `created_by_id`, `avatar`, `first_name`, `last_name`, `primary_campus_id`, `remote_id`, `status`

Attributes that can be set on update: `child`, `accounting_administrator`, `anniversary`, `birthdate`, `given_name`, `grade`, `graduation_year`, `middle_name`, `nickname`, `people_permissions`, `site_administrator`, `gender`, `inactivated_at`, `medical_notes`, `membership`, `stripe_customer_identifier`, `stripe_account_identifier`, `avatar`, `first_name`, `last_name`, `primary_campus_id`, `remote_id`, `status`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `accounting_administrator` | boolean | `true` | Visible to: `standard`. |
| `anniversary` | date | `"2000-01-01"` | Visible to: `standard`. |
| `avatar` | string | `"string"` | File UUID (see [File Uploads](https://api.planningcenteronline.com/docs/overview/file-uploads) section) |
| `birthdate` | date | `"2000-01-01"` | Visible to: `standard`. |
| `can_create_forms` | boolean | `true` | Visible to: `standard`. |
| `can_email_lists` | boolean | `true` | Visible to: `standard`. |
| `child` | boolean | `true` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` | Visible to: `standard`. |
| `demographic_avatar_url` | string | `"string"` |  |
| `directory_shared_info` | json | `{}` | Only available when requested with the `?fields` param Visible to: `standard`. |
| `directory_status` | string | `"string"` | Visible to: `standard`. |
| `first_name` | string | `"string"` |  |
| `gender` | string | `"string"` | Visible to: `standard`. |
| `given_name` | string | `"string"` | Visible to: `standard`. |
| `grade` | integer | `1` | Visible to: `standard`. |
| `graduation_year` | integer | `1` | Visible to: `standard`. |
| `id` | primary_key | `"primary_key"` |  |
| `inactivated_at` | date_time | `"2000-01-01T12:00:00Z"` | Set to an ISO 8601 date or time to make the profile inactive. Set to "null" to reactivate the profile. Visible to: `standard`. |
| `last_name` | string | `"string"` |  |
| `login_identifier` | string | `"string"` | Visible to: `permitted_viewer`. |
| `medical_notes` | string | `"string"` | Visible to: `standard`. |
| `membership` | string | `"string"` | Visible to: `standard`. |
| `mfa_configured` | boolean | `true` | Only available when requested with the `?fields` param Set to "true" or "false" to filter. Can only be viewed and queried by an Organization Administrator. Visible to: `organization_administrator`. |
| `middle_name` | string | `"string"` | Visible to: `standard`. |
| `name` | string | `"string"` |  |
| `nickname` | string | `"string"` | Visible to: `standard`. |
| `passed_background_check` | boolean | `true` | Visible to: `standard`. |
| `people_permissions` | string | `"string"` | Visible to: `standard`. |
| `remote_id` | integer | `1` | Visible to: `standard`. |
| `school_type` | string | `"string"` | Visible to: `standard`. |
| `selective_access` | boolean | `true` | Visible to: `standard`. |
| `site_administrator` | boolean | `true` | Visible to: `standard`. |
| `status` | string | `"string"` | Set to "inactive" to set "inactivated_at" to the current time and make the profile inactive. Set to anything else to clear "inactivated_at" and reactivate the profile. |
| `stripe_account_identifier` | string | `"string"` | Only available when requested with the `?fields` param Visible to: `super_user`. |
| `stripe_customer_identifier` | string | `"string"` | Only available when requested with the `?fields` param Visible to: `super_user`. |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` | Visible to: `standard`. |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `primary_campus` | to_one | PrimaryCampus |  |
| `created_by` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `addresses`, `emails`, `field_data`, `households`, `inactive_reason`, `marital_status`, `name_prefix`, `name_suffix`, `organization`, `person_apps`, `phone_numbers`, `platform_notifications`, `primary_campus`, `school`, `social_profiles`
- `order` (prefix with `-` to reverse): `accounting_administrator`, `anniversary`, `birthdate`, `child`, `created_at`, `first_name`, `gender`, `given_name`, `grade`, `graduation_year`, `inactivated_at`, `last_name`, `membership`, `middle_name`, `nickname`, `people_permissions`, `remote_id`, `site_administrator`, `status`, `updated_at`
- Query with `where[...]`:
  - `where[accounting_administrator]` (boolean), for example `?where[accounting_administrator]=true`
  - `where[anniversary]` (date), for example `?where[anniversary]=2000-01-01`
  - `where[birthdate]` (date), for example `?where[birthdate]=2000-01-01`
  - `where[child]` (boolean), for example `?where[child]=true`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[first_name]` (string), for example `?where[first_name]=string`
  - `where[gender]` (string), for example `?where[gender]=string`
  - `where[given_name]` (string), for example `?where[given_name]=string`
  - `where[grade]` (integer), for example `?where[grade]=1`
  - `where[graduation_year]` (integer), for example `?where[graduation_year]=1`
  - `where[id]` (primary_key), for example `?where[id]=primary_key`
  - `where[inactivated_at]` (date_time), for example `?where[inactivated_at]=2000-01-01T12:00:00Z`
  - `where[last_name]` (string), for example `?where[last_name]=string`
  - `where[medical_notes]` (string), for example `?where[medical_notes]=string`
  - `where[membership]` (string), for example `?where[membership]=string`
  - `where[mfa_configured]` (boolean), for example `?where[mfa_configured]=true`
  - `where[middle_name]` (string), for example `?where[middle_name]=string`
  - `where[nickname]` (string), for example `?where[nickname]=string`
  - `where[people_permissions]` (string), for example `?where[people_permissions]=string`
  - `where[primary_campus_id]` (integer), for example `?where[primary_campus_id]=1`
  - `where[remote_id]` (integer), for example `?where[remote_id]=1`
  - `where[search_name]` (string), for example `?where[search_name]=string`
  - `where[search_name_or_email]` (string), for example `?where[search_name_or_email]=string`
  - `where[search_name_or_email_or_phone_number]` (string), for example `?where[search_name_or_email_or_phone_number]=string`
  - `where[search_phone_number]` (string), for example `?where[search_phone_number]=string`
  - `where[search_phone_number_e164]` (string), for example `?where[search_phone_number_e164]=string`
  - `where[site_administrator]` (boolean), for example `?where[site_administrator]=true`
  - `where[status]` (string), for example `?where[status]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `addresses` | [Address](#address) | `/people/v2/people/{person_id}/addresses` |  |
| `apps` | [App](#app) | `/people/v2/people/{person_id}/apps` |  |
| `background_checks` | [BackgroundCheck](#background_check) | `/people/v2/people/{person_id}/background_checks` | `awaiting_applicant`, `canceled`, `complete_clear`, `complete_not_clear`, `current`, `expired`, `expired_invitation`, `needs_review`, `not_expired`, `report_processing` |
| `connected_people` | [ConnectedPerson](#connected_person) | `/people/v2/people/{person_id}/connected_people` |  |
| `emails` | [Email](#email) | `/people/v2/people/{person_id}/emails` |  |
| `field_data` | [FieldDatum](#field_datum) | `/people/v2/people/{person_id}/field_data` |  |
| `household_memberships` | [HouseholdMembership](#household_membership) | `/people/v2/people/{person_id}/household_memberships` |  |
| `households` | [Household](#household) | `/people/v2/people/{person_id}/households` |  |
| `inactive_reason` | [InactiveReason](#inactive_reason) | `/people/v2/people/{person_id}/inactive_reason` |  |
| `marital_status` | [MaritalStatus](#marital_status) | `/people/v2/people/{person_id}/marital_status` |  |
| `message_groups` | [MessageGroup](#message_group) | `/people/v2/people/{person_id}/message_groups` |  |
| `messages` | [Message](#message) | `/people/v2/people/{person_id}/messages` | `created_after`, `received`, `sent`, `unread` |
| `name_prefix` | [NamePrefix](#name_prefix) | `/people/v2/people/{person_id}/name_prefix` |  |
| `name_suffix` | [NameSuffix](#name_suffix) | `/people/v2/people/{person_id}/name_suffix` |  |
| `notes` | [Note](#note) | `/people/v2/people/{person_id}/notes` |  |
| `organization` | [Organization](#organization) | `/people/v2/people/{person_id}/organization` |  |
| `person_apps` | [PersonApp](#person_app) | `/people/v2/people/{person_id}/person_apps` |  |
| `phone_numbers` | [PhoneNumber](#phone_number) | `/people/v2/people/{person_id}/phone_numbers` |  |
| `platform_notifications` | [PlatformNotification](#platform_notification) | `/people/v2/people/{person_id}/platform_notifications` |  |
| `primary_campus` | [Campus](#campus) | `/people/v2/people/{person_id}/primary_campus` |  |
| `school` | [SchoolOption](#school_option) | `/people/v2/people/{person_id}/school` |  |
| `social_profiles` | [SocialProfile](#social_profile) | `/people/v2/people/{person_id}/social_profiles` |  |
| `workflow_assignee_workflow_summaries` | [WorkflowAssigneeWorkflowSummary](#workflow_assignee_workflow_summary) | `/people/v2/people/{person_id}/workflow_assignee_workflow_summaries` |  |
| `workflow_cards` | [WorkflowCard](#workflow_card) | `/people/v2/people/{person_id}/workflow_cards` | `assigned` |
| `workflow_shares` | [WorkflowShare](#workflow_share) | `/people/v2/people/{person_id}/workflow_shares` |  |

- `background_checks` filter `current`: filter background checks to only those considered "current"
- `messages`: The Person's received messages. Can also receive a filter to return `sent` or `unread` e.g. `?filter=sent`

**Reached from**

- [BackgroundCheck](#background_check) as `created_by`: `/people/v2/background_checks/{background_check_id}/created_by`
- [BackgroundCheck](#background_check) as `person`: `/people/v2/background_checks/{background_check_id}/person`
- [Condition](#condition) as `created_by`: `/people/v2/lists/{list_id}/rules/{rule_id}/conditions/{condition_id}/created_by`
- [Email](#email) as `person`: `/people/v2/emails/{email_id}/person`
- [FieldDatum](#field_datum) as `person`: `/people/v2/field_data/{field_datum_id}/person`
- [FormSubmission](#form_submission) as `person`: `/people/v2/forms/{form_id}/form_submissions/{form_submission_id}/person`
- [HouseholdMembership](#household_membership) as `person`: `/people/v2/households/{household_id}/household_memberships/{household_membership_id}/person`
- [Household](#household) as `people`: `/people/v2/households/{household_id}/people`
- [List](#list) as `created_by`: `/people/v2/lists/{list_id}/created_by`
- [List](#list) as `people`: `/people/v2/lists/{list_id}/people`
- [ListResult](#list_result) as `person`: `/people/v2/lists/{list_id}/list_results/{list_result_id}/person`
- [ListShare](#list_share) as `person`: `/people/v2/lists/{list_id}/shares/{list_share_id}/person`
- [List](#list) as `updated_by`: `/people/v2/lists/{list_id}/updated_by`
- [MessageGroup](#message_group) as `from`: `/people/v2/message_groups/{message_group_id}/from`
- [Message](#message) as `to`: `/people/v2/messages/{message_id}/to`
- [NoteCategoryShare](#note_category_share) as `person`: `/people/v2/note_categories/{note_category_id}/shares/{note_category_share_id}/person`
- [NoteCategory](#note_category) as `subscribers`: `/people/v2/note_categories/{note_category_id}/subscribers`
- [NoteCategorySubscription](#note_category_subscription) as `person`: `/people/v2/note_category_subscriptions/{note_category_subscription_id}/person`
- [Note](#note) as `created_by`: `/people/v2/notes/{note_id}/created_by`
- [Note](#note) as `person`: `/people/v2/notes/{note_id}/person`
- [Organization](#organization) as `people`: `/people/v2/people`
- [PeopleImportHistory](#people_import_history) as `person`: `/people/v2/people_imports/{people_import_id}/histories/{people_import_history_id}/person`
- [Report](#report) as `created_by`: `/people/v2/reports/{report_id}/created_by`
- [Report](#report) as `updated_by`: `/people/v2/reports/{report_id}/updated_by`
- [SocialProfile](#social_profile) as `person`: `/people/v2/social_profiles/{social_profile_id}/person`
- [WorkflowCard](#workflow_card) as `assignee`: `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/assignee`
- [WorkflowCard](#workflow_card) as `person`: `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/person`
- [WorkflowShare](#workflow_share) as `person`: `/people/v2/people/{person_id}/workflow_shares/{workflow_share_id}/person`
- [Workflow](#workflow) as `shared_people`: `/people/v2/workflows/{workflow_id}/shared_people`
- [WorkflowStepAssigneeSummary](#workflow_step_assignee_summary) as `person`: `/people/v2/workflows/{workflow_id}/steps/{step_id}/assignee_summaries/{workflow_step_assignee_summary_id}/person`
- [WorkflowStep](#workflow_step) as `default_assignee`: `/people/v2/workflows/{workflow_id}/steps/{workflow_step_id}/default_assignee`

**Example record**

```json
{
  "type": "Person",
  "id": "1",
  "attributes": {
    "accounting_administrator": true,
    "anniversary": "2000-01-01",
    "avatar": "string",
    "birthdate": "2000-01-01",
    "can_create_forms": true,
    "can_email_lists": true,
    "child": true,
    "created_at": "2000-01-01T12:00:00Z",
    "demographic_avatar_url": "string",
    "directory_shared_info": {},
    "directory_status": "string",
    "first_name": "string",
    "gender": "string",
    "given_name": "string",
    "grade": 1,
    "graduation_year": 1,
    "inactivated_at": "2000-01-01T12:00:00Z",
    "last_name": "string",
    "login_identifier": "string",
    "medical_notes": "string",
    "membership": "string",
    "mfa_configured": true,
    "middle_name": "string",
    "name": "string",
    "nickname": "string",
    "passed_background_check": true,
    "people_permissions": "string",
    "remote_id": 1,
    "school_type": "string",
    "selective_access": true,
    "site_administrator": true,
    "status": "string",
    "stripe_account_identifier": "string",
    "stripe_customer_identifier": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "primary_campus": {
      "data": {
        "type": "PrimaryCampus",
        "id": "1"
      }
    },
    "created_by": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="person_app"></a>
## PersonApp

A Person App is the relationship between a Person and an App.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/person_apps` | List |
| GET | `/people/v2/people/{person_id}/person_apps/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/person_apps` | Create |

Attributes that can be set on create: `app_id`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `allow_pco_login` | boolean | `true` |  |
| `id` | primary_key | `"primary_key"` |  |
| `people_permissions` | string | `"value"` | Possible values: `no_access`, `viewer`, or `editor` |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `app` | to_one | [App](#app) |  |
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `include`: `app`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `app` | [App](#app) | `/people/v2/people/{person_id}/person_apps/{person_app_id}/app` |  |

**Reached from**

- [Person](#person) as `person_apps`: `/people/v2/people/{person_id}/person_apps`

**Example record**

```json
{
  "type": "PersonApp",
  "id": "1",
  "attributes": {
    "allow_pco_login": true,
    "people_permissions": "value"
  },
  "relationships": {
    "app": {
      "data": {
        "type": "App",
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

<a id="person_merger"></a>
## PersonMerger

A Person Merger is the history of profiles that were merged into other profiles.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/person_mergers` | List |
| GET | `/people/v2/person_mergers/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `person_to_keep_id` | integer | `1` |  |
| `person_to_remove_id` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person_to_keep` | to_one | [Person](#person) |  |
| `person_to_remove` | to_one | [Person](#person) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[person_to_keep_id]` (integer), for example `?where[person_to_keep_id]=1`
  - `where[person_to_remove_id]` (integer), for example `?where[person_to_remove_id]=1`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `person_mergers`: `/people/v2/person_mergers`

**Example record**

```json
{
  "type": "PersonMerger",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "person_to_keep_id": 1,
    "person_to_remove_id": 1
  },
  "relationships": {
    "person_to_keep": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "person_to_remove": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="phone_number"></a>
## PhoneNumber

A phone number represents a single telephone number and location.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/phone_numbers` | List |
| GET | `/people/v2/phone_numbers/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/phone_numbers` | Create |
| PATCH | `/people/v2/phone_numbers/{id}` | Update |
| DELETE | `/people/v2/phone_numbers/{id}` | Delete |

Attributes that can be set on create: `number`, `carrier`, `location`, `primary`

Attributes that can be set on update: `number`, `carrier`, `location`, `primary`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `carrier` | string | `"string"` |  |
| `country_code` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `e164` | string | `"string"` |  |
| `formatted_number` | string | `"string"` | Only available when requested with the `?fields` param |
| `id` | primary_key | `"primary_key"` |  |
| `international` | string | `"string"` |  |
| `location` | string | `"string"` |  |
| `national` | string | `"string"` |  |
| `number` | string | `"string"` |  |
| `primary` | boolean | `true` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `verified` | boolean | `true` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `carrier`, `created_at`, `location`, `number`, `primary`, `updated_at`
- Query with `where[...]`:
  - `where[carrier]` (string), for example `?where[carrier]=string`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[location]` (string), for example `?where[location]=string`
  - `where[number]` (string), for example `?where[number]=string`
  - `where[primary]` (boolean), for example `?where[primary]=true`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `phone_numbers`: `/people/v2/phone_numbers`
- [Person](#person) as `phone_numbers`: `/people/v2/people/{person_id}/phone_numbers`

**Example record**

```json
{
  "type": "PhoneNumber",
  "id": "1",
  "attributes": {
    "carrier": "string",
    "country_code": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "e164": "string",
    "formatted_number": "string",
    "international": "string",
    "location": "string",
    "national": "string",
    "number": "string",
    "primary": true,
    "updated_at": "2000-01-01T12:00:00Z",
    "verified": true
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="platform_notification"></a>
## PlatformNotification

A Platform Notification is a suite-wide notification that shows at the top of each application's screen until dismissed by the user.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/platform_notifications` | List |
| GET | `/people/v2/people/{person_id}/platform_notifications/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `html` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |

**Reached from**

- [Person](#person) as `platform_notifications`: `/people/v2/people/{person_id}/platform_notifications`

**Example record**

```json
{
  "type": "PlatformNotification",
  "id": "1",
  "attributes": {
    "html": "string"
  },
  "relationships": {}
}
```

<a id="report"></a>
## Report

A report is editable liquid syntax that provides a powerful tool for presenting your Lists however you want.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/reports` | List |
| GET | `/people/v2/reports/{id}` | Fetch one |
| POST | `/people/v2/reports` | Create |
| PATCH | `/people/v2/reports/{id}` | Update |
| DELETE | `/people/v2/reports/{id}` | Delete |

Attributes that can be set on create: `name`, `body`

Attributes that can be set on update: `name`, `body`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `body` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `created_by`, `updated_by`
- `order` (prefix with `-` to reverse): `body`, `created_at`, `name`, `updated_at`
- Query with `where[...]`:
  - `where[body]` (string), for example `?where[body]=string`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `created_by` | [Person](#person) | `/people/v2/reports/{report_id}/created_by` |  |
| `updated_by` | [Person](#person) | `/people/v2/reports/{report_id}/updated_by` |  |

**Reached from**

- [Organization](#organization) as `reports`: `/people/v2/reports`

**Example record**

```json
{
  "type": "Report",
  "id": "1",
  "attributes": {
    "body": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "name": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="rule"></a>
## Rule

A rule belongs to a List and groups conditions together.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/lists/{list_id}/rules` | List |
| GET | `/people/v2/lists/{list_id}/rules/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `subset` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `include`: `conditions`
- `order` (prefix with `-` to reverse): `created_at`, `subset`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[subset]` (string), for example `?where[subset]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `conditions` | [Condition](#condition) | `/people/v2/lists/{list_id}/rules/{rule_id}/conditions` |  |

**Reached from**

- [List](#list) as `rules`: `/people/v2/lists/{list_id}/rules`

**Example record**

```json
{
  "type": "Rule",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "subset": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="school_option"></a>
## SchoolOption

A school option represents a school name, school type, grades, etc. and can be selected for a person.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/school_options` | List |
| GET | `/people/v2/school_options/{id}` | Fetch one |
| POST | `/people/v2/school_options` | Create |
| PATCH | `/people/v2/school_options/{id}` | Update |
| DELETE | `/people/v2/school_options/{id}` | Delete |

Attributes that can be set on create: `value`, `sequence`, `beginning_grade`, `ending_grade`, `school_types`

Attributes that can be set on update: `value`, `sequence`, `beginning_grade`, `ending_grade`, `school_types`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `beginning_grade` | string | `"string"` |  |
| `ending_grade` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `school_types` | array | `[]` |  |
| `sequence` | integer | `1` |  |
| `value` | string | `"string"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `beginning_grade`, `ending_grade`, `sequence`, `value`
- Query with `where[...]`:
  - `where[beginning_grade]` (string), for example `?where[beginning_grade]=string`
  - `where[ending_grade]` (string), for example `?where[ending_grade]=string`
  - `where[school_types]` (array), for example `?where[school_types]=[]`
  - `where[sequence]` (integer), for example `?where[sequence]=1`
  - `where[value]` (string), for example `?where[value]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `promotes_to_school` | [SchoolOption](#school_option) | `/people/v2/school_options/{school_option_id}/promotes_to_school` |  |

**Reached from**

- [Organization](#organization) as `school_options`: `/people/v2/school_options`
- [Person](#person) as `school`: `/people/v2/people/{person_id}/school`
- [SchoolOption](#school_option) as `promotes_to_school`: `/people/v2/school_options/{school_option_id}/promotes_to_school`

**Example record**

```json
{
  "type": "SchoolOption",
  "id": "1",
  "attributes": {
    "beginning_grade": "string",
    "ending_grade": "string",
    "school_types": [],
    "sequence": 1,
    "value": "string"
  },
  "relationships": {}
}
```

<a id="service_time"></a>
## ServiceTime

A ServiceTime Resource

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/campuses/{campus_id}/service_times` | List |
| GET | `/people/v2/campuses/{campus_id}/service_times/{id}` | Fetch one |
| POST | `/people/v2/campuses/{campus_id}/service_times` | Create |
| PATCH | `/people/v2/campuses/{campus_id}/service_times/{id}` | Update |
| DELETE | `/people/v2/campuses/{campus_id}/service_times/{id}` | Delete |

Attributes that can be set on create: `start_time`, `day`, `description`

Attributes that can be set on update: `start_time`, `day`, `description`

Permission to create: Must be an Organization Administrator

Permission to update: Must be an Organization Administrator

Permission to destroy: Must be an Organization Administrator

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `day` | string | `"value"` | Possible values: `sunday`, `monday`, `tuesday`, `wednesday`, `thursday`, `friday`, or `saturday` |
| `description` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `start_time` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `organization` | to_one | [Organization](#organization) |  |
| `campus` | to_one | [Campus](#campus) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `time`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Campus](#campus) as `service_times`: `/people/v2/campuses/{campus_id}/service_times`

**Example record**

```json
{
  "type": "ServiceTime",
  "id": "1",
  "attributes": {
    "day": "value",
    "description": "string",
    "start_time": 1
  },
  "relationships": {
    "organization": {
      "data": {
        "type": "Organization",
        "id": "1"
      }
    },
    "campus": {
      "data": {
        "type": "Campus",
        "id": "1"
      }
    }
  }
}
```

<a id="social_profile"></a>
## SocialProfile

A social profile represents a members's Twitter, Facebook, or other social media account.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/social_profiles` | List |
| GET | `/people/v2/social_profiles/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/social_profiles` | Create |
| PATCH | `/people/v2/social_profiles/{id}` | Update |
| DELETE | `/people/v2/social_profiles/{id}` | Delete |

Attributes that can be set on create: `site`, `url`, `verified`

Attributes that can be set on update: `site`, `url`, `verified`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `site` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `url` | string | `"string"` |  |
| `verified` | boolean | `true` |  |

**URL parameters**

- `include`: `person`
- `order` (prefix with `-` to reverse): `created_at`, `site`, `updated_at`, `url`, `verified`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[site]` (string), for example `?where[site]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
  - `where[url]` (string), for example `?where[url]=string`
  - `where[verified]` (boolean), for example `?where[verified]=true`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/social_profiles/{social_profile_id}/person` |  |

**Reached from**

- [Organization](#organization) as `social_profiles`: `/people/v2/social_profiles`
- [Person](#person) as `social_profiles`: `/people/v2/people/{person_id}/social_profiles`

**Example record**

```json
{
  "type": "SocialProfile",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "site": "string",
    "updated_at": "2000-01-01T12:00:00Z",
    "url": "string",
    "verified": true
  },
  "relationships": {}
}
```

<a id="spam_email_address"></a>
## SpamEmailAddress

An email address that is marked as spam

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/spam_email_addresses` | List |
| GET | `/people/v2/spam_email_addresses/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `address` | string | `"string"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Reached from**

- [Organization](#organization) as `spam_email_addresses`: `/people/v2/spam_email_addresses`

**Example record**

```json
{
  "type": "SpamEmailAddress",
  "id": "1",
  "attributes": {
    "address": "string",
    "created_at": "2000-01-01T12:00:00Z",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="tab"></a>
## Tab

A tab is a custom tab and groups like field definitions.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/tabs` | List |
| GET | `/people/v2/tabs/{id}` | Fetch one |
| POST | `/people/v2/tabs` | Create |
| PATCH | `/people/v2/tabs/{id}` | Update |
| DELETE | `/people/v2/tabs/{id}` | Delete |

Attributes that can be set on create: `name`, `sequence`

Attributes that can be set on update: `name`, `sequence`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `sequence` | integer | `1` |  |
| `slug` | string | `"string"` |  |

**URL parameters**

- `include`: `field_definitions`, `field_options`
- `order` (prefix with `-` to reverse): `name`, `sequence`, `slug`
- Query with `where[...]`:
  - `where[name]` (string), for example `?where[name]=string`
  - `where[sequence]` (integer), for example `?where[sequence]=1`
  - `where[slug]` (string), for example `?where[slug]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `field_definitions` | [FieldDefinition](#field_definition) | `/people/v2/tabs/{tab_id}/field_definitions` | `with_deleted` |
| `field_options` | [FieldOption](#field_option) | `/people/v2/tabs/{tab_id}/field_options` |  |

**Reached from**

- [FieldDatum](#field_datum) as `tab`: `/people/v2/field_data/{field_datum_id}/tab`
- [FieldDefinition](#field_definition) as `tab`: `/people/v2/field_definitions/{field_definition_id}/tab`
- [Organization](#organization) as `tabs`: `/people/v2/tabs`

**Example record**

```json
{
  "type": "Tab",
  "id": "1",
  "attributes": {
    "name": "string",
    "sequence": 1,
    "slug": "string"
  },
  "relationships": {}
}
```

<a id="workflow"></a>
## Workflow

A Workflow

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/workflows` | List |
| GET | `/people/v2/workflows/{id}` | Fetch one |
| POST | `/people/v2/workflows` | Create |
| PATCH | `/people/v2/workflows/{id}` | Update |
| DELETE | `/people/v2/workflows/{id}` | Delete |

Attributes that can be set on create: `name`, `campus_id`, `workflow_category_id`

Attributes that can be set on update: `name`, `campus_id`, `workflow_category_id`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `archived_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `campus_id` | integer | `1` |  |
| `completed_card_count` | integer | `1` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `deleted_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `my_due_soon_card_count` | integer | `1` | Only available when requested with the `?fields` param |
| `my_overdue_card_count` | integer | `1` | Only available when requested with the `?fields` param |
| `my_ready_card_count` | integer | `1` |  |
| `name` | string | `"string"` |  |
| `recently_viewed` | boolean | `true` |  |
| `total_cards_count` | integer | `1` |  |
| `total_overdue_card_count` | integer | `1` |  |
| `total_ready_and_snoozed_card_count` | integer | `1` |  |
| `total_ready_card_count` | integer | `1` |  |
| `total_steps_count` | integer | `1` |  |
| `total_unassigned_card_count` | integer | `1` |  |
| `total_unassigned_steps_count` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `workflow_category_id` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `workflow_category` | to_one | [WorkflowCategory](#workflow_category) |  |
| `campus` | to_one | [Campus](#campus) |  |

**URL parameters**

- `include`: `category`, `shares`, `steps`
- `order` (prefix with `-` to reverse): `archived_at`, `campus_id`, `campus_name`, `created_at`, `deleted_at`, `my_ready_card_count`, `name`, `recently_viewed`, `total_overdue_card_count`, `total_ready_card_count`, `total_unassigned_card_count`, `updated_at`, `workflow_category_id`, `workflow_category_name`
- Query with `where[...]`:
  - `where[archived_at]` (date_time), for example `?where[archived_at]=2000-01-01T12:00:00Z`
  - `where[campus_id]` (integer), for example `?where[campus_id]=1`
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[deleted_at]` (date_time), for example `?where[deleted_at]=2000-01-01T12:00:00Z`
  - `where[id]` (primary_key), for example `?where[id]=primary_key`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
  - `where[workflow_category_id]` (integer), for example `?where[workflow_category_id]=1`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `cards` | [WorkflowCard](#workflow_card) | `/people/v2/workflows/{workflow_id}/cards` |  |
| `category` | [WorkflowCategory](#workflow_category) | `/people/v2/workflows/{workflow_id}/category` |  |
| `shared_people` | [Person](#person) | `/people/v2/workflows/{workflow_id}/shared_people` |  |
| `shares` | [WorkflowShare](#workflow_share) | `/people/v2/workflows/{workflow_id}/shares` |  |
| `steps` | [WorkflowStep](#workflow_step) | `/people/v2/workflows/{workflow_id}/steps` |  |

**Reached from**

- [Organization](#organization) as `workflows`: `/people/v2/workflows`
- [WorkflowCard](#workflow_card) as `workflow`: `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/workflow`

**Example record**

```json
{
  "type": "Workflow",
  "id": "1",
  "attributes": {
    "archived_at": "2000-01-01T12:00:00Z",
    "campus_id": 1,
    "completed_card_count": 1,
    "created_at": "2000-01-01T12:00:00Z",
    "deleted_at": "2000-01-01T12:00:00Z",
    "my_due_soon_card_count": 1,
    "my_overdue_card_count": 1,
    "my_ready_card_count": 1,
    "name": "string",
    "recently_viewed": true,
    "total_cards_count": 1,
    "total_overdue_card_count": 1,
    "total_ready_and_snoozed_card_count": 1,
    "total_ready_card_count": 1,
    "total_steps_count": 1,
    "total_unassigned_card_count": 1,
    "total_unassigned_steps_count": 1,
    "updated_at": "2000-01-01T12:00:00Z",
    "workflow_category_id": 1
  },
  "relationships": {
    "workflow_category": {
      "data": {
        "type": "WorkflowCategory",
        "id": "1"
      }
    },
    "campus": {
      "data": {
        "type": "Campus",
        "id": "1"
      }
    }
  }
}
```

<a id="workflow_assignee_summary"></a>
## WorkflowAssigneeSummary

Aggregated card counts for an assignee across all visible workflows

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/workflow_assignee_summaries` | List |
| GET | `/people/v2/workflow_assignee_summaries/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `overdue_count` | integer | `1` |  |
| `ready_count` | integer | `1` |  |
| `snoozed_count` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `first_name`, `last_name`, `name`, `overdue_count`, `ready_count`, `snoozed_count`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Organization](#organization) as `workflow_assignee_summaries`: `/people/v2/workflow_assignee_summaries`

**Example record**

```json
{
  "type": "WorkflowAssigneeSummary",
  "id": "1",
  "attributes": {
    "overdue_count": 1,
    "ready_count": 1,
    "snoozed_count": 1
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    }
  }
}
```

<a id="workflow_assignee_workflow_summary"></a>
## WorkflowAssigneeWorkflowSummary

Per-workflow card counts for a specific assignee

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/workflow_assignee_workflow_summaries` | List |
| GET | `/people/v2/people/{person_id}/workflow_assignee_workflow_summaries/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `overdue_count` | integer | `1` |  |
| `ready_count` | integer | `1` |  |
| `snoozed_count` | integer | `1` |  |
| `workflow_name` | string | `"string"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `workflow` | to_one | [Workflow](#workflow) |  |
| `person` | to_one | [Person](#person) |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `overdue_count`, `ready_count`, `snoozed_count`, `workflow_name`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Person](#person) as `workflow_assignee_workflow_summaries`: `/people/v2/people/{person_id}/workflow_assignee_workflow_summaries`

**Example record**

```json
{
  "type": "WorkflowAssigneeWorkflowSummary",
  "id": "1",
  "attributes": {
    "overdue_count": 1,
    "ready_count": 1,
    "snoozed_count": 1,
    "workflow_name": "string"
  },
  "relationships": {
    "workflow": {
      "data": {
        "type": "Workflow",
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

<a id="workflow_card"></a>
## WorkflowCard

A Card

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/workflow_cards` | List |
| GET | `/people/v2/people/{person_id}/workflow_cards/{id}` | Fetch one |
| POST | `/people/v2/workflows/{workflow_id}/cards` | Create |
| PATCH | `/people/v2/people/{person_id}/workflow_cards/{id}` | Update |
| DELETE | `/people/v2/people/{person_id}/workflow_cards/{id}` | Delete |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/go_back` | Action: Move a Workflow Card back to the previous step. |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/promote` | Action: Move a Workflow Card to the next step. |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/remove` | Action: Removes a card |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/restore` | Action: Restore a card |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/send_email` | Action: Sends an email to the subject of the card |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/skip_step` | Action: Move a Workflow Card to the next step without completing the current step. |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/snooze` | Action: Snoozes a card for a specific duration |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/unsnooze` | Action: Unsnoozes a card |

Attributes that can be set on create: `sticky_assignment`, `assignee_id`, `person_id`

Attributes that can be set on update: `sticky_assignment`, `assignee_id`, `person_id`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `awaiting_automation` | boolean | `true` |  |
| `calculated_due_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `calculated_due_at_in_days_ago` | integer | `1` |  |
| `completed_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `flagged_for_notification_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `moved_to_step_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `overdue` | boolean | `true` |  |
| `pending_skip_evaluation` | boolean | `true` |  |
| `removed_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `snooze_until` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `stage` | string | `"string"` |  |
| `sticky_assignment` | boolean | `true` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `assignee` | to_one | Assignee |  |
| `person` | to_one | [Person](#person) |  |
| `workflow` | to_one | [Workflow](#workflow) |  |
| `current_step` | to_one | [WorkflowStep](#workflow_step) |  |

**URL parameters**

- `include`: `assignee`, `current_step`, `person`, `workflow`
- `order` (prefix with `-` to reverse): `completed_at`, `created_at`, `first_name`, `flagged_for_notification_at`, `last_name`, `moved_to_step_at`, `removed_at`, `stage`, `updated_at`
- Query with `where[...]`:
  - `where[assignee_id]` (integer), for example `?where[assignee_id]=1`
  - `where[overdue]` (boolean), for example `?where[overdue]=true`
  - `where[stage]` (string), for example `?where[stage]=string`
- `per_page` (1 to 100, default 25) and `offset`

**Action `send_email`**: `POST /people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/send_email`

Pass in a subject and note.

Example body

```json
{
  "data": {
    "attributes": {
      "subject": "Thanks for visiting this past Sunday!",
      "note": "It was great to meet you this past Sunday! Hope to see you again."
    }
  }
}
```

**Action `snooze`**: `POST /people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/snooze`

Pass in a duration in days.

Who can run it: Must be an editor or the person the card is assigned to

Example body

```json
{
  "data": {
    "attributes": {
      "duration": 15
    }
  }
}
```

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `activities` | [WorkflowCardActivity](#workflow_card_activity) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/activities` |  |
| `assignee` | [Person](#person) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/assignee` |  |
| `current_step` | [WorkflowStep](#workflow_step) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/current_step` |  |
| `notes` | [WorkflowCardNote](#workflow_card_note) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/notes` |  |
| `person` | [Person](#person) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/person` |  |
| `workflow` | [Workflow](#workflow) | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/workflow` |  |

**Reached from**

- [Person](#person) as `workflow_cards`: `/people/v2/people/{person_id}/workflow_cards`
- [Workflow](#workflow) as `cards`: `/people/v2/workflows/{workflow_id}/cards`

**Example record**

```json
{
  "type": "WorkflowCard",
  "id": "1",
  "attributes": {
    "awaiting_automation": true,
    "calculated_due_at": "2000-01-01T12:00:00Z",
    "calculated_due_at_in_days_ago": 1,
    "completed_at": "2000-01-01T12:00:00Z",
    "created_at": "2000-01-01T12:00:00Z",
    "flagged_for_notification_at": "2000-01-01T12:00:00Z",
    "moved_to_step_at": "2000-01-01T12:00:00Z",
    "overdue": true,
    "pending_skip_evaluation": true,
    "removed_at": "2000-01-01T12:00:00Z",
    "snooze_until": "2000-01-01T12:00:00Z",
    "stage": "string",
    "sticky_assignment": true,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "assignee": {
      "data": {
        "type": "Assignee",
        "id": "1"
      }
    },
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "workflow": {
      "data": {
        "type": "Workflow",
        "id": "1"
      }
    },
    "current_step": {
      "data": {
        "type": "WorkflowStep",
        "id": "1"
      }
    }
  }
}
```

<a id="workflow_card_activity"></a>
## WorkflowCardActivity

Workflow Card Activity is a record of an action performed on a card

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/activities` | List |
| GET | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/activities/{id}` | Fetch one |
| DELETE | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/activities/{id}` | Delete |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `attachments_meta` | array | `[]` |  |
| `attachments_urls` | array | `[]` |  |
| `automation_url` | string | `"string"` |  |
| `comment` | string | `"string"` |  |
| `content` | string | `"string"` |  |
| `content_is_html` | boolean | `true` |  |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `form_submission_url` | string | `"string"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `person_avatar_url` | string | `"string"` |  |
| `person_name` | string | `"string"` |  |
| `reassigned_to_avatar_url` | string | `"string"` |  |
| `reassigned_to_name` | string | `"string"` |  |
| `subject` | string | `"string"` |  |
| `type` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) | The person the `person_name` attribute refers to. Null when the activity was performed by an integration or an automation rather than a person. |
| `reassigned_to` | to_one | [Person](#person) | The person named by the `reassigned_to_name` attribute. |
| `workflow_card` | to_one | [WorkflowCard](#workflow_card) |  |
| `workflow_step` | to_one | [WorkflowStep](#workflow_step) | Only available when `type` attribute is `completion`, `skip`, `reversal`, `automation_success`, `automation_failure`, `manual_action_success`, or `manual_action_failure`. |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`, `id`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [WorkflowCard](#workflow_card) as `activities`: `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/activities`

**Example record**

```json
{
  "type": "WorkflowCardActivity",
  "id": "1",
  "attributes": {
    "attachments_meta": [],
    "attachments_urls": [],
    "automation_url": "string",
    "comment": "string",
    "content": "string",
    "content_is_html": true,
    "created_at": "2000-01-01T12:00:00Z",
    "form_submission_url": "string",
    "person_avatar_url": "string",
    "person_name": "string",
    "reassigned_to_avatar_url": "string",
    "reassigned_to_name": "string",
    "subject": "string",
    "type": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "reassigned_to": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "workflow_card": {
      "data": {
        "type": "WorkflowCard",
        "id": "1"
      }
    },
    "workflow_step": {
      "data": {
        "type": "WorkflowStep",
        "id": "1"
      }
    }
  }
}
```

<a id="workflow_card_note"></a>
## WorkflowCardNote

Workflow Note is a note that has been made on a Workflow Card

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/notes` | List |
| GET | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/notes/{id}` | Fetch one |
| POST | `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/notes` | Create |

Attributes that can be set on create: `note`

Permission to create: If you want to also create a matching profile note in the note category of your choice, pass in the ID of the note category. ``` { "data": { "attributes": { "note": "this is a new note!", "note_category_id": 123 } } } ``` The `note_category_id` is not actually stored on the workflow card note itself, so if you need to edit or delete the resulting profile note, you will need to do so via `people/v2/notes`. You can GET all the existing note categories at `/people/v2/note_categories`.

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `note` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [WorkflowCard](#workflow_card) as `notes`: `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/notes`

**Example record**

```json
{
  "type": "WorkflowCardNote",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "note": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="workflow_category"></a>
## WorkflowCategory

A Workflow Category

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/workflows/{workflow_id}/category` | List |
| GET | `/people/v2/workflows/{workflow_id}/category/{id}` | Fetch one |
| POST | `/people/v2/workflows/{workflow_id}/category` | Create |
| PATCH | `/people/v2/workflows/{workflow_id}/category/{id}` | Update |
| DELETE | `/people/v2/workflows/{workflow_id}/category/{id}` | Delete |

Attributes that can be set on create: `name`

Attributes that can be set on update: `name`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `id` | primary_key | `"primary_key"` |  |
| `name` | string | `"string"` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**URL parameters**

- `order` (prefix with `-` to reverse): `created_at`, `name`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Reached from**

- [Workflow](#workflow) as `category`: `/people/v2/workflows/{workflow_id}/category`

**Example record**

```json
{
  "type": "WorkflowCategory",
  "id": "1",
  "attributes": {
    "created_at": "2000-01-01T12:00:00Z",
    "name": "string",
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {}
}
```

<a id="workflow_share"></a>
## WorkflowShare

A workflow share defines who can access a workflow.

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/people/{person_id}/workflow_shares` | List |
| GET | `/people/v2/people/{person_id}/workflow_shares/{id}` | Fetch one |
| POST | `/people/v2/workflows/{workflow_id}/shares` | Create |
| PATCH | `/people/v2/people/{person_id}/workflow_shares/{id}` | Update |
| DELETE | `/people/v2/people/{person_id}/workflow_shares/{id}` | Delete |

Attributes that can be set on create: `group`, `permission`, `person_id`

Attributes that can be set on update: `group`, `permission`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `group` | string | `"value"` | Possible values: `No Access`, `Viewer`, `Editor`, or `Manager` |
| `id` | primary_key | `"primary_key"` |  |
| `permission` | string | `"value"` | Possible values: `view`, `manage_cards`, or `manage` |
| `person_id` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |
| `workflow` | to_one | [Workflow](#workflow) |  |

**URL parameters**

- `include`: `person`
- Query with `where[...]`:
  - `where[permission]` (string), for example `?where[permission]=value`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/people/{person_id}/workflow_shares/{workflow_share_id}/person` |  |

**Reached from**

- [Person](#person) as `workflow_shares`: `/people/v2/people/{person_id}/workflow_shares`
- [Workflow](#workflow) as `shares`: `/people/v2/workflows/{workflow_id}/shares`

**Example record**

```json
{
  "type": "WorkflowShare",
  "id": "1",
  "attributes": {
    "group": "value",
    "permission": "value",
    "person_id": 1
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "workflow": {
      "data": {
        "type": "Workflow",
        "id": "1"
      }
    }
  }
}
```

<a id="workflow_step"></a>
## WorkflowStep

A Step

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/workflows/{workflow_id}/steps` | List |
| GET | `/people/v2/workflows/{workflow_id}/steps/{id}` | Fetch one |
| POST | `/people/v2/workflows/{workflow_id}/steps` | Create |
| PATCH | `/people/v2/workflows/{workflow_id}/steps/{id}` | Update |
| DELETE | `/people/v2/workflows/{workflow_id}/steps/{id}` | Delete |

Attributes that can be set on create: `sequence`, `name`, `description`, `expected_response_time_in_days`, `default_assignee_id`, `auto_snooze_value`, `auto_snooze_interval`, `auto_snooze_wake_hour`, `auto_snooze_entry_hour`, `skip_conditions_enabled`, `action_type`

Attributes that can be set on update: `sequence`, `name`, `description`, `expected_response_time_in_days`, `default_assignee_id`, `auto_snooze_value`, `auto_snooze_interval`, `auto_snooze_wake_hour`, `auto_snooze_entry_hour`, `skip_conditions_enabled`, `action_type`

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `action_type` | string | `"value"` | Possible values: `assign` or `automate` |
| `auto_snooze_days` | integer | `1` |  |
| `auto_snooze_entry_hour` | integer | `1` | Valid values are 17-23. When set, only cards arriving after this hour are snoozed. Requires `auto_snooze_value`, `auto_snooze_interval`, and `auto_snooze_wake_hour` (which must be before it). |
| `auto_snooze_interval` | string | `"value"` | Valid values are `day`, `week`, or `month`. When auto_snooze_entry_hour is set, this is not the snooze length. The card wakes at auto_snooze_wake_hour, and this duration adds only whole days beyond that. Possible values: `day`, `week`, or `month` |
| `auto_snooze_value` | integer | `1` | Must be a positive number. When auto_snooze_entry_hour is set, this is not the snooze length. The card wakes at auto_snooze_wake_hour, and this duration adds only whole days beyond that. |
| `auto_snooze_wake_hour` | integer | `1` | Valid values are 7-19, defaulting to 7 when not set. The hour of day cards snooze until. Only present when auto-snooze is enabled (`auto_snooze_value` and `auto_snooze_interval` are set). |
| `created_at` | date_time | `"2000-01-01T12:00:00Z"` |  |
| `default_assignee_id` | primary_key | `"primary_key"` |  |
| `description` | string | `"string"` |  |
| `expected_response_time_in_days` | integer | `1` |  |
| `id` | primary_key | `"primary_key"` |  |
| `my_ready_card_count` | integer | `1` |  |
| `name` | string | `"string"` |  |
| `sequence` | integer | `1` |  |
| `skip_conditions_enabled` | boolean | `true` |  |
| `total_ready_card_count` | integer | `1` |  |
| `updated_at` | date_time | `"2000-01-01T12:00:00Z"` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `default_assignee` | to_one | [Person](#person) |  |
| `workflow` | to_one | [Workflow](#workflow) |  |

**URL parameters**

- `include`: `default_assignee`
- `order` (prefix with `-` to reverse): `created_at`, `name`, `sequence`, `updated_at`
- Query with `where[...]`:
  - `where[created_at]` (date_time), for example `?where[created_at]=2000-01-01T12:00:00Z`
  - `where[name]` (string), for example `?where[name]=string`
  - `where[updated_at]` (date_time), for example `?where[updated_at]=2000-01-01T12:00:00Z`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `assignee_summaries` | [WorkflowStepAssigneeSummary](#workflow_step_assignee_summary) | `/people/v2/workflows/{workflow_id}/steps/{workflow_step_id}/assignee_summaries` |  |
| `default_assignee` | [Person](#person) | `/people/v2/workflows/{workflow_id}/steps/{workflow_step_id}/default_assignee` |  |

**Reached from**

- [WorkflowCard](#workflow_card) as `current_step`: `/people/v2/people/{person_id}/workflow_cards/{workflow_card_id}/current_step`
- [Workflow](#workflow) as `steps`: `/people/v2/workflows/{workflow_id}/steps`

**Example record**

```json
{
  "type": "WorkflowStep",
  "id": "1",
  "attributes": {
    "action_type": "value",
    "auto_snooze_days": 1,
    "auto_snooze_entry_hour": 1,
    "auto_snooze_interval": "value",
    "auto_snooze_value": 1,
    "auto_snooze_wake_hour": 1,
    "created_at": "2000-01-01T12:00:00Z",
    "default_assignee_id": "primary_key",
    "description": "string",
    "expected_response_time_in_days": 1,
    "my_ready_card_count": 1,
    "name": "string",
    "sequence": 1,
    "skip_conditions_enabled": true,
    "total_ready_card_count": 1,
    "updated_at": "2000-01-01T12:00:00Z"
  },
  "relationships": {
    "default_assignee": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "workflow": {
      "data": {
        "type": "Workflow",
        "id": "1"
      }
    }
  }
}
```

<a id="workflow_step_assignee_summary"></a>
## WorkflowStepAssigneeSummary

The ready and snoozed count for an assignee & step

**Endpoints**

| Method | Path | What it does |
|---|---|---|
| GET | `/people/v2/workflows/{workflow_id}/steps/{step_id}/assignee_summaries` | List |
| GET | `/people/v2/workflows/{workflow_id}/steps/{step_id}/assignee_summaries/{id}` | Fetch one |

**Attributes**

| Name | Type | Example | Notes |
|---|---|---|---|
| `id` | primary_key | `"primary_key"` |  |
| `ready_count` | integer | `1` |  |
| `snoozed_count` | integer | `1` |  |

**Relationships**

| Name | Kind | Resource | Notes |
|---|---|---|---|
| `person` | to_one | [Person](#person) |  |
| `step` | to_one | Step |  |

**URL parameters**

- `include`: `person`
- `per_page` (1 to 100, default 25) and `offset`

**Related resources from here**

| Name | Returns | Path | Filters |
|---|---|---|---|
| `person` | [Person](#person) | `/people/v2/workflows/{workflow_id}/steps/{step_id}/assignee_summaries/{workflow_step_assignee_summary_id}/person` |  |

**Reached from**

- [WorkflowStep](#workflow_step) as `assignee_summaries`: `/people/v2/workflows/{workflow_id}/steps/{workflow_step_id}/assignee_summaries`

**Example record**

```json
{
  "type": "WorkflowStepAssigneeSummary",
  "id": "1",
  "attributes": {
    "ready_count": 1,
    "snoozed_count": 1
  },
  "relationships": {
    "person": {
      "data": {
        "type": "Person",
        "id": "1"
      }
    },
    "step": {
      "data": {
        "type": "Step",
        "id": "1"
      }
    }
  }
}
```
