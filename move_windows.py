#!/usr/bin/env python3
"""Move explicit Leaf drag payloads without switching desktops or closing Leaf."""
import json
import re
import subprocess
import sys
import time
from select_workspace import ipc, run
from workspace_model import workspace_target


def move(payload, number, monitor):
    number = int(number)
    data = json.loads(payload)
    addresses = data.get('windows') if isinstance(data, dict) else None
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 256:
        raise ValueError('Invalid window drop')
    if not all(isinstance(a, str) and re.fullmatch(r'0x[a-fA-F0-9]+', a) for a in addresses):
        raise ValueError('Invalid window address')
    if monitor not in {m['name'] for m in ipc('monitors')}:
        raise ValueError('Destination monitor disconnected')
    target = workspace_target(number, monitor, ipc('monitors'), ipc('workspaces'))
    clients = {c['address']: c for c in ipc('clients')}
    addresses = list(dict.fromkeys(a for a in addresses if a in clients))
    if not addresses:
        raise ValueError('These windows have already closed')
    for address in addresses:
        run('hyprctl', 'eval', 'hl.dispatch(hl.dsp.window.move({window = ' +
            json.dumps('address:' + address) + ', workspace = ' + json.dumps(str(target)) + ', follow = false}))')

    # Hyprland applies dispatches asynchronously. Poll briefly so a successful
    # move is not reported as a failure just because the first IPC snapshot was
    # taken before the workspace state had caught up.
    deadline = time.monotonic() + 2
    while True:
        clients = {c['address']: c for c in ipc('clients')}
        if all(a not in clients or clients[a].get('workspace', {}).get('id') == target
               for a in addresses):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('A window could not be moved to the selected workspace')
        time.sleep(0.05)


if __name__ == '__main__':
    try:
        move(*sys.argv[1:])
    except Exception as error:
        print(f'Leaf move failed: {error}', file=sys.stderr)
        subprocess.run(['notify-send', 'Leaf', f'Could not move window: {error}'], check=False)
        sys.exit(1)
