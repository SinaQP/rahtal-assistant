# Rahtal MCP assistant

- This is a standalone personal integration. Do not add its implementation,
  dependencies, configuration, tests, or documentation to `rahtal-be`.
- Treat `rahtal-be` as a remote HTTP API and as read-only discovery material.
- Resolve natural task references with `rahtal_search_my_tasks`; never guess a
  Rahtal entity ID. Ask the user when multiple candidates are plausible.
- A performance record may omit its task. Link it only when the user supplies
  or clearly refers to a task; routine work such as a daily meeting does not
  require a task reference.
- For Rahtal tasks clearly identified as «زرامد سیرجان», always include
  customer «شهرداری سیرجان» (ID 8) and product «زرامد» (ID 10162) in the
  performance record, unless the user says otherwise.
- Never expose Rahtal passwords, access tokens, refresh tokens, or authorization
  headers. Never automatically repeat `rahtal_create_performance` after an
  uncertain failure.
