#!/usr/bin/env python3
"""Synchronize the GCE public IPv4 with game login and Cloudflare DNS."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ZONE = 'xunxian.wiki'
HOST = 'game.xunxian.wiki'
CONFIG = Path('/opt/xxserver/runtime/xx/shm/XCLS2/cls2_cfg1.txt')
TOKEN = Path('/etc/xunxian-cloudflare-token')
METADATA = 'http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip'


def public_ip():
    request = urllib.request.Request(METADATA, headers={'Metadata-Flavor': 'Google'})
    with urllib.request.urlopen(request, timeout=5) as response:
        address = ipaddress.IPv4Address(response.read().decode().strip())
    if not address.is_global:
        raise ValueError('Metadata did not provide a public IPv4 address')
    return str(address)


def update_login(address):
    old = CONFIG.read_bytes()
    pattern = rb'(?m)^(#define[ \t]+FAKEWELCOMEADDR[ \t]+)[^\r\n]+(\r?)$'
    new, count = re.subn(pattern, lambda m: m.group(1) + address.encode('ascii') + b':0' + m.group(2), old)
    if count != 1:
        raise ValueError('Expected exactly one FAKEWELCOMEADDR setting')
    if new == old:
        return
    stat = CONFIG.stat()
    fd, name = tempfile.mkstemp(prefix='.cls2-ip-', dir=CONFIG.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, stat.st_mode & 0o777)
        os.replace(name, CONFIG)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def update_dns(address):
    token = TOKEN.read_text().strip()
    if not token:
        raise ValueError('Cloudflare token is empty')
    def api(method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request('https://api.cloudflare.com/client/v4' + path, data=body,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError('Cloudflare API HTTP ' + str(error.code)) from None
        if not result.get('success'):
            raise RuntimeError('Cloudflare API reported an error')
        return result['result']
    zones = api('GET', '/zones?' + urllib.parse.urlencode({'name': ZONE, 'status': 'active'}))
    if len(zones) != 1:
        raise RuntimeError('Expected one active Cloudflare zone')
    base = '/zones/' + zones[0]['id'] + '/dns_records'
    records = api('GET', base + '?' + urllib.parse.urlencode({'name': HOST, 'per_page': 100}))
    if any(r['type'] != 'A' for r in records) or len(records) > 1:
        raise RuntimeError('Conflicting or duplicate DNS records; no changes made')
    if records and records[0].get('content') == address and records[0].get('proxied') is False:
        return
    payload = {'type': 'A', 'name': HOST, 'content': address, 'ttl': 120, 'proxied': False}
    if records:
        api('PATCH', base + '/' + records[0]['id'], payload)
    else:
        api('POST', base, payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('login', 'dns'))
    args = parser.parse_args()
    address = public_ip()
    (update_login if args.mode == 'login' else update_dns)(address)
    print(args.mode + ' synchronized to ' + address)


if __name__ == '__main__':
    main()
