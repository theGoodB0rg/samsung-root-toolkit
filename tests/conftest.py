import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def sim_ctx(tmp_path):
    """A RunContext wired to a fresh simulated device session."""
    from srtk.context import Config, RunContext
    from srtk.core.state import RunState
    from srtk.core.transport import Adb, DeviceDetector, Scrcpy
    from srtk.sim_device import SimDevice, SimRunner

    device = SimDevice()
    runner = SimRunner(device)
    ws = tmp_path / "runs" / "test-job"
    ws.mkdir(parents=True)
    ctx = RunContext(
        job_id="test-job",
        workspace=ws,
        state=RunState(ws / "state.json"),
        runner=runner,
        adb=Adb(runner, "adb"),
        detector=DeviceDetector(adb=Adb(runner, "adb"), runner=runner),
        scrcpy=Scrcpy(runner, "scrcpy"),
        config=Config(root=REPO_ROOT),
        sim=True,
    )
    ctx.ensure_dirs()
    return ctx, device
