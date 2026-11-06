# Code walkthrough

Think of the model as a researcher and the harness as its workbench. The researcher proposes an action; the workbench decides whether that action is permitted, executes it, keeps the evidence, and returns the result. The model never receives a Python function and executes it directly.

This guide covers the new Agent Foundry core and its small Fieldnote command-line example. The original Fieldnote browser, PDF/embedding pipeline, graph explorer and detailed citation reviewer are separate; they are not implemented by this core. Start with the request trace, then read `types → cli → runner → registry → memory/context → extensions`. The function inventory below includes private helpers, nested callbacks and tests.

In VS Code, open this project folder and press F5 to choose an offline demo preset. Put breakpoints in `Runner._drive`, `ContextBuilder.build`, `Model.respond`'s implementation and a tool handler; step through one request and watch `state`, `messages`, `pending_index` and `working`. Use the Testing panel or the provided test task for targeted software checks. These presets require the project environment and Python/debugger extensions; they do not submit questions to a paid provider.

## Follow one question

Suppose you ask: “How many readings were valid, and what does valid mean?”

1. `cli.main → execute` reads configuration, opens `.foundry/state.sqlite`, and builds the application. A configured provider performs real inference; `foundry demo` uses a deterministic driver instead.
2. `application` registers native tools, the Fieldnote plugin's SQL/report/calculation tools, and a tool for loading approved instructions. It gives `Runner` a workspace, an explicit scope, instructions and limits.
3. `Runner.run` creates a new run ID and fresh working state. `ContextBuilder.build` assembles instructions, recent completed turns, relevant saved memory, the question and completed tool exchanges.
4. `Model.respond` returns text or structured requests such as `query_observations({"sql": "SELECT COUNT(*) ..."})`. These are proposals, not executable code supplied by the model.
5. `Runner._drive` validates each request's schema, capability and hooks; an approved handler executes it. The SQL handler additionally constrains SQLite to approved read-only tables and functions.
6. The complete result is stored as an artifact. A bounded observation, containing the result or an explicit excerpt plus its artifact ID, enters the next model request.
7. The model can request `search_reports` for the quality protocol, load the research skill, retrieve another artifact slice, or answer. A calculation tool can read numeric cells directly from this run's SQL artifacts.
8. Output/lifecycle hooks run before a completed turn becomes conversational history. The caller receives status, answer, public trace, reported usage, working state and any pending approval.

```text
new run → while time remains:
    while an earlier model reply still has pending tool calls:
        validate → authorize → hook → approve side effect if required
        execute or reuse read-only result → store artifact → append observation
    stop if model/tool budget is exhausted
    build bounded context → call model → record reported usage
    if answer without tool calls: finish
    otherwise: save its tool-call batch and continue
release checks → save completed turn → return
```

The inner loop completes a whole tool-call batch before another model request. Tools run sequentially here. A denied/invalid call gets an error observation and consumes its attempted-call budget. A side effect can pause the run for exact-action approval; resuming that pause restores its working state. An ordinary new question starts fresh working state and can retrieve completed conversation history instead.

## State and scope: what belongs to whom?

| State | Owner and lifetime | How it reaches the model |
| --- | --- | --- |
| Context | `ContextBuilder`, rebuilt before each inference | The actual message list and tool definitions |
| Working memory | One run: public notes, loaded skills, artifact IDs, tool exchanges | Notes as data; approved loaded skills as instructions |
| Completed history | SQLite `turns`, one namespaced conversation | Up to four completed question/answer pairs, subject to compaction |
| Long-term memory | SQLite `memories`, explicit project-scope facts/preferences | Lexical retrieval; expiry/deletion; labeled untrusted data |
| Evidence artifacts | SQLite `artifacts`, tied to a run | Bounded observations and `read_artifact` paging |
| Run checkpoints/events | SQLite `runs` and `events` | Explicit approval resume and local inspection; not automatic crash replay |

For example, workspace `/work/fieldnote`, scope `research-A`, session `chat-1` becomes:

```python
memory_scope = sha256(json([resolved_workspace, scope]))
history_key = sha256(json([resolved_workspace, scope, session_id]))
```

Changing the session separates history but still allows the same project's saved preferences. Changing the workspace or explicit scope separates both. Hashes are deterministic namespace labels, not encryption or authenticated user identities. The application chooses scope; the model cannot select another scope through a memory tool. SQLite is local mutable storage, not immutable audit storage.

## Core contracts and entry points

### [types.py](../src/agent_foundry/types.py)

These dataclasses are the envelopes passed between components, not additional model intelligence. Defaults use fresh lists/dictionaries per instance.

- `ToolCall`: model-requested call ID, tool name and argument object; consumed by the runner and matched to one observation.
- `ModelReply`: visible text, tool calls and reported usage; produced by a model adapter and consumed by the runner.
- `Model.respond(messages, tools, timeout, max_output_tokens)`: async protocol every model adapter implements; returns `ModelReply` without exposing provider-specific internals to the runner.
- `ToolContext`: host-created workspace/run/session identifiers, shared public working state and services; supplied to each handler, never granted by model text.
- `ToolResult`: arbitrary JSON-compatible content plus provenance metadata; the runner accepts plain handler output by wrapping it in this envelope.
- `Tool`: action name/description/schema/handler plus capability, side-effect flag, timeout, provenance and cacheability. `Tool.definition()` converts its public schema to the provider's function-tool format, omitting the Python handler.
- `RunConfig`: default limits are 8 model steps, 16 tool attempts, 120 seconds, 2,000 output tokens per request, 32,000 context characters and 6,000 observation characters; default capabilities exclude durable writes, MCP calls and code execution. `reasoning_mode` is `off` unless `plan-review` is explicitly selected.
- `RunResult`: caller-facing run/session IDs, terminal or paused status, answer, trace, usage, pending approvals and public working memory.

