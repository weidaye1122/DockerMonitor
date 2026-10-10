"""Repair legacy Config.Image references through the authenticated task API."""
import os
import sys
import time

import docker
import requests

from .engine import SOURCE_LABEL


def retained(attrs):
    config = dict(attrs['Config'])
    config.pop('Image', None)
    labels = dict(config.get('Labels') or {})
    labels.pop('docker-monitor.job', None)
    config['Labels'] = labels
    host = dict(attrs['HostConfig'])
    # The updater explicitly binds the same anonymous volume; compare effective mounts.
    host.pop('Mounts', None)
    mounts = sorted((m['Type'], m.get('Name') or m.get('Source'), m['Destination'], m.get('RW')) for m in attrs['Mounts'])
    networks = {n: {'IPAMConfig': e.get('IPAMConfig'), 'Aliases': sorted(a for a in (e.get('Aliases') or [])
                  if a not in (attrs['Id'], attrs['Id'][:12], attrs.get('Name','').lstrip('/')))}
                for n,e in attrs['NetworkSettings']['Networks'].items()}
    return config, host, mounts, networks


def main(names):
    if not names:
        raise SystemExit('Specify exact container names to repair')
    session = requests.Session()
    session.trust_env = False
    session.headers['X-Monitor-Request'] = '1'
    base = 'http://127.0.0.1:8080'
    def request(method, path, **kwargs):
        response = session.request(method, base + path, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError('Repair API refused the request')
        return response.json()
    request('POST', '/api/auth/login', json={'username':os.environ.get('ADMIN_USERNAME','admin'), 'password':os.environ.get('ADMIN_PASSWORD','')})
    if request('GET','/api/policy')['auto']:
        raise RuntimeError('Pause automatic updates before legacy repair')
    client = docker.from_env(timeout=30)
    for name in names:
        old = client.containers.get(name)
        before = old.attrs
        source = (before['Config'].get('Labels') or {}).get(SOURCE_LABEL)
        if not before['Config']['Image'].startswith('sha256:'):
            print(name + ': already has a normal image reference', flush=True)
            continue
        if not source or source.startswith('sha256:'):
            raise RuntimeError(name + ': missing original image reference')
        job_id = request('POST', '/api/containers/' + old.id + '/actions', json={'action':'repair'})['jobId']
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = request('GET','/api/jobs/' + job_id)
            if job['status'] not in ('queued','running'):
                break
            time.sleep(1)
        if job['status'] != 'success':
            raise RuntimeError(name + ': ' + job['message'])
        current = client.containers.get(name)
        after = current.attrs
        if after['Image'] != before['Image'] or after['Config']['Image'] != source or not after['State']['Running']:
            raise RuntimeError(name + ': post-repair image/state verification failed')
        if retained(before) != retained(after):
            raise RuntimeError(name + ': post-repair configuration verification failed')
        print(name + ': ' + job['message'] + '; original image and configuration preserved; ' + source, flush=True)
    print('Legacy repair completed', flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
