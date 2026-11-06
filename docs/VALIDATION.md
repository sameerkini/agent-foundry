# Validation record

November 6, 2026. This is a local v0 engineering milestone, with no public remote or deployment.

## Observed checks

- **127 tests passed; 1 skipped.** Tests exercise the real core with deterministic model fixtures, including tool/result protocol pairing, malformed calls, capability denial, hooks, output release, bound approvals and resume, scope isolation, context compaction, memory TTL/deletion, artifact provenance and declared-secret redaction.
- The optional `plan-review` workflow adds 44 cases covering unchanged off mode, plan-before-work, exact-draft review, current-run evidence, stale reviews, explicit gaps, budgets, hooks, secrets, capabilities, approval resume and CLI/environment selection. Coverage remains model self-assessment, not semantic entailment. Both CLI demo modes execute real SQL/report tools without inference; the scripted model takes three steps off versus five with plan-review, which demonstrates overhead rather than a quality improvement.
- A scripted Fieldnote integration executed the real read-only observations query and report-search plugin. It returned 108 scheduled readings: 101 valid, 4 suspect and 3 missing. This is integration evidence, not model reasoning accuracy.
- Real local MCP2 stdio peers were launched, initialized, discovered, called and closed. Tests covered explicit allowlists, namespacing, schemas, environment boundaries and closed-connection behavior. `python examples/mcp_demo.py` also completed through the ordinary runner and approval gate.
- Skills/plugin tests covered traversal, escaping symlinks, duplicate names, unapproved factories, permission overrides and progressive instruction loading.
- The Docker execution test was **skipped because Docker is absent**. Command constraints, snapshot bounds and failure-closed behavior were tested without an engine. No claim of real network/filesystem container isolation is made yet.
- No paid provider calls were made. The OpenAI-compatible adapter and remote MCP transport are implemented but not live-validated against a hosted service. No semantic accuracy, cost-saving or training result is asserted.

## Defects caught during development

Artifact metadata initially escaped the declared-secret redactor. It now goes through the same redaction as content. An answer blocked by the final lifecycle hook initially entered conversation history; turns are now saved only after all release checks succeed. Regressions cover both issues. Approval fingerprints bind code/configuration and common handler closure state, so changing a target invalidates a paused approval.

Planning exposed a second fingerprint defect: `marshal.dumps(code)` changes reference flags when execution retains a literal, despite unchanged code. Canonical recursive code attributes/constants now preserve stable approval fingerprints while still detecting changed handlers. The runner also records the actual tool name authoritatively over returned metadata, and bounds only the memory-search query to its 5,000-character limit while preserving the full valid question in model context. Regression tests cover these cases.

The final desktop check found a macOS hidden flag on this package's editable `.pth` link. Python 3.13 explicitly skips hidden `.pth` files, so the package could not import despite installed dependencies. Setup now clears that flag only on its own generated link and verifies import in a fresh interpreter. This is an installation repair, not a change to project file-access policy.

## Reproduce

```sh
bash setup.sh
.venv/bin/python -m pytest -q
.venv/bin/foundry demo
.venv/bin/foundry demo --reasoning plan-review
.venv/bin/python examples/mcp_demo.py
```

Recorded tests are software boundary checks. They do not establish resistance to every prompt injection, malicious trusted Python extension, compromised MCP peer, container escape or model semantic error. Native handlers and lifecycle hooks remain trusted code. Project scopes are local namespaces, not authenticated tenant identities.

Next validation: a selected live provider run, remote MCP interoperability, real Docker execution with filesystem/network/timeout probes, and matched task-family evaluations before training or public quality claims.
