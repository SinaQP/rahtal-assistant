# Rahtal API discovery for the Codex MCP assistant

This document records the API contract observed in the backend source and the
current Rahtal frontend. It is intentionally limited to authentication, the
authenticated user's assigned orders ("tasks"), and daily activity/performance
records.

## Terminology and base paths

- A user-facing **task** is a `leadership.Order`, assigned through
  `Order.user`. Shared task text and metadata live on its `OrderGroup`.
- A **performance/work record** is a `functionality.Activities` row.
- An activity can be linked to an assigned task through `Activities.order`.
- All IDs in these domains are integer primary keys. `Order.code` is a separate
  positive, unique, user-visible number.
- The routes below are relative to the configured Rahtal base URL.

## Authentication

### Login

- Endpoint: `POST /auth/jwt/create/`
- Source: `base/urls.py` includes `djoser.urls.jwt` at `/auth/`; the frontend
  calls this exact endpoint.
- JSON request:

  ```json
  {"username": "...", "password": "..."}
  ```

- Serializer: `CustomTokenObtainPairSerializer`.
- Response: the dashboard fields below plus `access` and `refresh` JWT strings.
  Dashboard generation is guarded, so only `access` and `refresh` are certain.

  ```text
  id, person_id, first_name, last_name, picture, is_admin,
  activities_stats, recent_activity_status, sessions_info, access, refresh
  ```

- Invalid credentials or an inactive account produce an authentication error
  with a localized `message`.

### Token use and refresh

- Authorization header: `Authorization: JWT <access-token>`.
- Access lifetime: 1 day.
- Refresh lifetime: 30 days.
- Refresh endpoint: `POST /auth/jwt/refresh/`.
- Refresh JSON: `{"refresh": "..."}`.
- Refresh response contains a new `access` token. Token rotation is not enabled,
  so the original refresh token remains in use.

### Current user

- Endpoint: `GET /user/auth/user-info/`
- Permission: authenticated user.
- Response fields:

  ```text
  id, full_name, is_active, is_admin, role, actions,
  notifications_count, notifications
  ```

`username` and employee/person ID are not returned by this endpoint, so the MCP
`whoami` result must not invent them.

## Tasks (`leadership.Order`)

### Assignment and status

`Order.user` is the assignee and `Order.order_group.commander` is the creator.
The data migration `0012_insert_order_statuses.py` establishes these stable
status IDs:

| ID | Persian name | MCP alias |
|---:|---|---|
| 1 | برای انجام | `todo` |
| 2 | در حال انجام | `in_progress` |
| 3 | انجام شد | `done` |
| 4 | انجام نشد | `not_done` |

Approval values are `pending`, `approved`, and `rejected`.

### List the current user's tasks

- Endpoint: `GET /leadership/get-orders/status/{status_id}/`
- Permission: authenticated user.
- Ownership: the query is always constrained to `Order.user == request.user`.
- Side effect: returned rows are marked `viewed=True`.
- Query parameters:
  - `page`: positive page number.
  - `count`: page size (default 10).
  - `order_by`: repeatable; accepted values are `created_at`, `deadline`, and
    the same names prefixed with `-`.
  - `search`: case-insensitive `OrderGroup.title` substring, or exact numeric
    `Order.code` when the value is numeric.
  - `commander_id`: comma-separated commander user IDs.
  - `approval_status`: `pending`, `approved`, or `rejected`; invalid values are
    silently ignored by the backend.
  - `is_completed`: recognized true/false spellings include
    `true/false`, `1/0`, `yes/no`, and `y/n`.
  - `user_id`: comma-separated user IDs, but the outer ownership filter still
    limits results to the authenticated user.

Paginated response:

```json
{
  "total_pages": 1,
  "total_items": 1,
  "current_page": 1,
  "next_page": null,
  "previous_page": null,
  "results": []
}
```

Each `OrderSerializer` result contains:

```text
id, code, order_title, files, reason, rating, reminders, tags,
has_comment, created_at, days_until_deadline, commander_full_name,
details, performer_full_name, send_sms, approval_status, is_completed
```

There is no project relationship or project filter on `Order`/`OrderGroup`.
The MCP layer must not claim otherwise. It may add `active` (statuses 1 and 2)
and `all` (statuses 1 through 4) as explicit client-side aggregates.

### Task search

The list endpoint's server search covers title substrings and exact numeric
codes. It does not search details, commander, or performer names. The MCP layer
can safely add bounded, client-side fuzzy matching across fields already
returned by the list API (`order_title`, `details`, `code`, and names), while
returning candidates instead of choosing an ambiguous ID.

### Task details

- Endpoint: `GET /leadership/get-order-details/{order_id}/`
- Permission: the authenticated user must be the assignee or commander.
- Serializer: `OrderDetailsSerializer`.
- Response: all list fields plus `comments`. The `tags` array includes status,
  importance, and urgency. `days_until_deadline` is the stored Jalali deadline
  string for this detail response.

### Tasks eligible in the frontend performance selector

- Endpoint: `GET /leadership/orders/for-activity/`
- Result: `id`, `code`, and `title`.
- It only returns the authenticated user's non-deleted status-2
  (`in_progress`) orders and requires the `GET_ORDERS` action permission.

## Performance/work records (`functionality.Activities`)

