# Model reasoning and the optional scaffold

The model supplies the learned decision-making ability. The harness supplies the workspace, tools, state, permission checks and opportunities to inspect results. Both layers matter: a skilled researcher still benefits from a notebook and reliable instruments.

## What lives in the model

A reasoning model can spend inference-time computation considering a problem before returning visible text or tool calls. Training can encourage this behavior through reinforcement learning: rewarded outputs become more likely as weights change during training. That training is different from our ordinary application runs, which do not update weights. OpenAI documents this training approach for its [o1 series](https://developers.openai.com/api/docs/models/o1), and describes internal reasoning tokens in its [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning). Training recipes and supported controls differ across model families.

We do not request, extract or persist private chain-of-thought. A visible task plan says what needs doing; an evidence checklist says what supports the answer. Neither is a transcript of the model's internal reasoning. OpenAI's [reasoning prompting guidance](https://developers.openai.com/api/docs/guides/reasoning-best-practices) recommends clear goals and constraints rather than asking a reasoning model to reveal step-by-step reasoning.

## What lives in this harness

The action/observation loop already lets a model choose a tool, see a result, request more evidence and answer. `plan-review` is an optional additional workflow:

1. Record a short public plan with identifiable answer parts.
2. Gather evidence using the ordinary tools and policies.
3. Record a review of the exact proposed answer, covering those parts and naming supporting artifacts or explicit gaps.
4. Release the same draft only after ordinary output/release checks.

The runner checks the workflow and references. The model remains responsible for interpretation. A real SQL artifact can still be misread, and a self-assessed coverage label is not an independent entailment grade. Scaffold artifacts and working notes are not fresh factual source evidence.

Any later successful tool work invalidates an earlier review. Editing the final text requires a new review. Missing or stale planning/review receives feedback within the existing step/time/tool budgets; there is no unbounded repair loop. Capabilities, approval and hooks continue to apply. The selected mode is included in approval checkpoint fingerprints.

## Toggle and inspect it

```sh
# Deterministic examples, no model API calls:
.venv/bin/foundry demo --reasoning off
.venv/bin/foundry demo --reasoning plan-review

# Fresh inference requires your configured provider:
.venv/bin/foundry chat "Compare valid measurements across stations." --reasoning plan-review
.venv/bin/foundry trace RUN_ID
```

Use `--reasoning off` to remove the scaffold's instructions, tools and completion requirements. `REASONING_SCAFFOLD` supplies a default when the command-line choice is omitted. Default installation behavior is off. Resume with the same mode as the paused run.

`REASONING_EFFORT` is a separate provider setting, forwarded only when configured. The harness does not infer model capabilities from a model name or silently change either setting. An internally reasoning model may still benefit from an explicit evidence checklist; test that rather than assuming either approach always wins.

## Trade-offs and next experiment

Explicit plans/reviews consume context and usually add tool exchanges. They can expose missing answer parts and stale drafts, but can also add latency, repetition or confidently wrong self-reviews. Off mode keeps the simpler loop. Neither mode changes the model's weights.

The software tests check gating, state, provenance and bounded behavior using fixtures. They do not establish a reasoning-quality improvement. A useful next comparison holds model, questions, corpus and budgets fixed and measures answer correctness, support, missing answer parts, tool calls, tokens and latency for both modes. Keep an untouched question-family split. DPO remains deferred until reviewed preferences, trainable weights and independent evaluation exist.