### [cli.py](../src/agent_foundry/cli.py)

- `DemoModel.__init__(reasoning_mode)` initializes its fixed step counter and chosen workflow; the CLI constructs it only for the zero-API demo.
- `DemoModel.respond(...)` requests the real count query, then report search, then formats their actual observations; plan-review mode additionally records a plan and reviews that exact draft. It is a wiring test, not language inference or an accuracy benchmark.
- `environment(root)` merges local `.env` values with the process environment; existing process variables win.
- `application(root, model, store, config)` imports the host-approved Fieldnote adapter, builds a registry/plugin/skill catalog and returns its configured runner. This is where domain logic connects to the reusable core.
- `application.load_skill(ctx, args)` resolves an approved skill name, stores its text in this run's `working.skills`, and returns instructions with trusted-local metadata; later model requests include those instructions.
- `parser()` defines `demo`, `chat`, `resume`, `trace` and explicit `memory add/search/forget` commands, with session/capability/action-ID arguments where appropriate; `--reasoning` selects the optional public plan/review workflow.
- `execute(args)` opens state, handles trace/manual-memory operations, selects scripted or configured inference plus reasoning mode, builds the application and runs/resumes it; it closes the remote SDK client afterward. `REASONING_SCAFFOLD` supplies the mode if omitted on the command line; manual CLI memory commands are explicit host actions rather than model-requested approvals.
- `main()` parses arguments, runs the async entry point, converts expected configuration errors to readable CLI exits, and prints the result as JSON.
- Module constants/imports: `ROOT` locates the checkout; the module's `__main__` branch invokes `main`. This CLI is not an HTTP server or browser UI.

### [models.py](../src/agent_foundry/models.py)

- `ScriptedModel.__init__(replies)` copies supplied fixtures and creates a request log; tests and demonstrations own the fixtures.
- `ScriptedModel.respond(...)` records the request and returns the next fixture, failing if exhausted. It makes no network request or intelligent decision.
- `OpenAICompatibleModel.__init__(base_url, api_key, model, reasoning_effort)` creates an async SDK client with automatic retries disabled; callers own endpoint configuration and client cleanup.
- `OpenAICompatibleModel.respond(...)` uses Chat Completions to send messages/tool schemas, parses tool-call JSON, maps malformed arguments to an invalid marker, and normalizes visible text/calls/usage. Optional reasoning effort is passed through only when configured; newer/provider-specific reasoning and tool protocols may require another adapter. Compatibility requires live verification; hidden reasoning is not collected.

### [registry.py](../src/agent_foundry/registry.py)

- `ToolRegistry.__init__()` creates the host-owned name-to-tool mapping.
- `register(tool)` checks names, collisions, object-shaped JSON schemas, callability and timeout bounds, then publishes one tool; loaders and applications call it.
- `names()` returns registered names; extension collision checks and tests use this small inventory.
- `register_many(tools)` validates a candidate registry first and replaces the mapping only if the entire batch succeeds, preventing partially published bundles.
- `get(name)` returns the named tool or rejects an unknown action; the runner calls it even if the model requested a name that was never offered.
- `list()` returns registered tool objects for host inspection and schema/fingerprint construction.
- `definitions(capabilities)` returns only public definitions whose capability is allowed; this reduces model choices but does not replace execution-time checks.
- `validate(tool, arguments)` rejects non-JSON/non-finite data and validates argument types/required fields against that tool's schema.
- `fingerprint()` hashes tool contracts and handler bindings for approval-resume checks; nested `stable(value, seen)` canonically normalizes code attributes, recursively nested code/constants, bytes, common values, paths, defaults, closures and selected object configuration while handling cycles. This hashes semantic code content rather than `marshal` reference flags that can change after normal execution.
- Fingerprints detect many code/configuration changes, not every possible external state change. Mutable objects captured in a closure can conservatively invalidate resume; keep changing execution state in `ToolContext` instead.

## The execution engine

### [runner.py](../src/agent_foundry/runner.py)

- `Runner.__init__(...)` binds model, registry, store, resolved workspace, policies, instructions, scope and declared secrets; it validates limits/mode and constructs the context builder. Plan-review mode uses a private registry copy with two scaffold tools, leaving the caller's registry unchanged.
- `_signature()` hashes core source, registry fingerprint, configuration, workspace, scope and instructions; `run` saves it and `resume` compares it before accepting old approvals.
- `_memory_scope()` computes the workspace/scope namespace; `_session_key(session_id)` additionally includes the conversation ID. The runner uses these consistently for memory retrieval and completed history.
- `_event(state, kind, **data)` redacts an event, atomically appends it to SQLite and adds its sequence number to the in-memory trace.
- `_save(state)` persists a redacted checkpoint through the store; checkpoints hold public execution state, not private chain-of-thought.
- `_action_id(state, tool, call)` hashes run, call ID, tool, capability, arguments and saved signature so approval applies to that exact action rather than every future use of the tool.
- `run(question, session_id, approval)` validates user inputs, creates fresh IDs/counters/cache/working state and enters `_drive`; it never reuses a stored final answer.
- `resume(run_id, approved_action_ids, approval)` loads only an approval-paused run, refuses changed runtime/configuration, records resume and re-enters `_drive` with exact approved IDs.
- `_drive(state, ...)` owns the nested loops in the pseudocode above: attempts, checks, execution, context, inference, stopping, errors and release. It clips only the memory-search query to 5,000 characters; the original valid question remains in model context. Model usage is summed from adapter reports; there is no built-in dollar-price calculation.
- `_drive.remaining()` calculates active runtime left from a monotonic deadline; approval-paused waiting does not consume the saved active budget.
- `_drive.hook(phase, data)` checks remaining time and awaits configured callbacks under that deadline; callbacks remain trusted Python, and blocking synchronous code is not forcibly preempted.
- `_drive.checkpoint()` saves remaining active time and current state; tool batches are persisted before execution and between observations.
- `_drive.observe(call, content)` appends one redacted JSON tool response with its matching call ID, preserving the provider's tool-call/result protocol.
- `_drive.invoke()` awaits an async handler or runs a synchronous handler in a worker thread, then awaits a returned awaitable if necessary. A thread timeout cannot undo a host side effect.
- `_finish(state, status, answer, pending)` persists final/paused state and builds `RunResult`; approval/reconciliation early returns use this too.

