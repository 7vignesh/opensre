## Summary

This PR adds support for running **any** `opensre` subcommand (not just hardcoded examples) through the interactive shell's `run_cli_command` action.

## Changes

- **intent_parser.py**: Add `cli_command` regex pattern detecting `deploy`, `guardrails`, `remote`, `doctor`, `onboard`, `uninstall`, or any `opensre <subcommand>` form
- **interaction_models.py**: Add `cli_command` to `PlannedAction.kind` Literal type
- **action_executor.py**: 
  - Add `_OPENSRE_BLOCKED_SUBCOMMANDS` (currently only `agent`)
  - Add `run_opensre_cli_command()` function that executes subcommands via `python -m app.cli`
  - Pass `argv` list directly to subprocess (prevents shell injection)

## Security

- Blocked subcommand list enforced at execution time
- Uses `argv` list instead of string construction to prevent shell metacharacter injection

## Testing

- Unit tests cover natural language to subcommand mapping

Fixes #1346