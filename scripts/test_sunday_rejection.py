from langgraph.types import Command
from checkpointer_config import get_checkpointer
# rebuild compiled/config exactly as in test_sunday_approval.py, same thread_id

resume_map = {
    "b8048ac1f9d7c37ef1ff451f75161837": "reject",
    "7f89b75200aaa4dcc87171d1361d22c2": "reject",
    "fd9e54b69428c25c2b792ac349fba921": "reject",
    "7f71fb80398fb1d2e702a6dcd1fc9bef": "reject",
}
final = compile.invoke(Command(resume=resume_map), config=config)