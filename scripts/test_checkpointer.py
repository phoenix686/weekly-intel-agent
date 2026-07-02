from checkpointer_config import get_checkpointer

cp = get_checkpointer()

write_config = {"configurable": {"thread_id": "test-thread-1", "checkpoint_ns": ""}}
checkpoint = {
    "v": 4,
    "ts": "2026-07-02T00:00:00.000000+00:00",
    "id": "test-checkpoint-1",
    "channel_values": {"my_key": "meow"},
    "channel_versions": {"my_key": 1},
    "versions_seen": {},
    "pending_sends": [],
}
cp.put(write_config, checkpoint, {}, {})

read_config = {"configurable": {"thread_id": "test-thread-1"}}
result = cp.get_tuple(read_config)
print("Round-trip result:", result)