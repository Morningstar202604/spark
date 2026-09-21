# Requirements Document

## Introduction

Spark is a local terminal coding agent. A developer runs `spark` in a repository, describes a task in natural language, and Spark performs an agent loop: call a model, execute tools (read/write files, run shell, MCP), apply an approval policy, persist the session, and stream results in a TUI. Spark uses user-supplied model endpoints. Spark does not require ChatGPT login, vendor plans, or cloud agent services.

## Glossary

- **Spark**: The Spark CLI and TUI process
- **Workdir**: The workspace root directory passed via `--workdir` or the current working directory
- **Agent Loop**: The cycle of model inference, optional tool execution, and re-inference until a final assistant message
- **Provider**: A model backend adapter (OpenAI-compatible HTTP, Ollama, or mock)
- **Approval Mode**: One of `suggest`, `auto-edit`, `full-auto`
- **Session**: A persisted conversation identified by a session id in the local SQLite store
- **AGENTS.md**: Optional project instruction file at the Workdir root
- **MCP Server**: An external stdio process that exposes tools through the Model Context Protocol
- **User API Key**: A secret supplied by the developer via `SPARK_API_KEY` or the env name in config

## Requirements

### Requirement 1: Local Agent Loop

**User Story:** AS a developer, I want Spark to iterate between model reasoning and tool use in my terminal, so that a coding task can be completed without a vendor agent product.

#### Acceptance Criteria

1. WHEN the developer submits a user message, Spark SHALL start an Agent Loop that sends conversation context and tool schemas to the configured Provider
2. WHEN the Provider returns one or more tool calls, Spark SHALL execute allowed tools, append tool results to the conversation, and request the Provider again
3. WHEN the Provider returns a final assistant message with no tool calls, Spark SHALL stream that message to the TUI and end the current turn
4. IF the number of tool rounds in the current turn reaches `max_tool_rounds`, Spark SHALL stop the loop and display a bounded error in the TUI
5. WHILE a turn is running, Spark SHALL accept a cancel action that stops further Provider requests and in-flight shell commands for that turn

### Requirement 2: Built-in Coding Tools

**User Story:** AS a developer, I want Spark to read and change files and run commands inside my Workdir, so that the agent can implement code locally.

#### Acceptance Criteria

1. WHEN the model calls `read_file` with a path inside Workdir, Spark SHALL return file content with optional line offset and limit
2. WHEN the model calls `list_dir` with a path inside Workdir, Spark SHALL return a bounded directory listing
3. WHEN the model calls `write_file` or `apply_patch` for a path inside Workdir and the Approval Mode allows the write, Spark SHALL apply the change and return a success payload
4. WHEN the model calls `apply_patch` and `old_text` matches zero times or more than one time, Spark SHALL leave the file unchanged and return an error payload
5. IF a tool path resolves outside Workdir, Spark SHALL reject the call and return an error payload
6. WHEN the model calls `run_shell` and the Approval Mode allows the command, Spark SHALL run the command with cwd inside Workdir, capture stdout, stderr, and exit code, and truncate output to a configured bound
7. IF a shell command exceeds `shell_timeout_sec`, Spark SHALL terminate the process and return a timeout error payload

### Requirement 3: Approval Modes

**User Story:** AS a developer, I want to choose how much Spark may do without asking me, so that I keep control of writes and commands.

#### Acceptance Criteria

1. WHEN Approval Mode is `suggest` and the model requests a write tool or `run_shell`, Spark SHALL show an approval prompt and wait for Allow or Deny before executing
2. WHEN Approval Mode is `auto-edit` and the model requests a write tool, Spark SHALL execute the write without an approval prompt
3. WHEN Approval Mode is `auto-edit` and the model requests `run_shell`, Spark SHALL show an approval prompt and wait for Allow or Deny
4. WHEN Approval Mode is `full-auto` and the tool path or command cwd is inside Workdir, Spark SHALL execute read, write, and shell tools without an approval prompt
5. WHEN the developer Denies an approval prompt, Spark SHALL skip that tool, return a denial payload to the model, and continue the Agent Loop
6. WHEN the developer selects Allow-always for a tool name in the current session, Spark SHALL auto-allow later calls of that tool name for the remainder of the session

