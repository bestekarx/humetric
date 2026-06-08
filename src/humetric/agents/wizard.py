"""AI Pack Wizard — generates Metric Pack YAML from free-text domain description (Spec 023).

Haiku model ile calisir; cikti PackDefinition schema'sina uygun olmali.
"""

from __future__ import annotations

import logging

from .. import config, telemetry
from ..schema import PackDefinition, PackWizardResponse
from . import _load_prompt

_log = logging.getLogger(__name__)

_DEFAULT_SYSTEM = _load_prompt("wizard-system")
if not _DEFAULT_SYSTEM:
    _DEFAULT_SYSTEM = "You are a Metric Pack design expert. Generate a YAML metric pack from the domain description."


async def generate_pack_yaml(text: str, entity_type_hint: str | None = None, tenant_id: int | None = None, api_key: str | None = None) -> PackWizardResponse:
    """Generate a pack YAML from free-text description."""
    from .base import structured_call

    user_msg = f"Domain description: {text}"
    if entity_type_hint:
        user_msg += f"\nSuggested entity_type: {entity_type_hint}"

    model = getattr(config, "WIZARD_MODEL", config.AGENT_MODEL)
    start = __import__("time").time()
    yaml_text = ""
    validation_errors: list[str] = []
    confidence = 0.5

    try:
        result = await structured_call(
            model=model,
            system=_DEFAULT_SYSTEM,
            user=user_msg,
            schema=PackDefinition,
            tool_name="generate_pack",
            tool_description="Generate a complete Metric Pack YAML definition from the domain description",
            tenant_id=tenant_id,
            api_key=api_key,
        )

        yaml_text = _pack_definition_to_yaml(result)
        try:
            PackDefinition.model_validate(result.model_dump())
        except Exception as exc:
            validation_errors.append(str(exc))
        else:
            confidence = 0.8

    except Exception as exc:
        _log.exception("Wizard generation failed")
        validation_errors.append(f"AI service error: {exc}")
        confidence = 0.0

    elapsed_ms = int((__import__("time").time() - start) * 1000)
    telemetry.log_call(
        agent="wizard",
        model=model,
        usage=type("Usage", (), {"input_tokens": len(text) // 4, "output_tokens": len(yaml_text) // 4})(),
        latency_ms=elapsed_ms,
    )

    return PackWizardResponse(
        pack_yaml=yaml_text,
        validation_errors=validation_errors,
        confidence=confidence,
    )


def _pack_definition_to_yaml(pack: PackDefinition) -> str:
    """PackDefinition modelini YAML string'e cevir."""
    import yaml

    metrics = []
    for m in pack.metrics:
        md = {
            "key": m.key,
            "label": m.label,
            "type": m.type,
            "prompt": m.prompt,
            "default_confidence": m.default_confidence,
            "sensitive": m.sensitive,
            "requires_consent_scope": m.requires_consent_scope,
        }
        if m.visible_to:
            md["visible_to"] = m.visible_to
        metrics.append(md)

    # required_fields list[str | PackFieldDef] olabilir; Pydantic objelerini
    # duz dict'e cevir, aksi halde yaml.dump !!python/object tag'i yazar ve
    # safe_load ile geri okunamaz.
    required_fields = []
    for rf in pack.required_fields:
        if isinstance(rf, str):
            required_fields.append(rf)
        else:
            required_fields.append({"key": rf.key, "type": rf.type, "label": rf.label})

    data = {
        "entity_type": pack.entity_type,
        "label": pack.label,
        "version": pack.version,
        "required_fields": required_fields,
        "metrics": metrics,
        "prompts": {
            "extraction": pack.prompts.extraction,
            "curation": pack.prompts.curation,
        },
        "kvkk": {
            "sensitive_metrics": pack.kvkk.sensitive_metrics,
        },
    }
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
