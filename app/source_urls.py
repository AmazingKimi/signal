"""Destination validation at persistence boundaries; never fetches a page."""
import ipaddress
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def isolated_test_database():
    from . import store
    path=Path(store.DB_PATH).resolve()
    temporary=Path(tempfile.gettempdir()).resolve()
    return (os.environ.get('SIGNAL_TEST_DATA')=='1' and path.is_relative_to(temporary)
            and any(part.startswith(('amazing-kimi-tests-','signal-tests-')) for part in path.relative_to(temporary).parts[:-1]))


def valid_source_url(value):
    try:
        p=urlsplit(str(value or ''))
        host=(p.hostname or '').lower().rstrip('.')
        if p.scheme not in {'https','http'} or not host or p.username or p.password: return False
        if any(ch.isspace() for ch in value): return False
        reserved=('example.com','example.org','example.net','localhost')
        if any(host==x or host.endswith('.'+x) for x in reserved): return False
        if host.endswith(('.test','.invalid','.example','.localhost','.local')) or '.' not in host: return False
        if any(x in {'fixture','mock','dummy','test'} or x.startswith(('fixture-','mock-','dummy-','test-')) for x in host.split('.')): return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except (ValueError,TypeError): return False


def require_source_url(value):
    if not valid_source_url(value) and not isolated_test_database():
        raise ValueError('INVALID_DISCOVERY_SOURCE_URL')
