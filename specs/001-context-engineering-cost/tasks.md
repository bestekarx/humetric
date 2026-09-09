---

description: "Task list for: Bağlam Mühendisliği ve Maliyet Optimizasyonu"
---

# Tasks: Bağlam Mühendisliği ve Maliyet Optimizasyonu

**Input**: Design documents from `/specs/001-context-engineering-cost/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: `tests/` is gitignored in this repo (local-only, per `CLAUDE.md`) and CI runs with a
dummy provider key, so no assertion needing a real LLM call can be a CI gate. Verification is
therefore split in two, per `contracts/prompt-composition.md` § Cache kanıtı:
**(a)** committed, key-free checks that CI can actually run (`scripts/verify_cache_prefix.py`),
and **(b)** local manual verification against a real key. Tasks below say which is which; a
missing local test never blocks a commit (`HM-PROC-01`).

**Two repositories**: Tasks whose path starts with `../humetric-site/` are implemented and
committed **in that separate repository**, never in this one (Constitution Principle VI /
CR-006). Everything else is `humetric` (this repo).

**Organization**: Phases follow the plan's user-decided execution order — **A1 → C1 → (A3 +
B1) → A2 → B2 → B3 → C2 → C3** — which also happens to be priority order (P1, P1, P2, P2, P3,
P3, P3). Each user-story phase maps to the identically-numbered story in `spec.md`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps to spec.md's US1–US7. Setup, Foundational, the site cross-cutting phase,
  and Polish carry no story label by convention.

---

## Phase 1: Setup

**Purpose**: Scaffold the shared architecture-decision document, then capture the
before-the-change baseline that SC-002 and SC-010 are measured against. **FR-020 makes T002 a
hard gate: it must complete before any code change from this feature lands**, because the
baseline is unrecoverable once behavior moves.

- [X] T001 Create `docs/architecture/context-engineering.md` skeleton with four section
      headers (bağlam bütçesi, prompt kompozisyonu, cache katmanları, maliyet muhasebesi), a
      `## Baseline (FR-020)` section for T002's numbers, and a placeholder Mermaid diagram
      (sinyal → bağlam montajı → bütçe kapısı → cache'li prefix / uçucu kuyruk → sağlayıcı
      ayrımı); link it from `docs/architecture/overview.md`'s file list
- [X] T002 **[GATE — FR-020]** Capture the pre-change baseline on the current `main` code, before
      any task below lands: (a) run a fixed sample set of signals through the current pipeline
      and record the produced `entity_metric.value` / `.confidence` values — use the existing
      canary harness as the fixed sample set: `python -m humetric.replay --pack <pack>
      --canary-file packs/canary/<pack>-canary.yaml --output /tmp/replay_before.json` (raw run
      output stays local) — and (b) run `python scripts/cost_bench.py run --signals 20
      --entities 5 --llm real --embed real --out /tmp/cost_before.json` for the current
      tokens-per-signal figure. Write the resulting numbers, the sample-set definition,
      and the run date into `docs/architecture/context-engineering.md` § Baseline — no server
      address, key, or personal path (`HM-SEC-01`) (depends on T001)

**Checkpoint**: Doc skeleton exists and carries a recorded baseline; Phase 3 (A1) may now start.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The token-breakdown columns and their config knobs are read/written by four of
the seven user stories (US1, US4, US5, US7) and the migration itself follows
`HM-DATA-02`/`HM-DATA-04`. Land this once, first.

**⚠️ CRITICAL**: US1, US4, US5 and US7 all depend on this phase.

- [X] T003 Add the feature's settings as environment-variable defaults in
      `src/humetric/config.py` (Principle II/IV — no brand model name, no secret; every new
      setting lives here): `EXTRACT_INPUT_TOKEN_BUDGET` (default **32000**, FR-005), a
      token-estimate calibration coefficient, `LLM_CALL_TIMEOUT_SECONDS`,
      `BATCH_POLL_MAX_ATTEMPTS`, `PLATFORM_KEY_DAILY_TOKEN_CAP` (FR-015; platform-key tenants
      only), and **one single** minimum-cacheable-prefix threshold variable whose default is
      the most conservative (highest) known threshold — explicitly **not** a per-provider map
      or model table, which would silently go stale (FR-011)
