import json
from datetime import UTC, datetime

from dayboard.integrations.ai.kiro import KiroProvider
from dayboard.settings import KiroSettings


async def test_recorded_credit_decimal_precision_survives_jsonl_collection(tmp_path):
    root = tmp_path / "sessions"
    folder = root / ("a" * 16) / "sess_00000000-0000-4000-8000-000000000001"
    folder.mkdir(parents=True)
    amount = "0.12345678901234567890123"
    text = json.dumps(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": {
                "type": "usage_summary",
                "executionId": "execution-one",
                "promptTurnSummaries": [{"unit": "credit", "usage": "EXACT_AMOUNT"}],
            },
        }
    ).replace('"EXACT_AMOUNT"', amount)
    source = folder / "messages.jsonl"
    source.write_text(text + "\n")
    provider = KiroProvider(KiroSettings(True, str(root)))
    try:
        snapshot = await provider.collect()
        assert next(m.value for m in snapshot.metrics if m.id == "today-credits") == amount
    finally:
        provider.close()
