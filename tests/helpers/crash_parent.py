"""仅供集成测试：pytest 会终止此主代理，检查独立控制进程的停止日志。"""

import json
import sys
import time
from pathlib import Path

from vrc_agent.config import AppConfig
from vrc_agent.contracts import ActionProposal, Observation
from vrc_agent.control.service import ControllerProcess, bind_request

if __name__ == "__main__":
    client = ControllerProcess(AppConfig(), Path(sys.argv[1]))
    state = client.request("arm")
    observation = Observation(
        session_id=client.session_id,
        captured_at_ns=time.monotonic_ns(),
        environment_epoch=state["environment_epoch"],
        window_state="visible",
    )
    client.request("observe", observation.model_dump(mode="json"))
    proposal = ActionProposal.model_validate(
        {"kind": "motion", "args": {"forward": 0.5, "duration_ms": 500}}
    )
    action = bind_request(proposal, observation, state)
    result = client.request("submit", action.model_dump(mode="json"))
    assert result["status"] == "RUNNING"
    print(
        json.dumps(
            {
                "session_id": client.session_id,
                "child_pid": client.process.pid,
                "action_id": action.action_id,
            }
        ),
        flush=True,
    )
    while True:
        time.sleep(1)
