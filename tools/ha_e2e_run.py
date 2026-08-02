#!/usr/bin/env python3
import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

CONFIG_ROOT = os.path.join(os.path.dirname(__file__), 'tmp_ha_config')
BASE_URL = 'http://localhost:8123'
TOKEN = os.environ.get('HA_TOKEN')

if not TOKEN:
    print('ERROR: set HA_TOKEN to a long-lived access token for Home Assistant')
    sys.exit(1)

HEADERS = {
    'Authorization': f'Bearer {TOKEN}',
    'Content-Type': 'application/json',
}


def request(path, method='GET', data=None):
    url = BASE_URL + path
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
    req = Request(url, data=body, method=method, headers=HEADERS)
    try:
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        body = exc.read().decode('utf-8')
        print(f'HTTP {exc.code} {exc.reason}: {body}')
        raise


def wait_for_ha(timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = request('/api/')
            print('HA ready')
            return True
        except Exception:
            time.sleep(5)
    return False


def set_state(entity_id, state, attributes=None):
    data = {'state': state}
    if attributes is not None:
        data['attributes'] = attributes
    return request(f'/api/states/{entity_id}', method='POST', data=data)


def get_state(entity_id):
    return request(f'/api/states/{entity_id}')


def main():
    if not wait_for_ha():
        print('Home Assistant did not become ready in time')
        sys.exit(1)

    print('Setting initial humidity and presence...')
    set_state('sensor.third_reality_inc_3rths0224z_humidity', '75.6', {'unit_of_measurement': '%'})
    set_state('binary_sensor.movement_detector_bathroom', 'on', {'device_class': 'motion'})

    # Simulate a rapid rise and then check pump state
    for state in ['82.63', '87.03', '91.83']:
        print(f'Setting humidity to {state}')
        set_state('sensor.third_reality_inc_3rths0224z_humidity', state, {'unit_of_measurement': '%'})
        time.sleep(5)

    pump_state = get_state('input_boolean.shower_guard_pump_should_be_off')['state']
    print(f'Pump off state: {pump_state}')
    if pump_state != 'on':
        print('Expected pump to be off after water cut')
        sys.exit(1)

    print('Humidity cut triggered successfully')
    sys.exit(0)


if __name__ == '__main__':
    main()
