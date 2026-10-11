def csrf_headers(client, **extra):
    response = client.get('/api/auth/csrf', headers={'X-Monitor-Request': '1',
        'X-Monitor-Client-Scheme': client.base_url.scheme})
    assert response.status_code == 200
    return {'X-Monitor-Request': '1', 'X-CSRF-Token': response.json()['token'], **extra}
