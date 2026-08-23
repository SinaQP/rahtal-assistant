# Rahtal Assistant

A standalone personal MCP server that lets Codex work with the existing Rahtal
HTTP API. It does not import from or modify the company `rahtal-be` repository.

The verified API contract is documented in
[`docs/rahtal-api-discovery.md`](docs/rahtal-api-discovery.md).

## Setup

Create a dedicated virtual environment and install the isolated dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure:

```dotenv
RAHTAL_BASE_URL=https://your-rahtal-host/
RAHTAL_USERNAME=your_username
RAHTAL_PASSWORD=your_password
```

Tokens stay in process memory. Passwords and tokens are not included in HTTP
logs or MCP results.

## Codex registration

From this directory, register the server with Codex:

```powershell
codex.cmd mcp remove rahtal
codex.cmd mcp add rahtal --env "PYTHONPATH=D:\Coding\ZarProject\rahtal-assistant" -- "D:\Coding\ZarProject\rahtal-assistant\.venv\Scripts\python.exe" -m rahtal_assistant.mcp.server
```

Restart Codex Desktop after changing the registration. The server exposes:

- `rahtal_whoami`
- `rahtal_list_my_tasks`
- `rahtal_search_my_tasks`
- `rahtal_get_task`
- `rahtal_list_performance_titles`
- `rahtal_create_performance`

Performance creation requires a performance-subject ID. A task ID is optional:
when one is supplied, it must be an exact task ID; routine work such as a daily
meeting can be registered without a task. The write POST is deliberately never
retried after an uncertain failure. New performance records use Rahtal
importance and urgency ID `2` by default; callers may supply different verified
IDs when needed.

## Tests

The default suite is mocked and performs no live requests or writes:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests\rahtal_assistant -v
```

Read-only smoke checks require `RAHTAL_SMOKE_READ_ONLY=true`. Write smoke checks
also require `RAHTAL_SMOKE_ALLOW_WRITE=true`, a non-production environment, and
explicit task, title, start-time, and end-time values. Never enable the write
gate against production merely to test the integration.
