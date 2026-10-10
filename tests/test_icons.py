from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.service import Service
from backend.store import Store


def test_runtime_brand_assets_are_public_valid_and_versioned(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-only')
    service = Service(Store(tmp_path / 'state.sqlite3'))
    service.start = lambda: None
    try:
        client = TestClient(create_app(service))
        assert client.get('/api/policy').status_code == 401
        svg = client.get('/icons/icon.svg?v=a2')
        assert svg.status_code == 200 and svg.headers['content-type'].startswith('image/svg+xml')
        assert 'Docker Monitor' in svg.text and '<rect x="126"' in svg.text
        for name, size in [('icon-32.png',32),('icon-180.png',180),('icon-192.png',192),('icon-512.png',512),('icon-maskable-512.png',512)]:
            response = client.get('/icons/' + name + '?v=a2')
            assert response.status_code == 200
            image = Image.open(BytesIO(response.content))
            assert image.size == (size,size) and image.format == 'PNG'
        assert client.head('/icons/icon-192.png').status_code == 200
        assert Image.open(BytesIO(client.get('/favicon.ico').content)).format == 'ICO'
        assert client.get('/icons/not-an-icon.png').status_code == 404
        manifest = client.get('/manifest.webmanifest?v=a2').json()
        assert all('?v=a2' in item['src'] for item in manifest['icons'])
        assert '/icons/icon.svg?v=a2' in client.get('/login').text
    finally:
        service.close()
