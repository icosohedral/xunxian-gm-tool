"""Resolve the game host, with Cloudflare fallback for stale negative DNS caches."""
import ipaddress
import json
import socket
import urllib.parse
import urllib.request


def resolve_game_host(host):
    if host.lower().rstrip('.') != 'game.xunxian.wiki':
        return host
    try:
        socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        return host
    except socket.gaierror:
        query = urllib.parse.urlencode({'name': host, 'type': 'A'})
        req = urllib.request.Request('https://cloudflare-dns.com/dns-query?' + query,
                                     headers={'Accept': 'application/dns-json'})
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.load(response)
        for item in data.get('Answer', []):
            if item.get('type') == 1:
                address = ipaddress.IPv4Address(item['data'])
                if address.is_global:
                    return str(address)
        raise OSError('游戏域名没有可用的公网 IPv4 地址')
