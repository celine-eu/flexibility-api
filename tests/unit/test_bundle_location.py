"""Where the Rego bundle is loaded from, and that the wheel carries it.

The image installs this service as a wheel into site-packages and shipped the bundle as
`/app/policies`, which the `__file__`-derived lookup never reached: the image ran with no
bundle (permissive in dev, refused elsewhere). The bundle now travels inside the package,
and the lookup never consults the working directory (REQ-0011).
"""
from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

from celine.flexibility.security import policy as policy_module
from celine.flexibility.security.policy import AccessPolicy, policies_dir

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "policies" / "flexibility.rego"


@pytest.fixture
def no_override(monkeypatch):
    monkeypatch.delenv("CELINE_POLICIES_POLICIES_DIR", raising=False)


def _bundle_in(directory: Path) -> Path:
    directory.mkdir(parents=True)
    shutil.copy(BUNDLE, directory / BUNDLE.name)
    return directory


# @verifies REQ-0011
def test_the_wheel_packages_the_bundle_next_to_the_code():
    """What puts `celine/flexibility/policies/` into the installed package."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text())
    force = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force["policies"] == "celine/flexibility/policies"
    assert policy_module._PACKAGED_POLICIES.parts[-3:] == ("celine", "flexibility", "policies")


# @verifies REQ-0011
def test_an_installed_package_loads_its_packaged_bundle(tmp_path, monkeypatch, no_override):
    """The image's shape: the bundle beside the code, no source checkout."""
    packaged = _bundle_in(tmp_path / "site-packages" / "celine" / "flexibility" / "policies")
    monkeypatch.setattr(policy_module, "_PACKAGED_POLICIES", packaged)
    monkeypatch.setattr(policy_module, "_CHECKOUT_POLICIES", tmp_path / "no-checkout")

    assert policies_dir() == packaged
    assert AccessPolicy().loaded


# @verifies REQ-0011
def test_the_working_directory_is_never_consulted(tmp_path, monkeypatch, no_override):
    """A `policies/` in the cwd is not a bundle this service trusts, and its absence
    does not unload the real one."""
    _bundle_in(tmp_path / "policies")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(policy_module, "_PACKAGED_POLICIES", tmp_path / "none-a")
    monkeypatch.setattr(policy_module, "_CHECKOUT_POLICIES", tmp_path / "none-b")

    assert not AccessPolicy().loaded


# @verifies REQ-0011
def test_a_source_checkout_loads_the_repository_bundle(tmp_path, monkeypatch, no_override):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(policy_module, "_PACKAGED_POLICIES", tmp_path / "not-installed")

    assert policies_dir() == REPO / "policies"
    assert AccessPolicy().loaded


# @verifies REQ-0011
def test_a_configured_directory_wins(tmp_path, monkeypatch):
    mounted = _bundle_in(tmp_path / "mounted")
    monkeypatch.setenv("CELINE_POLICIES_POLICIES_DIR", str(mounted))

    assert policies_dir() == mounted
    assert AccessPolicy().loaded
