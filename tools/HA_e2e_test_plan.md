# Home Assistant end-to-end test plan (Docker)

Goal
- Run a lightweight Home Assistant instance in Docker, load the custom component and package YAML, and validate the full wiring: sensor state → decision → script entity call.

Overview
- Use the official Home Assistant container (supervised/core) or the `homeassistant/home-assistant` image for an integration test container.
- Mount a temporary `config/` directory containing:
  - `configuration.yaml` with `shower_guard:` block
  - `custom_components/shower_guard/` copied from the repo
  - `packages/` (the `src/packages/shower_guard.yaml`) and `scripts/` (package scripts)

High-level steps
1. Build a test config dir (example: `./tmp_ha_config`).
2. Copy `custom_components/shower_guard` into `tmp_ha_config/custom_components/shower_guard`.
3. Place `configuration.yaml` that includes `homeassistant:` minimal config and the `shower_guard:` block pointing to your sensor and script entity ids.
4. Mount `tmp_ha_config` into the container and start HA:

```bash
docker run --rm -v $(pwd)/tmp_ha_config:/config -p 8123:8123 homeassistant/home-assistant:stable
```

5. Wait for Home Assistant to start (watch logs or poll `http://localhost:8123`).
6. Use the REST API or WebSocket API to set sensor states (humidity) and presence states. Example via REST (requires long-lived access token):

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"state": "91.83", "attributes": {"unit_of_measurement": "%"}}' \
  http://localhost:8123/api/states/sensor.bathroom_humidity
```

7. Observe script invocation by checking:
  - state of `input_boolean.shower_guard_pump_should_be_off` (should be `on` after cut), or
  - Home Assistant events/logs for `call_service` events targeting `script.shower_guard_cut_water`.

Automation idea for test orchestration
- Use a small Python script that (a) waits for HA to be ready, (b) posts the sequence of humidity states via the REST API, (c) subscribes to the WebSocket event stream or polls the `input_boolean` state to detect the actuator invocation, and (d) exits with success/failure.

Notes and cautions
- Running full HA in CI is heavy — try this locally first. Use a trimmed-down config to speed startup.
- Container images and startup times change; add resilient wait/retry logic in automation.
- For true production safety, consider a hardware-in-the-loop test with the actual actuator device or a mocked actuator that records calls.

If you'd like, I can produce the example `tmp_ha_config` skeleton and the orchestration script next.
