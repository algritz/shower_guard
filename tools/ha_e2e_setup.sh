#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_root="$repo_root/tools/tmp_ha_config"

rm -rf "$config_root"
mkdir -p "$config_root/packages"
mkdir -p "$config_root/scripts"
mkdir -p "$config_root/custom_components"
mkdir -p "$config_root/custom_components/shower_guard"

cp -r "$repo_root/custom_components/shower_guard/" "$config_root/custom_components/shower_guard/"
cp "$repo_root/src/packages/shower_guard.yaml" "$config_root/packages/shower_guard.yaml"

cat > "$config_root/configuration.yaml" <<'EOF'
homeassistant:
  name: Shower Guard Test

logger:
  default: warning
  logs:
    custom_components.shower_guard: debug

automation: []
script: !include_dir_merge_named scripts
packages: !include_dir_named packages

shower_guard:
  humidity_sensor: sensor.third_reality_inc_3rths0224z_humidity
  presence_sensor: binary_sensor.movement_detector_bathroom
  humidity_threshold: 75.0
  cooldown_seconds: 300
  max_humidity_delta: 15.0
  decision_log_size: 100
  water_cut_script: script.shower_guard_cut_water
  water_available_script: script.shower_guard_restore_water
  notify_service: null
EOF

cat > "$config_root/README.md" <<'EOF'
This temporary Home Assistant config is used for local end-to-end tests.
It mounts the repository's `custom_components/shower_guard` integration and
loads the `src/packages/shower_guard.yaml` package.
EOF

chmod -R 755 "$config_root"

echo "Created HA e2e config at: $config_root"
echo "Start Home Assistant with:"
echo "  docker run --rm -v ${config_root}:/config -p 8123:8123 homeassistant/home-assistant:stable"