The pending batch stores `pending_index` so an approved action resumes at the correct point. Identical cacheable read-only calls reuse their observation within the run; side effects and mutable operations are not cached. If a started side effect has an uncertain timeout/error, status becomes `reconciliation_required`: inspect the external outcome before retrying. There is no general transactional exactly-once guarantee.

Statuses are explicit: `completed`, `awaiting_approval`, `budget_exhausted`, `deadline_exceeded`, `context_limit`, `blocked`, `reconciliation_required`, `cancelled` or `error`. Only a nonempty completed answer that passes final release checks is saved as a conversation turn. Cancellation is persisted and re-raised to the caller.

### [guards.py](../src/agent_foundry/guards.py)

- `Decision` represents allow/deny and a public reason; `GuardBlocked` is the exception the runner catches to stop release/execution.
- `RegexGuard.__init__(pattern, phases, label)` compiles a case-insensitive pattern and selects when to apply it; `__call__(phase, event)` searches serialized event content and returns a decision.
- `Hooks.__init__(callbacks)` copies the callback list; `add(callback)` appends a host policy callback.
- `Hooks.check(phase, event)` accepts only known lifecycle phases, gives each callback a deep-copied snapshot, awaits async callbacks and denies on `False`, denied `Decision`, or an exception. Hooks inspect rather than silently rewrite authorized inputs.
- `Redactor.__init__(secrets)` records explicitly declared nonempty secret strings; `text(value)` replaces exact occurrences, and `value(value)` recursively redacts strings and dictionary keys in JSON-like data.

`input → before_model → after_model → before_tool → after_tool → output → after_run` are interception points, not a semantic truth checker. Exact secret redaction cannot discover all unknown credentials or encoded secrets. Regex checks cannot prove a citation supports a claim. Schemas establish shape; capability gates establish permission; neither establishes answer correctness. The new core has no automatic claim-entailment reviewer, comprehensive prompt-injection defense or trained verifier.

`Hooks()` starts with an empty callback list, including in the default CLI application. `RegexGuard` and custom content filters are available extension points, not policies silently installed by default. Schema/capability checks, path constraints and runtime limits are built into the execution path independently of those optional callbacks.

## Memory and context management

### [memory.py](../src/agent_foundry/memory.py)

- `_json(value)` serializes deterministic Unicode JSON while rejecting NaN/infinity; storage methods use it before writes.
- `_label(value, name, maximum)` validates nonempty bounded identifiers/text; `_utc(timestamp)` renders storage timestamps in UTC for returned records.
- `MemoryStore.__init__(path)` creates parent/state tables and indexes, then applies file mode `0600`; the store contains turns, memories, artifacts, runs and events.
- `_connect()` is a context manager that opens a fresh SQLite connection, commits success, rolls back failure and always closes it; all store operations use it.
- `add_turn(session_id, question, answer)` appends one completed conversation pair; the runner calls it only after successful release checks.
- `history(session_id, limit)` fetches the latest bounded number of pairs and returns them chronologically; the supplied session value is already namespaced by the runner.
- `remember(scope, key, text, source, ttl_seconds)` explicitly inserts/updates one fact while preserving its identity, provenance and optional expiry; it validates TTL/timestamp bounds before writing.
- `_memory_record(row)` converts a database row to a public dictionary with readable UTC timestamps; remember/search share it.
- `search(scope, query, limit)` filters scope and expiry, then ranks literal token overlap, weighting matches in keys more highly than text. It does not embed facts, infer facts from chat or delete expired rows during reads.
- `forget(scope, key)` deletes only the matching scoped record and returns whether one existed.
- `put_artifact(run_id, content, metadata)` stores immutable-by-interface serialized content under a digest of run/content/metadata; identical evidence in the same run has the same artifact ID.
- `get_artifact(identifier)` returns stored content/metadata/provenance or raises for an unknown ID; the calling tool additionally checks run ownership.
- `save_run(run_id, payload)` inserts/replaces a JSON checkpoint; `load_run(run_id)` returns a newly decoded copy or raises for an unknown run.
- `append_event(run_id, event)` reserves the next sequence and inserts the event in one immediate transaction, allowing concurrent writers without duplicate sequence numbers.
- `events(run_id)` returns that run's events in sequence order for CLI/debug inspection.

### [context.py](../src/agent_foundry/context.py)

- `ContextLimitError` signals that required context cannot safely fit; the runner converts it to an explicit stop rather than truncating instructions or intent.
- `_json(value)` provides stable strict serialization for size measurements and observation envelopes.
- `preview(content, artifact_id, limit)` returns either full content or an explicitly truncated string excerpt plus a full-artifact pointer; binary search fits the serialized envelope within its character bound.
- `_tool_groups(messages)` validates complete assistant-call/tool-result batches and runtime notes, rejecting missing/duplicate/orphan responses and privileged-role injection into this history.
- `_artifact_refs(group)` extracts only named artifact IDs from tool observations; it does not turn removed excerpts into remembered facts.
- `ContextBuilder.__init__(char_budget, observation_limit)` validates the two character limits; `ContextBuilder.preview(...)` applies the configured observation limit to the module helper.
- `build(instructions, question, history, memories, tool_messages, tool_definitions, working_memory)` copies/validates inputs, assembles messages and drops whole old history pairs, then low-ranked memories, then old complete tool groups until they fit. It preserves mandatory instructions/question/working notes/tool definitions and the latest tool exchange, or fails explicitly.
- `build.assemble(...)` builds messages in trusted-instructions/data/history/question/tool order and adds a compaction notice with recoverable artifact pointers.
- `build.size(messages)` measures serialized messages plus tool definitions; returned stats report removed groups and characters, with token estimates deliberately unset.

