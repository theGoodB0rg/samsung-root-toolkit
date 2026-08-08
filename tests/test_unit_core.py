import pytest

from srtk.core.errors import ErrorCode, SrtkError, wrap_internal
from srtk.core.hashing import sha256_file, verify_manifest, write_manifest
from srtk.core.state import PHASE_PASSED, RunState
from srtk.core.versioning import (
    binary_is_compatible,
    extract_binary,
    normalize_model,
    parse_version,
)


def test_error_codes_are_stable():
    assert ErrorCode.KG_PRENORMAL.value[0] == 32
    assert ErrorCode.PI_DEVICE_FAIL.value[0] == 61
    assert ErrorCode.INTERNAL_ERROR.value[0] == 90
    err = SrtkError(ErrorCode.TOOL_NOT_FOUND, context="adb", details="x")
    assert err.exit_code == 10


def test_wrap_internal():
    err = wrap_internal(ValueError("boom"), context="preflight")
    assert err.code is ErrorCode.INTERNAL_ERROR
    assert err.exit_code == 90
    assert "ValueError" in err.details


def test_parse_version():
    v = parse_version("A325FXXU3BVE1")
    assert v.model == "A325F"
    assert v.csc == "XXU"
    assert v.binary == "3"
    assert v.binary_int == 3
    v2 = parse_version("A325FXXSCDYB2")
    assert v2.binary == "C"
    assert v2.binary_int == 12
    assert parse_version("garbage") is None


def test_extract_binary():
    assert extract_binary("A325FXXSCDYB2") == "C"
    assert extract_binary("A325FXXU3BVE1") == "3"
    assert extract_binary("not a version") is None


def test_binary_compat():
    assert binary_is_compatible("C", "C")[0] is True
    assert binary_is_compatible("C", "D")[0] is True
    assert binary_is_compatible("C", "A")[0] is False
    assert binary_is_compatible(None, "C")[0] is False


def test_normalize_model():
    assert normalize_model("SM-A325F") == "SM-A325F"


def test_state_roundtrip(tmp_path):
    rs = RunState(tmp_path / "state.json")
    rs.start_phase("preflight")
    rs.finish_phase("preflight", PHASE_PASSED, artifacts=["a.json"])
    rs.set("notes", ["x"])
    rs.save()
    rs2 = RunState(tmp_path / "state.json")
    assert rs2.is_passed("preflight")
    assert rs2.get("notes") == ["x"]


def test_manifest_verify(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello" * 1000)
    m = tmp_path / "m.json"
    write_manifest(m, {f.name: sha256_file(f)})
    assert verify_manifest(m, base=tmp_path) == {f.name: sha256_file(f)}
    f.write_bytes(b"tampered")
    with pytest.raises(SrtkError) as e:
        verify_manifest(m, base=tmp_path)
    assert e.value.code is ErrorCode.HASH_MISMATCH


def test_plan_for_prereqs_and_resume(tmp_path):
    from srtk.phases import ORDER, plan_for, validate_targets

    rs = RunState(tmp_path / "s.json")
    plan = plan_for(rs, ["flash"])
    names = [n for n, a in plan]
    for prereq in ("preflight", "device_info", "unlock", "firmware", "magisk_patch"):
        assert prereq in names
    assert names.index("preflight") < names.index("flash")

    rs.start_phase("preflight")
    rs.finish_phase("preflight", PHASE_PASSED)
    plan2 = plan_for(rs, ["preflight", "device_info"], resume=True)
    assert ("preflight", "skip") in plan2


def test_validate_targets_unknown():
    from srtk.phases import validate_targets

    with pytest.raises(SrtkError):
        validate_targets(["nope"])
