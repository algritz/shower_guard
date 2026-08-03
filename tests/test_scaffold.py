# ---
# purpose: Smoke tests confirming the v0.1 scaffold is importable and constants are correct.
# version: 0.1.0
# ---

import sys
import os
from unittest.mock import MagicMock

# Allow importing the custom component without a full HA environment.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub homeassistant modules so tests run outside a live HA instance.
for _mod in (
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.helpers",
    "homeassistant.helpers.event",
):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["homeassistant.const"].STATE_UNKNOWN = "unknown"


def test_const_domain():
    """DOMAIN must equal 'shower_guard'."""
    from custom_components.shower_guard.const import DOMAIN
    assert DOMAIN == "shower_guard"


def test_const_version():
    """VERSION must be set and follow semver major.minor.patch format."""
    from custom_components.shower_guard.const import VERSION
    parts = VERSION.split(".")
    assert len(parts) == 3, f"VERSION '{VERSION}' is not semver"
    assert all(p.isdigit() for p in parts), f"VERSION '{VERSION}' contains non-numeric parts"


def test_manifest_exists():
    """manifest.json must exist alongside the package."""
    manifest_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "shower_guard",
        "manifest.json",
    )
    assert os.path.isfile(manifest_path), "manifest.json is missing"


def test_manifest_domain():
    """manifest.json domain must match DOMAIN constant."""
    import json
    from custom_components.shower_guard.const import DOMAIN

    manifest_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "shower_guard",
        "manifest.json",
    )
    with open(manifest_path) as f:
        manifest = json.load(f)

    assert manifest["domain"] == DOMAIN