### Allowed performance subjects

- Endpoint: `GET /core/title_activity/`
- Permission: authenticated user.
- Optional query: `search` over subject ID/name.
- Response items: `id`, `name`.
- The queryset is filtered to public subjects and subjects allowed for the
  authenticated user's current job position(s). The MCP exposes this as a
  focused lookup so Codex can obtain a real `title_id` instead of guessing it.

### Create

- Endpoint: `POST /functionality/activities/`
- Parser: JSON only (`JSONParser`).
- Permissions: authenticated user and the `CREATE_ACTIVITY` role action.
- Serializer: `ActivitiesSerializer`.
- Required input fields:
  - `user_id`: integer user ID. The serializer requires it, but the view saves
    the row with `request.user`, so the authenticated user is authoritative.
  - `date`: Jalali `YYYY-MM-DD` (`JDateField`).
  - `start_time`: time string accepted by DRF, conventionally `HH:MM`.
  - `end_time`: time string, later than `start_time` for a useful record.
  - `title_id`: `TitleActivity` primary key (the performance subject).
  - `summary`: activity description.
- Optional input fields:
  - `order_id`: assigned `Order` primary key.
  - `customer_id`, `product_id`.
  - `importance_id`, `urgency_id`; model defaults are ID 1 when omitted.
- Read-only fields include nested title/customer/product/priority data, files,
  `formatted_time`, `order_code`, and `order_title`.

The view computes `time` as decimal hours from the submitted start/end minutes
and ignores any caller-supplied `time` value.

### Validation and ownership

- The linked order must belong to the same user and must not be soft-deleted.
- A record cannot overlap another non-deleted activity for the same user/date.
- The model rejects an end time earlier than the start time.
- `ActivitySettings.max_days_allowed` limits how far in the past a record may be
  entered (default 3 days). The current code does not reject future dates.
- Foreign-key IDs are validated by the serializer.
- The global DRF error handler normally reduces errors to
  `{"message": "..."}`.
- The API exposes no idempotency key or equivalent creation mechanism.
  Therefore the MCP client must never transport-retry this POST.

### List, retrieve, edit, and delete

- Monthly list: `GET /functionality/activities/?year={jalaliYear}&month={1-12}`
  with `page`, `search`, repeatable `importance`, and repeatable `urgency`.
  Search fields include summary, performance title, order code, and order title.
- Date-range list: `GET /functionality/get_activities/?start_date={jalali}&end_date={jalali}`.
- Retrieve/update: `GET|PUT|PATCH /functionality/activity/{activity_id}/`.
- Soft delete: `DELETE /functionality/activities/{activity_id}/`, restricted to
  the authenticated user's record.

The MCP exposes create, the date-range list, partial update, and soft delete.

Notes verified against the backend source and the live API:

- The date-range list accepts Jalali `YYYY-MM-DD` dates, is not paginated, and
  returns `order_code`/`order_title` but not the order ID.
- Update runs the same serializer validation as create, including the overlap
  check, which reads `date`, `start_time`, and `end_time` even on `PATCH`. The
  MCP therefore always sends those three fields and sends other fields only
  when they change. Omitting `order_id` keeps the existing task link.
- The past-date limit is readable at
  `GET /functionality/activity-settings/max-day` (`{"max_days_allowed": n}`).

### Task status update

- Endpoint: `PATCH /leadership/order-partial-update/{order_id}/`
- Permission: `UPDATE_ORDERS`; `status` and `non_completion_reason` may only be
  changed by the assignee.
- Body used by the frontend board: `{"status": <1-4>}`, optionally with
  `non_completion_reason`.
- A status change writes `OrderStatusHistory` and publishes a notification to
  the assignee and the commander.

## Date and duration behavior

- Business dates use Jalali fields and frontend requests use Jalali
  `YYYY-M-D`/`YYYY-MM-DD` strings.
- Django timezone is `Asia/Tehran` with `USE_TZ=True`.
- Task deadlines and task timestamps use django-jalali fields.
- Performance duration is derived from same-day start/end wall-clock times and
  stored as decimal hours. It is not accepted as a standalone creation input.
- The MCP layer therefore centralizes Gregorian-to-Jalali conversion and
  start/end/duration validation. A duration without either a start or end time
  is ambiguous and must not be converted into a fabricated time range.

## Frontend observations

- Login, current-user, task list, and task detail calls match the backend routes
  documented above and use the `JWT` authorization prefix.
- The current performance form submits date, start/end, `title_id`, summary,
  priorities, and optional customer/product fields.
- That frontend helper still builds `multipart/form-data` and does not include
  `order_id`, while the current backend view declares `JSONParser` and supports
  `order_id`. The backend source is authoritative for this MCP integration; the
  MCP client sends JSON and explicitly includes the verified order ID.

## MCP implementation implications

- Credentials remain local environment variables and tokens remain in memory.
- Access tokens are reused, then refreshed; login is the fallback when refresh
  is absent or rejected.
- Authenticated GETs may retry once after refresh/re-login.
- The performance POST is never automatically retried.
- Task search returns scored candidates and an ambiguity flag; it never returns
  a fabricated/resolved ID outside those candidates.
- Performance creation verifies the exact task through the detail endpoint and
  gets `user_id` from the current-user endpoint before submitting.