This is bounded context selection, not an LLM summarizer or tokenizer-exact budget. Saved memory remains labeled data; only host-approved loaded skills are promoted to extra instructions by the runner.

### [builtin_tools.py](../src/agent_foundry/builtin_tools.py)

- `scoped_path(workspace, relative)` resolves an approved relative path while rejecting parent traversal, escaping symlinks, secret/state directories and private-key suffixes; file tools share it.
- `native_tools()` builds the seven tool contracts below; its small `obj`/`string` lambdas construct strict object/string schemas, not runtime actions.
- `native_tools.listing(ctx, args)` returns at most 100 directory entries, excluding denied names; registered as `list_files` with `files.read`.
- `native_tools.read(ctx, args)` reads a UTF-8 text file below 250 KB with its path/provenance; registered as `read_file` with `files.read`.
- `native_tools.artifact(ctx, args)` checks current-run ownership and returns a requested JSON-text slice plus `has_more`; registered as `read_artifact`.
- `native_tools.working(ctx, args)` updates a public note in current working state; `working_note` is run-local and explicitly non-cacheable.
- `native_tools.search_memory(ctx, args)` searches the host-supplied memory scope; `search_memory` never accepts an arbitrary scope from model arguments.
- `native_tools.remember(ctx, args)` saves a scoped record with an approved-run source and optional TTL; `remember` requires `memory.write` and exact-action approval.
- `native_tools.forget(ctx, args)` deletes a record in that same host-supplied scope; `forget_memory` also requires approved `memory.write`.

## Extensions: different concepts with a shared boundary

A **tool** is an action. A **skill** is instructions. A **plugin** bundles approved tools/resources. **MCP** is a protocol for reaching tools in another process/service. A **sandbox** restricts execution. None implies the others: an MCP server is not automatically isolated, and a skill cannot grant a capability.

### [extensions.py](../src/agent_foundry/extensions.py)

- `_inside(root, value)` resolves an extension path within its approved root, rejecting traversal and symlink components; skill/plugin readers use it.
- `_read(path, maximum)` reads bounded UTF-8 text only from an existing file; default maximum is 64 KiB.
- `_register(registry, tools)` checks intra-bundle/existing collisions and uses atomic batch registration where supported; plugins and MCP bridges share it.
- `Skills.__init__(root)` binds an existing approved catalog directory.
- `Skills.list()` discovers at most 100 Markdown resources, derives unique portable names, reads a limited description/front-matter subset, and returns path/hash metadata. Discovery does not inject full skill text into model context.
- `Skills.load(name)` validates a requested catalog name and returns only its approved bounded Markdown; the application decides how to use it.
- `PluginLoader.__init__(root)` establishes the host-approved manifest root.
- `PluginLoader.load(path, registry, tool_factories)` validates manifest version/fields/resource paths, selects only host-supplied factories, rejects permission/import overrides, annotates provenance and publishes the bundle. A plugin cannot dynamically import arbitrary Python; its approved factory code remains trusted host code.
- `_has_reserved_options(value)` recursively rejects options attempting to set permissions, handlers, commands, environment or imports; the plugin loader calls it before factory execution.

### [mcp_adapter.py](../src/agent_foundry/mcp_adapter.py)

- `MCPConfig` holds server namespace, explicit tool allowlist, either URL or stdio command, approved launch settings, explicit environment and timeout. `__post_init__()` rejects ambiguous transports, unsafe names/credentials, nonlocal plain HTTP and unapproved/nonabsolute local launches.
- `MCPBridge.__init__(config)` stores configuration with no connection yet; the application owns the bridge's lifetime.
- `__aenter__()` opens the MCP SDK2 client via an async exit stack and cleans up if initialization fails; `__aexit__(...)` closes it and removes the usable client reference.
- `_environment()` supplies a minimal PATH plus explicit configured values instead of forwarding the full host environment; the SDK may add its small documented OS allowlist.
- `tools()` discovers bounded tool pages, rejects duplicates/missing allowlisted tools, checks schemas and constructs ordinary `Tool` objects named `mcp__SERVER__TOOL`.
- `tools.call(context, arguments, remote_name)` is each generated async handler: it calls the captured remote tool, bounds text/structured output, omits unsupported media payloads and returns explicitly untrusted external observations.
- `register(registry)` prepares all adapters, publishes them through the shared registry, and returns local names.
- `_check_local_schema(schema)` recursively rejects nonlocal `$ref` values, preventing third-party schemas from initiating remote reference resolution during validation.

Every adapted MCP action conservatively requires `mcp.call`, is marked side-effectful and is not cached; runner approval still applies. Server descriptions/observations cannot self-grant permission. Local stdio means an explicitly trusted host process, not a container. Real local protocol tests/demo exist; hosted-server interoperability remains unverified.

### [sandbox.py](../src/agent_foundry/sandbox.py)

- `SandboxUnavailable` makes missing execution infrastructure explicit; there is no host-Python fallback.
- `DockerSandbox.__init__(image, docker, limits...)` validates trusted image syntax and bounded time/memory/CPU/output settings.
- `available` is a property checking whether the Docker executable exists; it does not prove a usable daemon or image.
- `build_command(snapshot, name)` builds an argument list with no-network, read-only root, dropped capabilities, nonroot identity, resource limits, temporary writable directories and only the supplied read-only input mount.
- `run_python(code, input_files)` validates code/files, creates a bounded explicit snapshot, starts Docker with stdin code, monitors runtime/output, removes its uniquely named container and returns exit/output/timing metadata.
- `run_python.drain()` is its reader-thread callback: it consumes stdout/stderr continuously, retains only the output allowance and flags overflow so the monitoring loop can stop execution.
- `tool()` wraps this executor as `sandbox_python`; nested `tool.run(context, args)` moves synchronous execution into a worker thread. Registration requires `code.execute`, approval and no caching.

