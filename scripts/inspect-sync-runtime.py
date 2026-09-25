#!/usr/bin/env python3
"""Read only the local Syncthing API; report health without exposing its API key.

Devices and folders are Syncthing's own, paired and shared in its web interface;
a home apply never changes them, and this report pauses or resumes nothing.
"""
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler
import xml.etree.ElementTree as ET

home = Path.home()
tree = ET.parse(home / '.local/state/syncthing/config.xml').getroot()
assert tree.findtext('gui/address') == '127.0.0.1:8384'
assert tree.find('gui').get('tls') == 'false'
key = tree.findtext('gui/apikey')
opener = build_opener(ProxyHandler({}))


def get(path):
    req = Request('http://127.0.0.1:8384/rest/' + path, headers={'X-API-Key': key})
    with opener.open(req, timeout=15) as stream:
        return json.load(stream)


status = get('system/status')
connections = get('system/connections')
folders = get('config/folders')
report = {'device_name': next((d.get('name') for d in tree.findall('device') if d.get('id') == status['myID']),
                               None),
          'connected_peers': sum(bool(v.get('connected')) for v in connections['connections'].values()),
          'all_peers_paused': all(v.get('paused') for v in connections['connections'].values()),
          'folders': [], 'errors': get('system/error').get('errors') or []}
for folder in folders:
    entry = {k: folder.get(k) for k in ('id', 'label', 'path', 'paused', 'type')}
    try:
        data = get('db/status?' + urlencode({'folder': folder['id']}))
        entry['status'] = {k: data.get(k) for k in ('state', 'localFiles', 'globalFiles', 'needFiles', 'needDeletes', 'pullErrors')}
    except HTTPError as error:
        entry['status_http_code'] = error.code
    report['folders'].append(entry)
os.umask(0o077)
output = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'shaffy/runs' / 'sync-runtime/runtime.json'
output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
output.write_text(json.dumps(report, indent=2) + '\n')
os.chmod(output, 0o600)
print(json.dumps({**{k: v for k, v in report.items() if k not in ('folders', 'errors')},
                  'folders': report['folders'], 'error_count': len(report['errors'])}))