### Requirement 4: Pluggable Model Providers

**User Story:** AS a developer, I want to point Spark at my own OpenAI-compatible endpoint or local Ollama, so that I can use my own keys and models.

#### Acceptance Criteria

1. WHEN config `provider.name` is `openai_compat`, Spark SHALL send streaming Chat Completions requests to `base_url` using the model name from config or CLI
2. WHEN config `provider.name` is `ollama`, Spark SHALL send Chat Completions-shaped requests to the configured Ollama base URL
3. WHEN config `provider.name` is `mock`, Spark SHALL run the Agent Loop with scripted tool calls and messages and SHALL skip outbound model HTTP
4. WHEN `api_key_env` is set, Spark SHALL read the User API Key from that environment variable
5. IF the User API Key is missing for `openai_compat`, Spark SHALL exit startup with a configuration error that names the expected environment variable
6. Spark SHALL load provider settings from `--config`, then `./.spark.toml`, then `~/.spark/config.toml` in that order of precedence

### Requirement 5: Terminal TUI

**User Story:** AS a developer, I want a terminal UI for conversation, streaming output, diffs, and approvals, so that I can work without leaving the terminal.

#### Acceptance Criteria

1. WHEN the developer runs `spark` with no subcommand, Spark SHALL open the TUI bound to Workdir
2. WHEN the Provider streams text, Spark SHALL append that text to the conversation pane as it arrives
3. WHEN a tool executes, Spark SHALL show a tool card with tool name, argument summary, and a truncated result
4. WHEN a write tool requires approval, Spark SHALL display a diff or content summary in the approval layer
5. WHEN the developer presses the configured cancel shortcut, Spark SHALL abort the in-progress turn
6. WHILE the TUI is running, Spark SHALL display the current model name and Approval Mode in the status area

### Requirement 6: Session Memory

**User Story:** AS a developer, I want Spark to save and restore conversations, so that I can continue a task later.

#### Acceptance Criteria

1. WHEN a turn produces user, assistant, or tool messages, Spark SHALL persist those records to the local SQLite store
2. WHEN the developer runs `spark sessions`, Spark SHALL list session id, Workdir, updated time, and title
3. WHEN the developer runs `spark resume SESSION_ID`, Spark SHALL open the TUI with that session history loaded into Context
4. IF `SESSION_ID` does not exist, Spark SHALL exit with a configuration error
5. WHEN Workdir contains `AGENTS.md`, Spark SHALL inject a truncated copy of that file into Context for every Provider request in that session

### Requirement 7: MCP Tools

**User Story:** AS a developer, I want Spark to load tools from MCP servers I configure, so that I can extend the agent without changing the kernel.

#### Acceptance Criteria

1. WHEN config lists MCP servers, Spark SHALL start each server as a stdio process during session start
2. WHEN an MCP server returns `tools/list`, Spark SHALL register each tool under a namespaced function name `mcp__{server}__{tool}`
3. WHEN the model calls a namespaced MCP tool and Approval Mode allows the call, Spark SHALL forward `tools/call` to that server and return the result to the Agent Loop
4. IF an MCP server exits or handshake fails, Spark SHALL drop that server's tools from the registry and show a bounded error in the TUI
5. WHEN an MCP tool has no readonly declaration, Spark SHALL treat the call with the same approval rules as `run_shell`

### Requirement 8: Safety and Local Control

**User Story:** AS a developer, I want Spark to stay inside my Workdir and keep secrets under my control, so that the agent remains a local tool I own.

#### Acceptance Criteria

1. WHEN Spark resolves tool paths, Spark SHALL reject paths that leave Workdir after normalization
2. WHEN Spark writes configuration templates, Spark SHALL write environment variable placeholders and omit secret values
3. WHEN Spark starts, Spark SHALL read model credentials only from user-facing variables such as `SPARK_API_KEY`
4. IF `spark exec` is invoked with Approval Mode `suggest`, Spark SHALL exit with a configuration error because non-interactive execution cannot collect approvals