The container receives explicit input content, not the application workspace or provider credentials. Actual Docker isolation has not been validated on this machine; tests inspect configuration and missing-engine behavior. Trusted daemon/kernel boundaries and thread cancellation limits still matter.

## Domain example and all supporting files

### [examples/fieldnote/adapter.py](../examples/fieldnote/adapter.py)

- `sql_factory(options)` constructs `query_observations` with its strict SQL-string schema; options are currently unused.
- `sql_factory.query(ctx, args)` opens the fixed database read-only, sets query-only mode/length/progress limits, executes one allowed query and returns distinct columns, up to 200 rows, query text and source metadata.
- `sql_factory.query.allow(action, a, b, database, trigger)` is SQLite's authorizer callback: permits selects/reads of four approved main tables and a small function allowlist, recording touched tables and denying other actions.
- `search_factory(options)` constructs the report-search tool; `search_factory.search(ctx, args)` counts query-term occurrences in original Markdown reports/protocols, sorts matches and returns at most four complete documents with IDs. This is lexical whole-document search, not PDF chunking, embeddings or vector search.
- `calculator_factory(options)` constructs the evidence-reference calculation schema; `calculator_factory.calculate(ctx, args)` resolves current-run artifact rows/fields to finite `Decimal` values, computes sum/mean/difference/ratio and returns the exact operands. It rejects other-run evidence, missing/boolean values, wrong operand counts and zero divisors.
- `instructions()` reads database table definitions and semantics into domain instructions; the CLI calls it at application construction, so the model sees the real schema rather than guessing table names.
- `ROOT`, `SCHEMA` and `FACTORIES` locate domain data, define SQL arguments and map approved factory IDs to constructors. The generic runner contains no station names or sensor assumptions.

### [examples/mcp_demo.py](../examples/mcp_demo.py)

- `multiply(a, b)` is the local demo server's decorated MCP tool; the `--server` branch exposes it over stdio.
- `main()` creates temporary state, opens an approved local peer, registers one tool and drives a scripted call through the real runner; it prints the actual returned artifact, not an invented fixture result.
- `main.approve(request)` prints the exact proposed demo action and explicitly approves it; this narrowly configured example is not a blanket approval policy for applications.

### Configuration, assets and documentation

- [`src/agent_foundry/__init__.py`](../src/agent_foundry/__init__.py) exports public contracts/runtime helpers; it defines no additional functions.
- [`pyproject.toml`](../pyproject.toml) defines Python/package/dependency bounds, wheel packaging, the `foundry → cli.main` entry point and pytest async settings. [`uv.lock`](../uv.lock) pins the resolved dependency graph; it is generated package metadata, not application logic.
- [`setup.sh`](../setup.sh) checks Python 3.11+, uses installed `uv` or installs its pinned bootstrap copy, and performs `uv sync --locked`. Its inline Python then clears macOS `UF_HIDDEN` only on this package's generated editable `.pth` link, if needed, because Python 3.13 skips hidden links; a fresh Python subprocess verifies `import agent_foundry`. This repairs environment discovery without changing file permissions or access policy; it creates no model answers or datasets.
- [`.env.example`](../.env.example) lists endpoint/key/model, optional provider `REASONING_EFFORT` and separate harness `REASONING_SCAFFOLD`; local `.env` is ignored. [`.gitignore`](../.gitignore) excludes credentials, environments, runtime state, caches and build output; this is version-control hygiene, not runtime access control.
- [`.vscode/settings.json`](../.vscode/settings.json) selects this folder's environment and pytest discovery; [`launch.json`](../.vscode/launch.json) supplies F5 presets for ordinary demo, plan-review demo and local MCP; [`tasks.json`](../.vscode/tasks.json) runs the offline pytest suite. These are editor conveniences, not additional runtime permissions.
- [`plugin.json`](../examples/fieldnote/plugin.json) selects the three approved domain factories and research-skill resource. [`SKILL.md`](../examples/fieldnote/skills/research/SKILL.md) explains valid/missing readings, compatible units/time windows, causal limits and evidence citations.
- `examples/fieldnote/data/operations.db` is a fixed synthetic SQLite snapshot: `sites` identifies locations, `sensors` defines instruments/units, `readings` stores scheduled values/quality/time, and `maintenance` records interventions. It has 3 sites, 9 sensors, 108 readings and 6 maintenance events; it is domain data, separate from runtime `state.sqlite`.
- [`manifest.json`](../examples/fieldnote/data/manifest.json) records fictional provenance, UTC period and six document paths/hashes; [`semantics.md`](../examples/fieldnote/data/semantics.md) defines interpretation and fixed-snapshot scope. The current search tool scans Markdown directly rather than using manifest hashes as a runtime integrity check.
- [`alp-field-report.md`](../examples/fieldnote/data/documents/alp-field-report.md), [`mar-field-report.md`](../examples/fieldnote/data/documents/mar-field-report.md) and [`pin-field-report.md`](../examples/fieldnote/data/documents/pin-field-report.md) supply station-specific narrative evidence.
- [`quality-protocol.md`](../examples/fieldnote/data/documents/quality-protocol.md) defines quality/aggregation; [`units-time-protocol.md`](../examples/fieldnote/data/documents/units-time-protocol.md) defines units/schema/time; [`maintenance-protocol.md`](../examples/fieldnote/data/documents/maintenance-protocol.md) explains evidence and causal limitations.
- [`README.md`](../README.md) is the runnable entry point; [`ARCHITECTURE.md`](ARCHITECTURE.md) separates original Fieldnote from this core; [`REASONING.md`](REASONING.md) explains optional public planning/review; [`ROADMAP.md`](ROADMAP.md) lists improvement gates; [`PROVENANCE.md`](PROVENANCE.md) states origin/release boundaries; [`VALIDATION.md`](VALIDATION.md) records observed checks and limitations.
- [`validation-fingerprint.json`](validation-fingerprint.json) binds a recorded software check to source/test/dependency hashes and counts. It is a historical validation record and needs refreshing when those files change; it does not prove live model accuracy. This walkthrough maps code to purpose; it is not another execution log.
- Generated `.foundry/state.sqlite`, `.venv`, `.bootstrap`, caches and Git metadata are local operational artifacts, excluded from the review archive. No PDF/vector index, browser assets, external model weights or DPO dataset is shipped by this core.

