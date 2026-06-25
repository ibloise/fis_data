# AGENTS.md

## Scope

These instructions apply to the whole repository.

## Data Access

- Do not open, read, list, summarize, sample, parse, or otherwise inspect files under `data/` unless the user gives explicit authorization in the current conversation.
- Treat any similarly named data dump, export, or raw dataset directory as restricted unless the user clearly authorizes access.
- If a task appears to require data access, ask for explicit permission before touching those files.
- Do not open, query, inspect, dump, sample, or otherwise read the real SQLite database unless the user gives explicit authorization in the current conversation.
- It is allowed to create and read temporary dummy SQLite databases for tests, as long as they do not contain real project data.

## Existing Code

- `external/` contains local external reference repositories, including `microb_db`.
- Do not scan or inspect repositories under `external/` until the user asks for that specifically.

## Development Guidelines

- Work on a `codes/<feature>` branch by default and keep `main` clean.
- Before committing repository changes, ensure the current branch is named after the current work, such as `codes/<feature>` or `codes/fix-<issue>`, unless the user explicitly asks for another branch.
- Prefer small, focused changes that match the repository's existing structure.
- Use `rg` or `rg --files` for searches when available.
- Keep generated files, caches, and local environment files out of version control.
