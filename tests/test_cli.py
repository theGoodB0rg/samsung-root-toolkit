import pytest

from srtk.cli import main
from srtk.core.errors import ErrorCode


def test_help_exits_zero():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


def test_init_creates_workspace(tmp_path):
    rc = main(["--root", str(tmp_path), "init", "job-a"])
    assert rc == 0
    assert (tmp_path / "runs" / "job-a" / "state.json").exists()


def test_status_empty_job(tmp_path):
    rc = main(["--root", str(tmp_path), "--job", "missing", "status"])
    assert rc == 1


def test_status_after_init(tmp_path):
    main(["--root", str(tmp_path), "init", "job-b"])
    rc = main(["--root", str(tmp_path), "--job", "job-b", "status"])
    assert rc == 0


def test_plan_output(tmp_path):
    rc = main(["--root", str(tmp_path), "--sim", "run", "all", "--plan"])
    assert rc == 0


def test_unknown_phase(tmp_path):
    rc = main(["--root", str(tmp_path), "--sim", "run", "nope"])
    assert rc == ErrorCode.PHASE_PREREQ_FAIL.value[0]


def test_full_sim_run(tmp_path):
    rc = main(["--root", str(tmp_path), "--job", "job-sim", "--sim", "run", "all"])
    assert rc == 0, f"sim run failed with rc {rc}"
    assert (tmp_path / "runs" / "job-sim" / "evidence-bundle.zip").exists()
    rc = main(["--root", str(tmp_path), "--job", "job-sim", "status"])
    assert rc == 0


def test_dry_run_sim(tmp_path):
    rc = main(["--root", str(tmp_path), "--job", "job-dry", "--sim", "run", "all", "--dry-run"])
    assert rc == 71  # DRY_RUN_ABORT


def test_runbook(tmp_path):
    rc = main(["--root", str(tmp_path), "runbook", "unlock"])
    assert rc == 0
    rc = main(["--root", str(tmp_path), "runbook"])
    assert rc == 0