## Tests, function by function

Tests use temporary workspaces and deterministic model replies to verify host behavior without spending model credits. A named test can generate several pytest cases through parametrization, so function count differs from test-case count. Return values are normally `None`; success means assertions passed. These are software-boundary tests, not held-out answer-quality evaluations.

### [tests/test_context.py](../tests/test_context.py)

- `exchange(index, size)` creates one artificial matching call/result pair; `assert_complete_tool_protocol(messages)` checks complete pairing and ordering in assembled prompts.
- `test_context_keeps_original_instructions_question_and_order`: preserves instructions/current question, labels memory as untrusted data and leaves inputs unchanged.
- `test_budget_drops_whole_old_history_pairs_before_tool_evidence`: removes complete old conversation pairs before current evidence.
- `test_compaction_removes_complete_old_groups_and_preserves_artifact_pointers`: compacts whole exchanges while retaining recovery IDs and the latest result.
- `test_mandatory_prompt_and_tool_definitions_are_never_silently_truncated`: refuses oversized instructions, question or schemas.
- `test_latest_observation_must_be_explicitly_previewed_not_discarded`: refuses to silently drop the newest oversized observation.
- `test_orphan_duplicate_incomplete_and_privileged_tool_history_are_rejected`: parametrized malformed pairing/role boundaries are rejected.
- `test_preview_is_valid_bounded_json_and_explicit_about_missing_content`: large escaped/Unicode content stays valid, bounded JSON with explicit excerpt status.
- `test_small_preview_keeps_original_type_and_full_content`: small content retains its type; missing artifact IDs are rejected.
- `test_trailing_runtime_note_does_not_displace_latest_tool_observation`: a later note cannot make compaction discard the latest real tool result.
- `test_malformed_message_container_raises_validation_error`: non-list/non-object history gets a clear validation failure.

### [tests/test_memory.py](../tests/test_memory.py)

- `test_completed_turns_are_session_scoped_ordered_and_durable`: reopening SQLite retains correctly ordered, isolated completed turns.
- `test_long_term_memory_requires_explicit_write_and_never_crosses_scope`: chat history does not silently become facts, and searches respect scope/provenance.
- `test_expiry_filters_without_silently_mutating_and_forget_deletes`: expiry hides records at the exact boundary; explicit deletion affects only its scope.
- `test_memory_update_retains_identity_and_replaces_source_and_expiry`: replacing a scoped key keeps its ID and updates its content/provenance/TTL.
- `test_memory_rejects_invalid_ttl`: rejects zero, negative, boolean, nonfinite and textual TTL values.
- `test_artifacts_are_content_addressed_and_runs_round_trip_without_aliasing`: IDs depend on run/content, serialized copies cannot be mutated by reference, and absent records fail.
- `test_event_sequence_is_atomic_across_writers`: simultaneous writes get unique ordered sequence numbers; its nested `append(i)` submits one indexed event per worker.
- `test_json_state_rejects_nonfinite_numbers`: artifacts/checkpoints/events reject invalid JSON numbers without partial event writes.
- `test_same_memory_key_updated_concurrently_keeps_one_identity`: concurrent upserts retain one record and private file mode.
- `test_huge_ttl_is_cleanly_rejected_before_any_write`: unrepresentable expiries fail without creating memory.

### [tests/test_runner.py](../tests/test_runner.py)

- `build(...)` creates a temporary real runner/registry/store plus scripted model; `lookup_tool(handler, ...)` supplies a common strict integer tool contract.
- `calls(*items)` builds fixture tool-request replies; `tool_observations(request)` decodes resulting model-visible observations for assertions.
- `test_real_loop_supplies_tool_result_then_finishes_and_records_history`: its nested async `lookup` records execution/working notes; the test verifies artifacts, subsequent inference context and follow-up history.
- `test_unknown_and_malformed_calls_consume_budget_and_all_receive_replies`: invalid and excess calls do not execute and every requested ID still gets a rejection.
- `test_denied_capability_is_hidden_and_never_executed_even_if_model_requests_it`: hiding a tool is backed by execution-time denial.
- `test_before_tool_exception_fails_closed`: nested `broken_policy` raises at the execution gate; no handler/artifact can run afterward.
- `test_hooks_receive_deep_copies_and_cannot_rewrite_authorized_arguments`: nested `mutate_snapshot` tries to change arguments/instructions, but executed values remain original.
- `test_regex_output_guard_blocks_release_and_does_not_add_a_completed_turn`: a blocked draft does not become future conversation history.
- `test_artifact_preview_and_all_persisted_tool_metadata_redact_declared_secrets`: nested `large_result` deliberately emits a declared secret in content/metadata; returned answers and stored records must redact it.
- `test_side_effect_waits_for_its_exact_action_approval_then_executes_once`: wrong approval does nothing, right approval resumes once, and a completed run cannot be resumed again.
- `test_approval_is_bound_to_exact_arguments_even_if_saved_state_is_modified`: changing pending arguments produces a different action ID and cannot reuse old approval.
- `test_changed_runtime_configuration_or_registry_rejects_old_approval`: changed limits/capabilities invalidate resume.
- `test_same_qualname_handler_with_changed_closure_target_cannot_reuse_approval`: nested `writer_factory(target)` returns `writer(ctx, args)` targeting that path; swapping equal-named closures cannot redirect an approved write.
- `test_timeout_of_side_effect_requires_reconciliation_and_no_automatic_retry`: nested `slow_write` finishes after await timeout, demonstrating why the harness refuses automatic replay.
- `test_identical_read_only_calls_reuse_one_artifact_and_one_execution`: same arguments reuse evidence within a run while supplying both protocol responses.
- `test_model_deadline_records_explicit_stop_without_a_completed_history_turn`: nested `SlowModel.respond` sleeps beyond the budget; the failed question is not added as completed history.
- `test_model_cancellation_is_recorded_and_propagated`: nested `WaitingModel.respond` signals startup then waits; cancellation persists status and remains visible to the caller.
- `test_recent_history_cannot_cross_runner_scope`: the same session label in a different explicit scope receives none of the previous conversation.
- `test_durable_memory_in_shared_store_is_scoped_by_workspace_and_explicit_scope`: one shared SQLite file still isolates durable facts across workspace or scope changes.

