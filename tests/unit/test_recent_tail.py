import json
import threading
from datetime import UTC, datetime

from dayboard.integrations.ai.reader import ClaudeReader


def test_large_file_recent_usage_is_seen_before_backfill_finishes(tmp_path):
    root = tmp_path / "claude"
    (root / "projects").mkdir(parents=True)
    path = root / "projects" / "history.jsonl"
    old = (json.dumps({"type": "user", "content": "PRIVATE_CANARY" * 30}) + "\n").encode()
    recent = {
        "type": "assistant",
        "sessionId": "session-one",
        "version": "2.1.96",
        "timestamp": datetime.now(UTC).isoformat(),
        "message": {
            "id": "recent-message",
            "model": "claude-example",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
    }
    path.write_bytes(old * 120 + json.dumps(recent).encode() + b"\n")
    reader = ClaudeReader(str(root), max_record=1024, byte_budget=4096)
    first = reader.scan(threading.Event())
    assert first.pending
    assert any(item.message_id == "recent-message" for item in first.records)
    assert "CANARY" not in repr(first.records)
    reader.commit(first)
    all_ids = {item.message_id for item in first.records}
    for _ in range(40):
        batch = reader.scan(threading.Event())
        reader.commit(batch)
        all_ids.update(item.message_id for item in batch.records)
        if not batch.pending:
            break
    assert all_ids == {"recent-message"}
    assert not batch.pending
    reader.close()