- [X] T004 [P] `alembic/versions/023_llm_call_token_breakdown.py` — `down_revision = "022"`;
      `upgrade()` adds four nullable columns (`input_tokens`, `output_tokens`,
      `cache_read_tokens`, `cache_write_tokens`) to `llm_call_record`; `downgrade()` drops
      exactly those four columns and nothing else; docstring records **why no new RLS policy
      is needed** (column add on an already-RLS'd, already-granted table) so a future reader
      doesn't assume it was skipped by mistake — reference the `022` policy form, not `020`'s
      on-disk (unpatched) template (`HM-DATA-02`, `HM-DATA-04`, CR-005)
- [X] T005 [P] `src/humetric/db/models.py:357-381` — add the same four nullable `Integer`
      columns to the `LlmCallRecord` model; `token_count`'s meaning and existing readers
      (`store.py:495`) are untouched
- [X] T006 Run `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` locally
      and confirm all three steps succeed before starting any **engine** user story (US1, US3,
      US4, US5, US6, US7); the site-only phases (US2, Phase 10) are not gated by it
      (depends on T004, T005)

**Checkpoint**: Migration applied both directions cleanly; token columns exist and are queryable.

---

## Phase 3: User Story 1 — Bir çağrının gerçekte ne kadara mal olduğunu görebilmek (Priority: P1) 🎯 MVP

**Goal**: Split `llm_call_record`'s single total into input/output/cache token counts and
record a pre-call context-size estimate, with **zero change to metric values** (a new
measurement key is added to `trace_data`; no metric value moves).

**Independent Test**: Ingest a signal, let the worker process it, query `llm_call_record` —
input/output/cache fields are populated (or `NULL` when the provider doesn't report them);
`entity_metric` values are byte-identical to T002's baseline.

### Implementation for User Story 1

- [X] T007 [P] [US1] Create `src/humetric/context.py` with `estimate_tokens(text) -> int` and
      `measure_extract_inputs(system, user) -> ContextMeasurement` per
      `contracts/context-measurement.md` — local, deterministic estimate; no provider network
      call, no `tiktoken` (research R5)
- [X] T008 [US1] `src/humetric/agents/base.py:167` — read `input_tokens`, `output_tokens`,
      `cache_read_input_tokens`, `cache_creation_input_tokens` separately from the Anthropic
      `Usage` object (verified field names, `contracts/llm-call-record.md`)
- [X] T009 [US1] `src/humetric/agents/multi_llm.py:~296` (`_call_openai`, shared by the OpenAI
      and DeepSeek providers) — **DeepSeek is already verified by live measurement (2026-09-08,
      `contracts/llm-call-record.md`)**: map `usage.prompt_tokens` → `input_tokens`,
      `.completion_tokens` → `output_tokens`, `.prompt_cache_hit_tokens` → `cache_read_tokens`,
      and pass `cache_write_tokens=None` (the provider does not report it). `prompt_tokens`
      already includes the cached part, so do **not** subtract the hit count from it. The
      OpenAI field names differ (`prompt_tokens_details.cached_tokens`) and are still
      **to verify** — branch on the resolved provider rather than assuming the shared SDK
      implies shared fields
- [X] T010 [US1] `src/humetric/agents/multi_llm.py:~420` — **Google is verified by live
      measurement (2026-09-09)**: map `usage_metadata.prompt_token_count` → `input_tokens`,
      `.candidates_token_count` → `output_tokens`, `.cached_content_token_count` →
      `cache_read_tokens` (the field is always present and is `0` on a miss — write the `0`,
      do **not** coerce it to `NULL`), and `cache_write_tokens=None` (no such field).
      See `contracts/llm-call-record.md` § `0` ile `NULL` ayrımı
- [X] T011 [US1] `src/humetric/services/usage_service.py:65-87` — expand `record_llm_tokens()`
      with optional `input_tokens` / `output_tokens` / `cache_read_tokens` /
      `cache_write_tokens` kwargs (default `None`); `_insert_llm_call_record` writes them;
      `count` and `token_count`'s meaning are unchanged; the existing tenant-session behavior
      of this write path is preserved unchanged (CR-001) (depends on T005)
- [X] T012 [US1] Wire all **four** call sites (the batch path in `base.py:record_batch_usage`
      is a fourth `llm_call_record` writer the original wording missed) (Anthropic path in `base.py`, OpenAI/DeepSeek and
      — Anthropic realtime in `base.py`, OpenAI/DeepSeek and Google in `multi_llm.py`, and
      the Anthropic batch path — to pass the fields read in T008–T010 into
      `record_llm_tokens()`; a field a provider doesn't report is passed as `None`, never `0`
      or an estimate (depends on T008, T009, T010, T011)
- [X] T013 [US1] Wire `measure_extract_inputs()` into the extraction call path in
      `src/humetric/worker.py` so `trace_data.measured_input_tokens` is written for **every**
      processed metric, always, regardless of budget status. `budget_exceeded` is a separate
      key written only on overage and is US5's job — this task only records the number
      (`contracts/trace-budget-flag.md` § Alanların yazılma kuralı) (depends on T007)
- [X] T014 [US1] Create `scripts/calibrate_token_estimate.py` — offline tool that reports the
      estimator's deviation **against recorded real data, not a provider count endpoint**
      (FR-004): it joins each signal's `trace_data.measured_input_tokens` (T013) against the
      corresponding `llm_call_record.input_tokens` (T012) and reports the deviation % plus a
      suggested coefficient. Makes **no network call**, so it works for all four providers. It
      edits no source file: a human sets the env var / `config.py` default from the report.
      Never runs at request time (depends on T012, T013)
- [X] T015 [US1] Local test (`tests/test_context_accounting.py`, gitignored): ingest a signal,
      assert the resulting `llm_call_record` row has `input_tokens`/`output_tokens` populated
      and cache fields `NULL` when the provider doesn't report them; assert `entity_metric`
      values match T002's captured baseline exactly (SC-002) (depends on T012, T013)

**Checkpoint**: `llm_call_record` is queryable for input/output/cache tokens; metric values
identical to the T002 baseline.

---

## Phase 4: User Story 2 — Sınırsız sihirbaz girdisinin faturayı katlamasını durdurmak (Priority: P1)

**Goal**: Cap the Pack Wizard's free-text/schema inputs so an oversized request never reaches
an LLM call or a credit charge.

**Independent Test**: Send an over-limit input to the wizard start tool; schema validation
rejects it and no agent turn begins.

### Implementation for User Story 2 (repo: `../humetric-site`)

- [ ] T016 [P] [US2] `../humetric-site/backend/src/mcpServer/tools.ts:139-143` — add Zod
      `.max()` bounds to `humetric_pack_wizard_start`'s `text`, `db_schema`, and `sample_data`
      parameters, following the existing signal-chat pattern
      (`z.string().min(10).max(MAX_SIGNAL_TEXT_CHARS)`, `tools.ts:58,251`) with the limits
      fixed in FR-016 / `contracts/site-wizard-input.md` §C1: `text` **60000**, `db_schema`
      **40000**, `sample_data` **20000** characters, as three named constants
- [ ] T017 [US2] Confirm the Zod validation in T016 runs before any LLM call and before
      `billing.ts` credit deduction for `humetric_pack_wizard_start` (depends on T016)
- [ ] T018 [P] [US2] `../humetric-site` test extending the `tools.charge.test.ts` pattern: a
      60001-character `text` (and a 40001-character `db_schema`) is rejected with a Zod error
      and triggers **no** credit charge; an in-limit input starts the session normally (SC-007)

**Checkpoint**: Oversized wizard input is rejected pre-LLM-call with no credit consumed.

---

## Phase 5: User Story 3 — Değişmez çıkarım kurallarının pack override'ında kaybolmaması (Priority: P2)

**Goal**: The invariant extraction rules (scale, `source_span`, calibration, output example)
always render, even when a pack defines its own prompt — and move the calibration rules that
live in the user message today into the cached system block.

**Independent Test**: Process a signal with a pack that defines `prompts.extraction`; the
rendered system prompt contains both the invariant block and the pack's framing.

### Implementation for User Story 3

- [ ] T019 [US3] `src/humetric/schema.py:468-471` — add `rubric: str = ""` and
      `examples: str = ""` to `PackPrompts`; set `extra="forbid"` on this submodel **only**
      (not on `PackDefinition`, which would break existing packs carrying other extra keys)
- [ ] T020 [US3] `prompts/extractor-default.md` — move the scale/`source_span`/ambiguity
      calibration rules currently embedded in the user message (`extractor.py:64-72`) into the
      invariant system block; the block stays in the prompt file, never inlined into Python
      (CR-003 / Principle VI)
- [ ] T021 [US3] `src/humetric/agents/extractor.py:26` — replace `pack_prompt or
      _DEFAULT_SYSTEM` with a composition: invariant base → pack's `extraction` framing →
      pack's `rubric` → pack's `examples` → allowed-keys block, in that order; when a pack
      defines no prompt fields, the rendered output is byte-identical to today's
      (depends on T019, T020)
- [ ] T022 [US3] `src/humetric/agents/extractor.py` — remove the calibration rules from
      `build_extract_inputs()`'s user message now that they live in the system block
      (depends on T020, T021)
- [ ] T023 [US3] `src/humetric/agents/base.py` / `multi_llm.py` — detect
      `stop_reason == "max_tokens"` (or the provider's equivalent) and raise an explicit error
      instead of silently returning `metrics: []` (FR-009)
- [ ] T024 [US3] Local test: a pack with `prompts.extraction` set still contains the full
      invariant block (scale, `source_span`, calibration, example) in the rendered system
      prompt; a pack that defines a prompt **but no metrics at all** still renders the
      invariant block with an empty allowed-keys section (spec § Edge Cases); a pack YAML with
      an unrecognized prompt key is rejected on save/update (depends on T019, T021)
- [ ] T025 [US3] Re-run the canary against T002's `/tmp/replay_before.json` after applying
      T019–T023: `python -m humetric.replay --pack <pack> --canary-file
      packs/canary/<pack>-canary.yaml --output /tmp/replay_before.json --compare-run --ci`
      (`--compare-run` takes no argument — it reads the file given by `--output` as the previous
      report); confirm any diff is attributed to a
      `prompt_hash` change (`replay.py:438-450`) and record the intentional version note in
      `docs/architecture/context-engineering.md` (`HM-REAS-05`) (depends on T021)

**Checkpoint**: Invariant rules survive pack override; any metric drift is attributable to a
documented `prompt_hash` change, not silent regression.

---

## Phase 6: User Story 4 — Cache'in gerçekten çalıştığını kanıtlamak (Priority: P2)

**Goal**: Prove — with a standing check, not a one-time look — that the second of two
back-to-back calls actually reads from the prompt cache.

**Independent Test**: Process two signals back-to-back with the same pack; the second call's
cache-read token count is greater than zero.

### Implementation for User Story 4

- [ ] T026 [US4] Add a **startup** check that measures the composed prefix with `context.py`
      (T007) against the single configured minimum-cacheable-prefix threshold (T003) and logs a
      `WARNING` when it falls below — a recurring check on every boot, not a one-time audit, so
      a later model or prompt change resurfaces the condition (FR-011). If the prefix is below
      threshold, either grow it deliberately (e.g. more few-shot anchors) or explicitly disable
      `cache_control` for that model, and record the decision in
      `docs/architecture/context-engineering.md`. The conservative default will emit a
      **false-positive** warning on an auto-caching provider that caches below it (measured:
      DeepSeek cached a 762-token prefix) — that is accepted, the warning is advisory and never
      blocks a call; the deployment lowers the threshold for its own model (FR-011)
      (depends on T003, T007, T021)
- [ ] T027 [US4] Verify byte-identical serialization across calls (FR-010): the allowed-keys
      block's line order (stable per pack version, tied to the pack YAML's own metric list
      order) and `tool["input_schema"]` (`model_json_schema()`, deterministic in Pydantic v2) —
      no timestamps, UUIDs, or unordered serialization anywhere in the cached prefix
- [ ] T028 [US4] Create `scripts/verify_cache_prefix.py` — a **committed, key-free** check CI
      can actually run: compose the extraction prefix twice for the same pack, assert the two
      are byte-identical, and assert its measured size clears the configured threshold. Makes
      no provider call. Wire it into `.github/workflows/ci.yml`. This is the standing guard
      against cache regression, since `tests/` is gitignored and CI runs on a dummy key
      (SC-003a) (depends on T007, T026, T027)
- [ ] T029 [US4] Local manual verification against a **real** provider key (`LOCAL_RUN.md`;
      `quickstart.md` step 3): process two signals back-to-back with the same pack and confirm
      the second call's `llm_call_record.cache_read_tokens > 0`. Record the observed numbers in
      `docs/architecture/context-engineering.md` — this is the only place SC-003's second half
      can be proven. **The proof means different things per provider** (SC-003): on the
      auto-caching provider (DeepSeek, the local default) it proves the *measurement* is wired
      correctly, since caching already worked; on the markup-based provider it proves caching
      engaged for the first time. Run it on DeepSeek locally and record the Anthropic half as
      pending until a key is available (depends on T012, T026, T027)
- [ ] T030 [US4] Before extending `cache_control` beyond the Anthropic path in `multi_llm.py`,
      verify each other provider's cache semantics **by measurement**; record verified vs.
      unverified providers in `contracts/llm-call-record.md`'s table — no assumption written as
      fact (Principle II: the branch-local abstraction stays inside `multi_llm.py`).
      **DeepSeek is done**: caching is automatic, so per FR-021 **no `cache_control` markup is
      added to that branch** — the only work there is the read side (T009). **Google is also
      measured (2026-09-09)**: implicit caching, no markup, but it is *thresholded* (dead at
      1.339 tokens, alive at 2.529) **and non-deterministic** (engaged on call 2, gone on call
      3, at both sizes) — so a single `> 0` assert is flaky there; express the Google criterion
      as a cache-read *rate* over several calls. OpenAI remains unverified

**Checkpoint**: Second consecutive call's `cache_read_tokens > 0`, guarded in CI by T028.

---

## Phase 7: User Story 5 — Bütçeyi aşan bağlamın sessizce geçmemesi (Priority: P3)

**Goal**: An over-budget signal is still processed in full — never truncated, never dropped —
but its resulting metrics are flagged for review with the measured token count recorded.

**Independent Test**: Ingest a signal whose context exceeds the budget; the call is made, the
resulting metrics are marked for review, and the trace records the measured overage.

### Implementation for User Story 5

- [ ] T031 [US5] `src/humetric/worker.py:222-232,265` — read `EXTRACT_INPUT_TOKEN_BUDGET`
      (T003, default 32000) and call `measure_extract_inputs()` before the extraction call;
      when `total_tokens` exceeds the budget, still make the call, add `budget_exceeded` as a
      third `review_status` condition alongside `fm.needs_review or not span_verified`, and
      write `budget_exceeded: true` + `measured_input_tokens` into `trace_data`. The trace
      carries **only** those two keys — never signal text or a metric value (CR-002)
      (depends on T007, T013)
- [ ] T032 [US5] Verify (no code change expected) that the consent gate at `worker.py:196-204`
      still runs — and `continue`s for consent-denied metrics — **before** the budget flag, so
      a rejected-consent metric never produces a `trace_data` row at all
- [ ] T033 [US5] Local test: an over-budget signal is processed with the `user` message's bytes
      identical to the non-budget-gated path (no truncation); `review_status=pending_review`,
      `trace_data.budget_exceeded=true`, `trace_data.measured_input_tokens` is numeric. An
      in-budget signal has **no** `budget_exceeded` key at all (key absence == no overage,
      matching existing rows) but **does** carry a numeric `measured_input_tokens` (SC-005)
      (depends on T031)

**Checkpoint**: Over-budget signal is flagged for review, never truncated, never dropped.

---

## Phase 8: User Story 6 — Ucuz sorguların LLM'e hiç gitmemesi (Priority: P3)

**Goal**: Let an API consumer opt a query out of LLM re-ranking, with today's ranked behavior
as the unconditional default.

**Independent Test**: Send a query with re-ranking disabled; results return from hybrid search
alone and no LLM call record is created for that request.

### Implementation for User Story 6

- [ ] T034 [US6] `src/humetric/schema.py:332-340` — add `rerank: bool = True` to
      `QueryRequest` (FR-012). The name is a single word, so **no camelCase alias is defined**;
      the default must stay `True` for backward compatibility (`HM-REAS-05`)
- [ ] T035 [US6] `src/humetric/api.py:1637-1647` — make the `ranker.rank_entities()` call
      conditional on `rerank`; when `False`, return `Store.hybrid_search_entities()`'s own
      ranking with no LLM call; `QueryResponse`/`RankedResult` shapes are unchanged
      (depends on T034)
- [ ] T036 [US6] Local test: `rerank=false` → no new `llm_call_record` row for that request and
      a non-empty result list; `rerank=false` with a query hybrid search matches nothing → an
      empty result list, **not** an error (spec § Edge Cases); field omitted → response
      byte-identical to pre-change behavior (depends on T035)

**Checkpoint**: Opting out skips the LLM call; the unconditional default is unchanged.

---

## Phase 9: User Story 7 — Ödenmiş bir batch'in, asılı bir çağrının ve sınırsız harcamanın işi kilitlememesi (Priority: P3)

**Goal**: A crash after batch submission doesn't lose a paid batch, one non-responding provider
call no longer stalls the whole (sequential) worker queue, and a platform-key tenant cannot
spend without a ceiling.

**Independent Test**: Restart the process after a batch submission — the job resumes from the
persisted batch ID rather than re-submitting; a non-responding provider call times out within a
bounded duration; a platform-key tenant over the daily cap gets `429`.

### Implementation for User Story 7

- [ ] T037 [P] [US7] `src/humetric/batch_worker.py:175-189` — persist the submitted batch job
      ID before the results-write step, so a mid-process crash after submission doesn't lose a
      paid batch on restart (FR-014)
- [ ] T038 [P] [US7] `src/humetric/agents/base.py:260-264` — bound
      `submit_and_await_batch()`'s `while True` polling loop with `BATCH_POLL_MAX_ATTEMPTS`
      (T003); raise on exceeding it (FR-014)
- [ ] T039 [P] [US7] `src/humetric/agents/base.py:144-155` and `multi_llm.py` — add an explicit
      per-call timeout using `LLM_CALL_TIMEOUT_SECONDS` (T003) to every provider LLM call
      (FR-013, SC-009)
- [ ] T040 [US7] `src/humetric/middleware/billing_guard.py` — extend the existing
      `tier_limit_exceeded` pattern with a **daily token cap** for platform-key tenants: when
      the day's recorded token total exceeds `PLATFORM_KEY_DAILY_TOKEN_CAP` (T003), reject the
      request with **`429`** before any LLM call is made. BYOK tenants are exempt — the cost is
      on their own key. No new mechanism is invented (FR-015) (depends on T003)
- [ ] T041 [P] [US7] Verify per-provider batch-discount support (`batch_worker.py:200-218`)
      before assuming Anthropic-only is a permanent constraint; document findings, no behavior
      change if unverified
- [ ] T042 [US7] Local test: kill the process after batch submission but before result
      persistence, restart, confirm the same batch ID is found and results are collected
      without re-submission; a non-responding provider call times out within the configured
      duration and the worker moves to the next queued task (depends on T037, T038, T039)
- [ ] T043 [US7] Local test (SC-011): a platform-key tenant over
      `PLATFORM_KEY_DAILY_TOKEN_CAP` gets `429` and **no** new `llm_call_record` row for that
      request; a BYOK tenant at the same volume is processed normally (depends on T040)

**Checkpoint**: A hung call, a crashed batch, or an uncapped platform-key tenant no longer
stalls the queue or the bill.

---

## Phase 10: Site multi-turn caching & measurement (C2, C3)

**Purpose**: Implements FR-017/FR-018 and `contracts/site-wizard-input.md` §C2–C3. These are
cross-cutting site improvements the plan schedules last, after the engine's own caching
(US3/US4) is proven — they don't map to one of spec.md's seven numbered user stories, so they
carry no `[Story]` label, the same convention as Setup/Foundational/Polish.

- [ ] T044 [P] `../humetric-site/backend/src/promptLoader.ts:32-37` — move
      `injectTenantMemory()` so tenant memory is injected **after** the static system prefix
      instead of before it (call site: `signalGraph/nodes.ts:62`) — a required precondition,
      since a breakpoint before this move gains nothing (the prefix's first bytes differ per
      tenant) (FR-017)
- [ ] T045 `../humetric-site/backend/src/agentic/loop.ts` — add an explicit cache breakpoint at
      the end of the (now tenant-memory-free) static system prefix, and another at the last
      content block of the most recently appended conversation turn, so cache-read volume
      grows with the conversation (depends on T044)
- [ ] T046 [P] `../humetric-site/backend/src/agentic/tokenUsage.ts:60-64` — stop folding cache
      write/read tokens into `input`; return them as separate fields (FR-018)
- [ ] T047 `../humetric-site/backend/schema.sql:179-189` — add cache-read and cache-write
      columns to `llm_token_usage`; existing rows are **not** backfilled (`NULL` means "not
      measured in that period") (depends on T046)
- [ ] T048 [P] `../humetric-site/backend/src/agent/anthropicClient.ts` — add an explicit
      per-call timeout (today only the SDK default applies)
- [ ] T049 Site test: advance a wizard or signal-chat session two turns; assert the second
      turn's cache-read tokens are `> 0` and recorded separately from input tokens in
      `llm_token_usage` (SC-008); assert the cache-read rate does not regress after the
      tenant-memory move in T044 (depends on T044, T045, T046, T047)

**Checkpoint**: Site-side caching actually engages on turn 2+ and is measured apart from input
tokens.

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Final documentation, end-to-end validation, and CI-parity gates across every
phase above.

- [ ] T050 Finalize `docs/architecture/context-engineering.md` (FR-019) with all four contracts
      (bağlam bütçesi, prompt kompozisyonu, cache katmanları, maliyet muhasebesi), the Mermaid
      diagram, the T002 baseline, the FR-011 threshold decision from T026, T029's observed
      cache numbers, the T025 `prompt_hash` version note, and an explicit note on the
      credit/LLM-cost reconciliation gap (`contracts/site-wizard-input.md` §C3 known gap)
      (depends on T001, T002, T025, T026, T029, T031, T049)
- [ ] T051 [P] Re-run `python scripts/cost_bench.py run --signals 20 --entities 5 --llm real
      --embed real --out /tmp/cost_after.json` after all phases and record the
      per-signal token-cost delta against T002's baseline figure in the architecture doc
      (SC-010) (depends on T002)
- [ ] T052 Run `quickstart.md` steps 1–9 end to end: quality gates, measurement accuracy, cache
      proof, budget gate, replay regression, cost diff, and the three site steps
      (depends on all prior phases)
- [ ] T053 Run the CI-parity quality gates: `.venv/bin/ruff check src/`; `python -m py_compile`
      on every changed `.py` file; `.venv/bin/pytest -x -q --tb=short --timeout=30`; and
      `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
- [ ] T054 Re-verify all six Constitution Check gates from `plan.md` and CR-001…CR-006 from
      `spec.md` against the delivered code (Principles I–VI)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately. **T002 gates Phase 3 and every
  later engine phase**: once behavior changes land, the baseline is unrecoverable (FR-020).
- **Foundational (Phase 2)**: Independent of Setup's T002 gate for the migration itself, but
  **no code from Phase 3+ may land before T002**. Blocks US1, US4, US5, and US7 — US7's
  T038/T039/T040 all consume config knobs introduced in T003. Only US2, US3 and US6 are
  independent of it.
- **User Story phases (3–9)**: Follow the plan's fixed order — US1 → US2 → (US3 + US4) → US5 →
  US6 → US7 — because US4 needs US3's grown prefix, and US5 needs US1's `context.py`. US2, US6,
  and US7 have no cross-story dependency and could run in parallel with the others if staffed.
- **Site cross-cutting (Phase 10)**: Independent of every engine phase, of Foundational, and of
  the T002 gate (different repo). Phase 4 (US2) touches the same repo and is useful prior
  context, but is not a dependency.
- **Polish (Phase 11)**: Depends on every phase above.

### Story-Level Dependencies

- **US1 (P1)**: No dependency on other stories; gated by T002. MVP candidate.
- **US2 (P1)**: No dependency on other stories; different repo, can run fully in parallel with
  US1 and is not gated by T002.
- **US3 (P2)**: No dependency on other stories (touches `PackPrompts` and `extractor.py`
  independently).
- **US4 (P2)**: Depends on **US3** (needs the grown, invariant-plus-rubric system block to
  cross the cache threshold) and on **US1** (`context.py`, and the `cache_read_tokens` column
  it queries).
- **US5 (P3)**: Depends on **US1** (`context.py`, `trace_data.measured_input_tokens` plumbing).
- **US6 (P3)**: No dependency on other stories.
- **US7 (P3)**: No dependency on other *stories*, but **depends on Foundational (T003)** for
  `LLM_CALL_TIMEOUT_SECONDS`, `BATCH_POLL_MAX_ATTEMPTS` and `PLATFORM_KEY_DAILY_TOKEN_CAP`.

### Parallel Opportunities

- T004 and T005 (Foundational) touch different files and can run in parallel.
- Within US1: T009 and T010 (different provider branches) are parallel-safe.
- US2 (site repo) can be staffed entirely in parallel with US1 (engine repo) — zero file
  overlap, and it is not behind the FR-020 gate.
- US6 has no dependency on Foundational or on US3/US4/US5 and can be staffed at any time.
- US7's T037, T038, T039 and T041 touch different files and are parallel-safe once T003 is done.
- Phase 10 (site caching) can be staffed in parallel with Phases 7–9 (engine P3 stories) —
  different repos, no shared files.

---

## Parallel Example: User Story 1

```bash
# T007 must land first (the wiring tasks depend on it):
Task: "Create src/humetric/context.py with estimate_tokens() and measure_extract_inputs()"

# Then, in parallel:
Task: "agents/multi_llm.py:~296 — verify and map OpenAI/DeepSeek usage fields"
Task: "agents/multi_llm.py:~420 — verify and map Google usage fields"
```

## Parallel Example: Repo Split

```bash
# Engine repo and site repo have zero file overlap — staff simultaneously:
Task: "US1 — src/humetric/context.py + agents/base.py + agents/multi_llm.py + usage_service.py"
Task: "US2 — ../humetric-site/backend/src/mcpServer/tools.ts wizard input caps"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup) — **including the T002 baseline capture, which cannot be done
   later** — and Phase 2 (Foundational).
2. Complete Phase 3 (US1) — pure measurement, zero behavior change.
3. **STOP and VALIDATE**: query `llm_call_record`, confirm metric values match the T002
   baseline exactly.
4. Deploy — US1 is safe to ship alone per its own acceptance criteria.

### Incremental Delivery (matches the plan's decided order)

1. Setup (baseline) + Foundational → US1 (measure) → validate → deploy.
2. US2 (site wizard cap) → validate → deploy (site repo, independent release).
3. US3 + US4 together (prompt composition unlocks cache) → validate cache proof → deploy.
4. US5 (budget gate) → validate → deploy.
5. US6 (rerank opt-out) → validate → deploy.
6. US7 (batch/timeout/cap resilience) → validate → deploy.
7. Phase 10 (site multi-turn caching) → validate → deploy (site repo).
8. Phase 11 (polish, docs, full quickstart, quality gates).

### Parallel Team Strategy

With two tracks (engine repo + site repo):

- **Engine track**: Setup (T002 gate) → Foundational → US1 → US3+US4 → US5 → US6 → US7 → Polish.
- **Site track**: US2 → Phase 10 (C2/C3) — can start immediately; neither depends on
  Foundational, on the engine's token columns, nor on the FR-020 gate.

---

## Notes

- [P] tasks touch different files and have no unmet dependency.
- Site-repo tasks (`../humetric-site/...`) are never committed to this repo (Principle VI).
- `A1`/`A2`/`A3`/`B1`/`B2`/`B3`/`C1`/`C2`/`C3` in parentheses above are the plan.md phase codes
  the user already agreed to — kept here so the two documents cross-reference cleanly.
- **Local runs use DeepSeek** (tenant 1's `llm_provider`, key in the gitignored `.env`). Live
  measurement on 2026-09-08 showed its caching is automatic and already engages at ~760 tokens,
  which splits SC-003's proof in two and adds FR-021 — see `spec.md` § Clarifications (ikinci
  tur) and `contracts/prompt-composition.md` § Sağlayıcı başına cache.
- T002 is the one task with an irreversible deadline: every other task can be retried, but the
  pre-change baseline disappears the moment behavior moves (FR-020).
- Verify tests fail before implementing, where a test task precedes its implementation task.
- Constitution Check re-verification (T054) is a hard gate, not busywork — plan.md notes all
  six principles PASS today; T054 confirms the delivered code didn't drift from that.
