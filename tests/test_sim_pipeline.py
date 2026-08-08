import pytest

from srtk.core.errors import ErrorCode, SrtkError
from srtk.core.state import PHASE_PASSED
from srtk.core.transport import DeviceState
from srtk.phases import ORDER, PHASES


def test_full_pipeline(sim_ctx):
    ctx, _ = sim_ctx
    for name in ORDER:
        PHASES[name](ctx).run()
        assert ctx.state.phase_status(name) == PHASE_PASSED, f"{name} failed"
    assert ctx.state.get("unlock_report")["unlocked"] is True
    pi = ctx.state.get("play_integrity")
    assert pi["basic"] is True
    assert pi["device"] is True
    assert (ctx.workspace / "evidence-bundle.zip").exists()
    assert (ctx.workspace / "artifacts" / "final-report.md").exists()


def test_unlock_short_circuits_when_already_unlocked(sim_ctx):
    ctx, _ = sim_ctx
    ctx.state.set("device_report", {"bootloader_unlocked": True})
    from srtk.phases.p03_unlock import P03Unlock

    P03Unlock(ctx).run()
    assert ctx.state.get("unlock_report")["skipped"] is True


def test_detector_states(sim_ctx):
    ctx, device = sim_ctx
    adb = ctx.adb
    state, _ = ctx.detector.detect()
    assert state is DeviceState.DEVICE
    adb.reboot("download")
    state, detail = ctx.detector.detect()
    assert state is DeviceState.DOWNLOAD
    assert "COM" in detail or "COM7" in detail
    adb.reboot("system")
    state, _ = ctx.detector.detect()
    assert state is DeviceState.DEVICE


def test_dry_run_blocks_firmware(sim_ctx):
    ctx, _ = sim_ctx
    ctx.dry_run = True
    ctx.state.set("device_report", {"model": "SM-A325F", "csc": "EUX", "binary": "C"})
    with pytest.raises(SrtkError) as e:
        PHASES["firmware"](ctx).run()
    assert e.value.code is ErrorCode.DRY_RUN_ABORT


def test_flash_gate_requires_unlock(sim_ctx):
    ctx, _ = sim_ctx
    from srtk.phases.p06_flash import P06Flash

    ctx.state.set("firmware", {"parts": {"BL": "x", "AP": "y", "CP": "z", "CSC": "w"}})
    ctx.state.set("magisk_patch", {"patched_tar": "/tmp/p.tar"})
    with pytest.raises(SrtkError) as e:
        P06Flash(ctx).run()
    assert e.value.code is ErrorCode.PHASE_PREREQ_FAIL


def test_pipeline_resume_skips_passed(sim_ctx):
    ctx, _ = sim_ctx
    from srtk.phases import plan_for

    PHASES["preflight"](ctx).run()
    plan = plan_for(ctx.state, ORDER, resume=True)
    assert ("preflight", "skip") in plan
