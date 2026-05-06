Fixes #1346

<!-- Add issue number above -->

#### Describe the changes you have made in this PR -

This PR adds support for running **any** `opensre` subcommand (not just hand-picked examples) through the interactive shell's `run_cli_command` action.

**Changes:**
- **intent_parser.py**: Add `cli_command` regex pattern detecting `deploy`, `guardrails`, `remote`, `doctor`, `onboard`, `uninstall`, or any `opensre <subcommand>` form
- **interaction_models.py**: Add `cli_command` to `PlannedAction.kind` Literal type
- **action_executor.py**: 
  - Add `_OPENSRE_BLOCKED_SUBCOMMANDS` (currently only `agent`)
  - Add `run_opensre_cli_command()` function executing via `python -m app.cli`
  - Pass `argv` list directly to subprocess (prevents shell injection)
- **action_planner.py**: Handle `cli_command` pattern (extracts subcommand from regex named group)
- **cli_agent.py**: Wire `run_cli_command` kind in `_execute_action_plan` and update `_ACTION_RULE` to advertise the new action
- **rendering.py**: Add `cli_command` label ("opensre") to `print_planned_actions`

### Demo/Screenshot for feature changes and bug fixes -
<!-- Include at least one proof of the change: UI screenshot, terminal screenshot/log snippet, short video/GIF, or equivalent demo output. -->
<!-- Do not add code diff here -->

![demo](https://private-user-images.githubusercontent.com/97684755/588211883-5ac55920-1c50-4e8f-8231-4f9a64355b94.png)

---

## Code Understanding and AI Usage

**Did you use AI assistance (ChatGPT, Copilot, etc.) to write any part of this code?**
- [ ] No, I wrote all the code myself
- [x] Yes, I used AI assistance (continue below)

**If you used AI assistance:**
- [x] I have reviewed every single line of the AI-generated code
- [x] I can explain the purpose and logic of each function/component I added
- [x] I have tested edge cases and understand how the code handles them
- [x] I have modified the AI output to follow this project's coding standards and conventions

**Explain your implementation approach:**
- **Problem**: The interactive shell's `_ACTION_RULE` listed only hardcoded examples (`/health`, `/doctor`, `/version`), contradicting the "any subcommand" contract.
- **Alternative approaches**: 
  1. Reuse existing slash command infrastructure (rejected - separate concerns)
  2. Add new action kind with dedicated executor (chosen)
- **Key functions/components**:
  - `_OPENSRE_BLOCKED_SUBCOMMANDS`: Single source of truth for blocked subcommands
  - `run_opensre_cli_command()`: Executes subcommands with blocked list enforcement + argv passing
  - Regex pattern: Detects natural language + explicit `opensre <subcmd>` form

---

## Checklist before requesting a review
- [x] I have added proper PR title and linked to the issue
- [x] I have performed a self-review of my code
- [x] **I can explain the purpose of every function, class, and logic block I added**
- [x] I understand why my changes work and have tested them thoroughly
- [x] I have considered potential edge cases and how my code handles them
- [x] If it is a core feature, I have added thorough tests
- [x] My code follows the project's style guidelines and conventions

---

Note: Please check **Allow edits from maintainers** if you would like us to assist in the PR.