PYTHONPATH=. python3 - <<'EOF'
import sys
from unittest.mock import MagicMock
for mod in ("homeassistant", "homeassistant.core", "homeassistant.config_entries",
            "homeassistant.helpers", "homeassistant.helpers.event"):
    sys.modules[mod] = MagicMock()

import sys
sys.argv = ["replay", "test_data/readings.csv",
            "--humidity-threshold", "75.0",
            "--cooldown-seconds", "60",
            "--max-humidity-delta", "15.0"]

from custom_components.shower_guard.replay import _main
_main()
EOF