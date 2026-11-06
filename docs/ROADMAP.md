# Agent Foundry roadmap

The goal is a reusable, inspectable agent harness, with Fieldnote as a working example. Build a small complete path first, then expand capabilities only when tests and real tasks justify them. Publication happens after the local project and its claims are reviewed.

The local v0 already supplies the bounded runner, shared tool registry, guards/hooks, explicit SQLite memory, skills/plugin loading, an MCP adapter and an optional Docker executor. The scripted Fieldnote CLI example and real local MCP demonstration are exercised. The phases below define verification and improvement gates, rather than claiming those layers remain entirely unimplemented. Live remote provider behavior, external MCP servers, actual Docker isolation and a rebuilt browser UI remain separate milestones. See `VALIDATION.md` for exact evidence.

## 1 Harden the implemented core

Implement the provider-neutral model contract, typed tool registry, bounded runner, public action/observation trace, capability gates, input/output checks and lifecycle hooks. Keep native tools usable without a network connection through a deterministic model fixture. Distinguish a demonstration fixture from live inference.

**Exit evidence:** tools cannot execute before validation/authorization; every model tool call receives a corresponding result or rejection; malformed calls consume budget; output checks can block a result; timeout/cancellation and partial-answer behavior are explicit. Configuration and public traces exclude credentials.

## 2 Validate explicit state and context

Provide session history, structured working-state checkpoints, a bounded context builder and scoped SQLite memory with provenance, retrieval and deletion. Clearly distinguish remembered preferences, source observations and model-generated summaries. Show users what is retained.

**Exit evidence:** no cross-session scope leakage; expired/deleted memories disappear from retrieval; untrusted text cannot silently promote itself to trusted memory; context reduction preserves tool-call/result pairing. Character budgets are labeled as approximations until tokenizer-aware accounting is implemented.

## 3 Expand and verify extension surfaces

Use one registry for native tools and MCP-adapted tools. Load skills progressively as instructions/resources. Plugins declare their tools, skills and requested capabilities. Require explicit configuration for third-party code and local MCP process launches; protocol compatibility is not a trust decision.

**Exit evidence:** unknown or duplicate tool names are rejected, plugin capabilities cannot exceed application policy, skills cannot escape their resource root, MCP input/output remains bounded, and disconnects produce useful errors. SDK-level tests are distinct from tests against a real external MCP server.

## 4 Validate optional isolated execution

Introduce a Docker-backed executor for workloads that genuinely need code execution. Define workspace mounts, network policy, resource limits and cleanup. Keep it optional so read-only tools and the basic demonstration remain easy to run.

**Exit evidence:** the image and container boundary are explicit; execution fails safely when Docker is unavailable; no implicit host fallback occurs; tests cover filesystem access, network restrictions, timeout, output size and cleanup. Runtime claims require an actual engine test, not only an inspected command or mocked process.

## 5 Extend the Fieldnote demonstration

Keep the synthetic research data and reference answers in the example/evaluator, not the core. Exercise database/document routing, source-referenced arithmetic, relationship navigation and citations through the generic runner. Preserve the original standalone Fieldnote project for comparison.

The current CLI example covers SQL, report search and calculations. Reconnecting the original browser, graph explorer and source-review UI requires an application adapter; it is not completed by copying those assets into an example directory.

**Exit evidence:** a clean local setup, successful deterministic run, source/routing diagnostics and selected fresh provider runs. Report software checks, retrieval coverage, semantic answer grades, latency and usage separately. No scores from a different application or code snapshot transfer automatically.

## 6 Measure which features help

Freeze model, corpus, settings and question families before comparing context strategies, memory, skills, MCP adapters or sandbox tools. Remove one component at a time and preserve the actual outputs and unknown grades. Inspect both improvements and errors introduced by review.

**Exit evidence:** matched comparisons with explicit denominators, source-based analysis of failures, cost/latency records and version fingerprints. A successful tool execution or valid citation is not a correctness grade. Inspected confirmation cases become regression data.

## 7 Connect a trainable model later

Collect only authorized, reviewed preference pairs and public action trajectories. Record why one response was preferred, preserve provenance and split training/evaluation by task family. First make the future transformer satisfy the `Model` interface and tool-call protocol.

**Deferred:** DPO training, model serving and broad claims of improved reasoning. DPO needs access to trainable weights and a suitable reference/training setup; it cannot be applied merely by wrapping an arbitrary hosted API. Do not collect hidden chain-of-thought as a prerequisite for debugging or training.

## Before public release

- Refresh the implemented/experimental/deferred feature inventory against the exact code and tests.
- Run the documented clean-install path and inspect the example output and diagrams.
- Verify that only newly authored or properly licensed code/data is included, with no credentials, private runs, prior project history or unrelated assets.
- Choose a license deliberately and document third-party dependencies.
- Publish test evidence and limitations alongside the architecture, rather than claiming every supported integration was live-validated.

The local v0 can implement several of these layers together. Their exit evidence is what determines readiness; the presence of a class, adapter or configuration option alone does not.
