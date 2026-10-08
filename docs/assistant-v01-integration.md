# EVA Assistant v0.1 design integration

Status: implemented in the stable runtime. Source: the user-supplied
`EVA_Assistant_v0.1.zip`. The archive was read as reference material, not executed
or installed. Its standalone kernel has not been copied into EVA.

## What was adopted

| Archive idea | Stable implementation | Important boundary |
| --- | --- | --- |
| Epistemic memory types | `memory/provenance.py`, S1–S3, governor metadata | Origin is not proof of truth |
| Explicit memory retention | `ChatRequest.remember`, tier manager, direct document tool invocation | Only S3 promotion is opt-in; transcripts, events and episodic records still persist |
| Preserve uncertain knowledge | Context labels and compaction provenance | A generated summary remains an assistant inference |
| Deterministic output review | `core/response_review.py`, agent orchestrator | Checks declarations and receipt existence, not factual truth or semantic entailment |
| Separate proposal from authorization | Document ingestion requires a direct authenticated API call | Model arguments cannot approve their own write |
| Trust execution receipts | Consistent `ok`, `error`, `review` in result registry and WS/HTTP responses | Failure messages are not ingested as successful assistant memories |

Existing FastAPI routes, event bus, worker interface, LLM adapters, persona,
world graph and five-tier storage remain the main architecture.

## Memory contract

New records carry a versioned provenance object with `schema_version`,
`epistemic_status`, `source`, and `source_event_id`. The knowledge labels are
`verified_fact`, `user_statement`, `working_model`, `hypothesis`,
`assistant_inference`, and `unknown`. No current ingestion path automatically
assigns `verified_fact`.

- User messages are labeled `user_statement`, not verified facts.
- Agent replies and generated summaries are labeled `assistant_inference`.
- Imported document chunks are `unknown` with their file source. Document
  instructions do not become system instructions.
- Existing unlabeled records stay readable and are interpreted as `unknown`.
- Migrations 004 and 005 add provenance columns without rewriting old content.
- Session flush/restore, governor/tier bridges and retrieval retain provenance.
- The governor bridge no longer recursively sends the same write back through
  the tier manager. One ingest produces one record per selected tier.
- Compaction keeps input origins and event IDs, uses no more than the lowest
  input confidence, and never promotes its summary based on repeated claims.
- Archived S3 records are excluded from both FTS and LIKE retrieval paths.

The existing importance threshold still selects S1/S2. S3 additionally requires
`allow_long_term=True` from application code. For chat, the strict boolean
`remember: true` authorizes storing the exact user message and ensures it reaches
the retention threshold. `/search` or other command prefixes are not retention
consent. The UI checkbox applies to one message and resets after submission.

This is **not an incognito mode** or a deletion policy. Disabling `remember`
does not disable event, episodic, working-memory or conversation storage.

## Tool authorization

`ingest_document` is marked `requires_user_authorization`. Calls emitted by
the model are denied, including calls with an `authorized` argument. A direct
`POST /api/tools/call` request authorizes that exact tool and argument set through
the existing API authentication and executor boundary. An explicit document
import may retain chunks in S3, labeled with their source but not as facts.

Code and network tools keep their existing opt-in settings. This change is not
a new sandbox or a general interactive approval UI. New mutating tools must be
marked and routed through the same boundary. Duplicate tool registration fails
instead of silently replacing a guarded tool.

## Output review

ChatAgent receives a structured final-response protocol (`eva_response`) with
message text, claims, knowledge labels, confidence, evidence IDs, time sensitivity
and risk level. Tool calls continue to use the existing `tool` protocol.

The runtime creates receipts after tool execution. A model cannot supply its own
receipt in the response envelope. The review rejects malformed envelopes, empty
answers, invalid confidence, overconfident unknowns, nonexistent or failed tool
references, verified-fact labels with no successful tool receipt, and time-sensitive
claims without a valid runtime observation time. A memory-search receipt alone
cannot promote a remembered statement into a verified fact.

Final text is reviewed in the orchestrator before publication or memory ingestion.
Streaming collects the agent's final result without forwarding draft tokens, then
sends the reviewed text over the existing SSE/WS transport. This sacrifices early
token/progress display. Rejected drafts are replaced with an uncertainty response,
marked `ok: false`, and excluded from successful reply ingestion and graph extraction.

Reports have explicit limits:

- `not_assessed`, `passed: null`: legacy/plain text or no structured claims.
- `contract_checked`, `passed: true`: declared claims passed the mechanical checks.
- `blocked`, `passed: false`: a declared contract violation was found.
- `execution_failed`: the agent itself returned a failure.

`fact_verified` is always false. This reviewer does not identify all claims in
unstructured prose, decide whether references entail a claim, assess source
reliability, guarantee freshness from a timestamp, or detect every high-risk
topic. The model still chooses its declarations. These limits must not be
presented as a completed truth-verification system.

## Deliberately not ported

- Project/session isolation needs a consistent scope across memory, world state,
  vectors, results and authorization. A database filter alone would overstate it.
  The application remains a single-user/workspace runtime.
- Full per-record revision/supersession is not introduced by provenance schema
  versioning. Existing lifecycle and conflict handling remain in place.
- The archive's functional state numbers are not imported as consciousness scores.
- Process workers remain a separate roadmap item; the pre-existing untracked
  process-backend test still references an unimplemented module.

Regression coverage is in `tests/test_evidence_governance.py`, supplemented by
the existing chat, memory, agent, migration and architecture tests.

## Validation of this integration

- 987 tests passed in an isolated source copy with Mock LLM and vector model
  initialization disabled: `python -m pytest -q --ignore=tests/test_process_worker.py`.
- The ignored file was already untracked and cannot collect because
  `runtime.process_worker` does not exist. It was neither changed nor removed.
- Ruff passes for every changed/new Python file. The repository-wide check still
  reports the same 13 pre-existing unused-import/variable findings in worker files.
- JavaScript syntax checks pass. Browser smoke testing verified a chat reply,
  automatic checkbox reset, and exactly one S3 record labeled `user_statement`.
- Existing user configuration and databases were not migrated during validation.
  Real provider calls, external services and production deployment were not tested.
