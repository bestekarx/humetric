"""Batch worker multi-provider routing testleri.

Anthropic tenant'lar native Batches API'yi kullanmaya devam etmeli;
diger sagl ayicilar (openai/google/deepseek) senkron structured_call_multi
fallback'ine dusmeli (bkz. batch_worker.py Phase A1/A2).

DB'ye baglanmadan calisir: Store/base cagrilari tamamen mock'lanir.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from humetric import batch_worker
from humetric.schema import ExtractionResult


def _make_context(task_id: int, tenant_id: int, provider: str) -> dict:
    task = SimpleNamespace(id=task_id, tenant_id=tenant_id)
    return {
        "task": task,
        "entity": MagicMock(),
        "entity_id": f"ent-{task_id}",
        "pack_def": {},
        "ctx": "",
        "input_hash": "hash",
        "occurred_at": None,
        "llm_provider": provider,
        "llm_key": f"key-{provider}",
        "extract_system": "sys",
        "extract_user": "user",
        "extract_meta": {"model": "test-model", "prompt_hash": "p", "schema_hash": "s"},
        "curator_meta": {},
        "error": None,
    }


def _extraction_result() -> ExtractionResult:
    return ExtractionResult(metrics=[])


@pytest.mark.asyncio
async def test_batch_worker_anthropic_uses_native_batch_api():
    contexts = [_make_context(1, 100, "anthropic")]

    with patch.object(batch_worker.Store, "get_next_task", new=AsyncMock(return_value=[SimpleNamespace(id=1)])), \
         patch.object(batch_worker, "_prepare_task", new=AsyncMock(side_effect=contexts)), \
         patch.object(batch_worker, "_set_tenant", new=AsyncMock()), \
         patch.object(batch_worker.base, "build_batch_request", return_value={"custom_id": "1"}) as mock_build, \
         patch.object(batch_worker.base, "submit_and_await_batch", new=AsyncMock(return_value={"1": SimpleNamespace(type="succeeded", message=MagicMock())})) as mock_submit, \
         patch.object(batch_worker.base, "parse_batch_result", return_value=_extraction_result()), \
         patch.object(batch_worker, "structured_call_multi", new=AsyncMock()) as mock_multi, \
         patch.object(batch_worker.Store, "get_entity_metrics", new=AsyncMock(return_value=[])), \
         patch.object(batch_worker.curator, "finalize_merge", return_value=[]), \
         patch.object(batch_worker, "_persist_signal_result", new=AsyncMock()), \
         patch.object(batch_worker.Store, "complete_task", new=AsyncMock()), \
         patch.object(batch_worker.base, "record_batch_usage", new=AsyncMock()):
        db = AsyncMock()
        n = await batch_worker.run_batch_once(db)

    assert n == 1
    mock_build.assert_called_once()
    mock_submit.assert_called_once()
    mock_multi.assert_not_called()


@pytest.mark.asyncio
async def test_batch_worker_non_anthropic_uses_structured_call_multi():
    contexts = [_make_context(2, 200, "openai")]

    with patch.object(batch_worker.Store, "get_next_task", new=AsyncMock(return_value=[SimpleNamespace(id=2)])), \
         patch.object(batch_worker, "_prepare_task", new=AsyncMock(side_effect=contexts)), \
         patch.object(batch_worker, "_set_tenant", new=AsyncMock()), \
         patch.object(batch_worker.base, "build_batch_request") as mock_build, \
         patch.object(batch_worker.base, "submit_and_await_batch", new=AsyncMock()) as mock_submit, \
         patch.object(batch_worker, "structured_call_multi", new=AsyncMock(return_value=_extraction_result())) as mock_multi, \
         patch.object(batch_worker.Store, "get_entity_metrics", new=AsyncMock(return_value=[])), \
         patch.object(batch_worker.curator, "finalize_merge", return_value=[]), \
         patch.object(batch_worker, "_persist_signal_result", new=AsyncMock()), \
         patch.object(batch_worker.Store, "complete_task", new=AsyncMock()), \
         patch.object(batch_worker.base, "record_batch_usage", new=AsyncMock()) as mock_usage:
        db = AsyncMock()
        n = await batch_worker.run_batch_once(db)

    assert n == 1
    mock_build.assert_not_called()
    mock_submit.assert_not_called()
    mock_multi.assert_called_once()
    _, kwargs = mock_multi.call_args
    assert kwargs["provider"] == "openai"
    assert kwargs["api_key"] == "key-openai"
    # No Anthropic batch messages were produced, so the post-hoc batch usage
    # accounting loop has nothing to iterate over for this tenant.
    mock_usage.assert_not_called()


@pytest.mark.asyncio
async def test_batch_worker_mixed_providers_splits_correctly():
    contexts = [_make_context(3, 300, "anthropic"), _make_context(4, 400, "google")]

    with patch.object(batch_worker.Store, "get_next_task", new=AsyncMock(return_value=[SimpleNamespace(id=3), SimpleNamespace(id=4)])), \
         patch.object(batch_worker, "_prepare_task", new=AsyncMock(side_effect=contexts)), \
         patch.object(batch_worker, "_set_tenant", new=AsyncMock()), \
         patch.object(batch_worker.base, "build_batch_request", return_value={"custom_id": "3"}) as mock_build, \
         patch.object(batch_worker.base, "submit_and_await_batch", new=AsyncMock(return_value={"3": SimpleNamespace(type="succeeded", message=MagicMock())})) as mock_submit, \
         patch.object(batch_worker.base, "parse_batch_result", return_value=_extraction_result()), \
         patch.object(batch_worker, "structured_call_multi", new=AsyncMock(return_value=_extraction_result())) as mock_multi, \
         patch.object(batch_worker.Store, "get_entity_metrics", new=AsyncMock(return_value=[])), \
         patch.object(batch_worker.curator, "finalize_merge", return_value=[]), \
         patch.object(batch_worker, "_persist_signal_result", new=AsyncMock()), \
         patch.object(batch_worker.Store, "complete_task", new=AsyncMock()), \
         patch.object(batch_worker.base, "record_batch_usage", new=AsyncMock()):
        db = AsyncMock()
        n = await batch_worker.run_batch_once(db)

    assert n == 2
    mock_submit.assert_called_once()
    mock_multi.assert_called_once()
    _, kwargs = mock_multi.call_args
    assert kwargs["provider"] == "google"


@pytest.mark.asyncio
async def test_batch_worker_non_anthropic_extraction_error_sets_error_field():
    contexts = [_make_context(5, 500, "deepseek")]

    with patch.object(batch_worker.Store, "get_next_task", new=AsyncMock(return_value=[SimpleNamespace(id=5)])), \
         patch.object(batch_worker, "_prepare_task", new=AsyncMock(side_effect=contexts)), \
         patch.object(batch_worker, "_set_tenant", new=AsyncMock()), \
         patch.object(batch_worker, "structured_call_multi", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(batch_worker, "handle_failure", new=AsyncMock()) as mock_handle_failure, \
         patch.object(batch_worker, "_persist_signal_result", new=AsyncMock()) as mock_persist, \
         patch.object(batch_worker.Store, "complete_task", new=AsyncMock()), \
         patch.object(batch_worker.base, "record_batch_usage", new=AsyncMock()):
        db = AsyncMock()
        n = await batch_worker.run_batch_once(db)

    assert n == 1
    mock_handle_failure.assert_called_once()
    mock_persist.assert_not_called()