### [tests/test_extensions.py](../tests/test_extensions.py)

- `basic_tool(name)` makes an inert read-only fixture; `manifest(root, **updates)` writes a minimal plugin JSON for each loader test.
- `test_skill_catalog_only_loads_approved_markdown_on_request`: discovers instruction metadata, loads approved Markdown and never imports unrelated code.
- `test_skills_reject_symlink_escape_and_ambiguous_names`: outside-root symlinks and colliding catalog names fail.
- `test_plugin_publishes_only_approved_factory_tool_without_permission_escalation`: approved factory output retains its capability and receives plugin provenance.
- `test_plugins_cannot_import_unknown_code_or_self_grant_capabilities`: unknown factories and direct/nested permission/environment overrides are rejected before publication.
- `test_plugin_collision_does_not_partially_publish_bundle`: a late collision leaves the original registry unchanged.
- `test_plugin_skills_cannot_traverse_or_escape_root`: plugin instruction paths cannot leave the approved root.

### [tests/test_mcp_adapter.py](../tests/test_mcp_adapter.py)

- `make_server(tmp_path)` writes a real local test peer. Inside that generated script, `add(a,b)` adds integers, `environment()` reports only test environment flags, and `hidden_delete()` is deliberately unexposed; these are test fixtures, not shipped actions.
- `test_real_local_mcp_tool_discovery_call_allowlist_and_environment`: launches the peer, discovers/calls only allowed names, checks schema/environment/provenance and rejects calls after closure.
- `test_missing_allowlisted_mcp_tool_fails_before_registry_changes`: missing advertised requirements cannot partially register tools.
- `test_mcp_connections_require_explicit_trusted_configuration`: rejects absent/wildcard allowlists, remote HTTP, credential-bearing URLs and unapproved local launches.
- `test_untrusted_schema_cannot_trigger_network_reference_resolution`: remote references are denied while local JSON-schema references remain usable.

### [tests/test_sandbox.py](../tests/test_sandbox.py)

- `test_sandbox_never_falls_back_to_host_execution`: a missing Docker executable cannot execute the submitted Python on the host.
- `test_container_command_has_explicit_boundaries_and_only_snapshot_mount`: inspects command arguments for network/resource/user/mount restrictions and absent dangerous flags.
- `test_sandbox_registration_requires_execution_permission_and_approval`: the tool is classified as approved `code.execute`, side-effectful and non-cacheable.
- `test_image_cannot_inject_docker_options`: invalid image strings cannot become command flags or shell fragments.
- `test_optional_real_container_isolation`: when Docker/daemon/image exist, executes the explicit-input smoke test; otherwise skips. It is not a comprehensive escape/network security test.
- `test_snapshot_rejects_unapproved_paths_before_starting_a_process`: nested `forbidden` fails if process creation occurs; traversal, absolute and hidden input paths must be rejected first.

### [tests/test_examples.py](../tests/test_examples.py)

- `test_scripted_fieldnote_plugin_runs_real_database_and_reports`: runs native plugin tools against actual synthetic assets and checks 108/101/4/3 scheduled/valid/suspect/missing counts.
- `test_fieldnote_loader_has_skills_and_rejects_database_writes`: confirms the approved skill tool and a rejected SQL delete.
- `test_known_configured_secrets_never_reach_model_or_tool`: nested `handler` records any execution; the test submits a declared secret and requires redaction plus denied secret-bearing tool arguments.
- `test_approved_skill_loads_progressively_as_task_instructions`: full skill instructions enter the next prompt only after a valid load request.
- `test_final_lifecycle_guard_cannot_leak_blocked_answer_into_history`: nested `block_release` denies `after_run`; the blocked draft must never become a completed turn.
- `test_empty_final_answer_does_not_create_a_completed_turn`: a reply containing neither calls nor visible answer becomes an error, without conversational history pollution.

### [tests/test_reasoning.py](../tests/test_reasoning.py)

