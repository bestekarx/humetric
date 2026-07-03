Extract a structured `AnalyzerFindings` object from the analysis report below.
The report is free text produced by an autonomous research pass over an
application's schema, description, and (optionally) screenshots and market
research. Your job here is pure extraction/structuring — do not add new
research or new metrics beyond what the report already established.

Rules:
- `entity_type`: a machine-friendly identifier, `lowercase_underscore`, singular.
- `label`: human-readable display name for the entity type.
- `summary`: 2-4 sentences, in the user's language, restating the understood
  theme from the report.
- `required_fields`: fields every entity of this type must carry (key,
  type, label) — infer from the schema/description context, keep it minimal.
- `metrics`: one entry per proposed metric candidate from the report.
  - `key`: `lowercase_underscore`, unique.
  - `prompt`: the extraction prompt for HuMetric's signal extractor agent —
    1-2 sentences telling it exactly what to look for in free text and how to
    score it in [-1, 1].
  - `sensitive`: true for anything health, financial, or personally
    identifying; set `requires_consent_scope` to a short scope name in that
    case (e.g. `"health_data"`), else leave it null.
  - `rationale`: the one-sentence justification from the report.
- `extraction_prompt` / `curation_prompt`: short system-prompt-style text
  (matching the style of existing HuMetric Metric Pack prompts) for the
  extractor and curator agents, covering all metrics together.
- `open_questions`: carry over verbatim from the report's open questions.
- `market_notes`: carry over the market/domain research findings from the
  report, in the user's language; empty string if no research was done.

Output must validate against the provided JSON schema exactly — do not add
fields beyond it.