- Fixture helpers: `call(identifier, name, args)` makes one requested action; `plan()` makes a one-part checklist; `coverage(identifier, draft)` makes a source-reference self-review; `review(messages, tools)` selects the actual observed artifact ID for a dynamic fixture reply.
- `FixtureModel.__init__(replies)` stores fixture responses; `FixtureModel.respond(...)` records copied requests and returns either a fixed reply or one calculated from current observations. `build(...)` assembles a real plan-review runner with a lookup tool; `review_context(...)` creates direct tool-test state and a stored source artifact.
- `test_off_is_default_and_has_no_new_schemas_or_extra_exchanges`: off mode preserves the original tool surface, registry identity, working-state shape and one-call completion.
- `test_on_clones_registry_and_completes_an_exact_reviewed_draft`: on mode leaves the caller's registry unchanged and saves only an exactly reviewed answer.
- `test_ordinary_work_is_denied_before_plan_and_can_recover`: premature tool use fails, consumes budget and can recover by planning first.
- `test_premature_final_repair_uses_existing_step_budget`: missing planning/review receives bounded feedback instead of a new unbounded repair loop.
- `test_plan_alone_is_not_an_answer_review`: a checklist cannot substitute for a final-draft review.
- `test_changed_final_text_requires_reviewed_text_or_a_new_review`: modifying the draft is rejected until the model returns/reviews the matching text.
- `test_ordinary_reads_notes_and_failed_work_invalidate_review`: later reads, cached reads, notes or failed source work invalidate the old review; nested `note` mutates working notes and `broken` simulates an unavailable source.
- `test_tool_budget_counts_planning_and_review`: scaffold calls consume the ordinary action budget.
- `test_scaffold_uses_normal_hooks_and_capabilities`: nested `hook` denies a scaffold action; a separate case verifies denied `working.write` is hidden and enforced.
- `test_secret_in_scaffold_arguments_is_rejected_and_redacted`: public plans cannot bypass configured-secret argument protection.
- `test_review_boundaries_survive_approval_pause_and_mode_change_is_rejected`: approval resume preserves the workflow, while switching mode invalidates the saved fingerprint.
- `test_plan_schema_rejects_private_fields_and_unbounded_parts`: extra reasoning fields and oversized/empty checklists fail strict schema validation.
- `test_review_schema_has_only_bounded_public_fields`: private fields, arbitrary truth labels and oversized drafts/references are rejected.
- `test_scaffold_notes_skills_and_memory_are_not_source_evidence`: each excluded tool category cannot manufacture factual support for a review.
- `test_review_rejects_unknown_foreign_and_missing_part_references`: review parts must match the plan and support must reference this run's observed sources.
- `test_review_is_stale_after_public_state_change_and_does_not_claim_truth`: changed notes invalidate completion; the review explicitly disclaims semantic verification.
- `test_a_gap_can_be_honestly_reviewed_without_fabricated_evidence`: a missing-evidence answer can complete with an explicit gap rather than invented sources.
- `test_source_tool_cannot_spoof_the_recorded_artifact_tool_name`: handler metadata cannot replace the runner's recorded tool identity.
- `test_cli_all_run_commands_expose_reasoning_mode`: demo/chat/resume parse mode selection consistently.
- `test_demo_plan_review_runs_actual_tools_without_model_api`: the deterministic example executes real assets and a two-part review without inference credits.
- `test_cli_environment_default_and_flag_override`: environment selection works, explicit off wins, and unknown modes fail; nested `configured` redirects only test application construction to the real example assets.
- `test_unknown_runtime_mode_and_scaffold_name_collision_fail_closed`: invalid runtime settings or conflicting scaffold names fail before use.
- `test_fingerprint_is_canonical_after_a_thread_retains_code_constants`: nested `retain` saves a literal in working state; normal threaded execution must leave the unchanged handler's fingerprint stable.
- `test_fingerprint_still_detects_changed_literals_and_nested_code`: nested `first`/`second` return comprehensions with different constants; even equal descriptive names must produce different fingerprints.
- `test_long_valid_question_is_not_rejected_by_memory_search`: a question longer than the memory-search limit still completes with its full text present in model context.

## Optional public reasoning scaffold

### [reasoning.py](../src/agent_foundry/reasoning.py)

The baseline loop is already capable of multiple evidence-gathering steps. `--reasoning plan-review` adds a visible checklist and self-review contract: plan → ordinary tools → review exact draft → return it verbatim. It does not add a reviewer model, train weights or expose hidden chain-of-thought. `off` removes its extra tools/instructions/gates. Provider reasoning effort is a separate inference setting, not this workflow.

- `answer_hash(answer)` hashes exact UTF-8 draft text, including whitespace; review and completion checks use it to detect a changed answer.
- `_state_hash(ctx)` hashes public working state and non-scaffold artifact IDs, excluding the scaffold's own logs; completion uses it to detect changed notes or evidence.
- `require_plan(state, tool_name)` rejects ordinary tools until `record_plan` creates a checklist; the runner calls it at execution time.
- `invalidate_review(state, tool_name)` clears an earlier review before ordinary work, including cached reads and notes; a later observation cannot silently inherit a stale assessment.
- `completion_issue(ctx, answer)` returns a repair instruction if the plan/review is absent or draft/state hashes differ, otherwise `None`; the runner feeds repair text back within existing budgets.
- `_record_plan(ctx, args)` creates a once-recorded checklist with IDs `P1…P8` and a public scaffold-state marker; it cannot replace the plan after evidence gathering starts.
- `_review_answer(ctx, args)` requires coverage of each plan ID exactly once and checks claimed supporting artifact IDs are observed original sources from this run, excluding notes, skills, memory and scaffold artifacts. It saves draft/state hashes, coverage and limitations; it does not establish that evidence entails the claims.
- `scaffold_tools()` creates strict non-cacheable `working.write` contracts for `record_plan` and `review_answer`, including bounded tasks/draft/coverage/limitations; normal schema/capability/hook/budget checks still apply.
- `MODES`, `SCAFFOLD_TOOLS`, `NON_EVIDENCE_TOOLS` and `INSTRUCTIONS` define allowed settings, source exclusions and the visible workflow prompt. The mode is included in approval fingerprints, so resume must use the same selection.

Supported/partial coverage needs actual source references, while missing/not-applicable parts can report gaps. Those labels and the checklist's interpretation of the question remain model judgments. Plans/reviews add calls, tokens and latency; software tests can prove gating and reference ownership, but a matched live evaluation is needed to establish any answer-quality improvement.
