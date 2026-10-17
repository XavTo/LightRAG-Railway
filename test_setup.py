import json
import os
from pathlib import Path
import tempfile
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from config_store import config_path, effective_env, load_saved, save_settings
from setup_app import COOKIE, make_setup

class Auth:
    accounts = {'admin': 'test-password', 'member': 'member-password'}
    tokens = {}
    def verify_password(self, username, password): return self.accounts.get(username) == password
    def create_token(self, username, **kw):
        token = 'test-token-' + username
        self.tokens[token] = {'username': username, 'metadata': kw.get('metadata', {})}
        return token
    def validate_token(self, token):
        if token not in self.tokens: raise HTTPException(401)
        return self.tokens[token]

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {'RAILWAY_SETUP_DIR': self.tmp.name, 'TOKEN_SECRET': 'test-token-secret-with-sufficient-entropy', 'ADMIN_USERNAME': 'admin', 'LLM_BINDING': 'openai', 'LLM_MODEL': 'gpt-4o-mini', 'LLM_BINDING_HOST': 'https://api.openai.com/v1', 'EMBEDDING_BINDING': 'openai', 'EMBEDDING_MODEL': 'text-embedding-3-small', 'EMBEDDING_DIM': '1536', 'EMBEDDING_BINDING_HOST': 'https://api.openai.com/v1'}
        self.auth = Auth()
        self.client = TestClient(make_setup(self.auth, self.env), base_url='https://app.example')
    def tearDown(self): self.tmp.cleanup()
    def post(self, path, value, origin='https://app.example'):
        return self.client.post('/setup/' + path, json=value, headers={'Origin': origin, 'X-Setup-Request': '1'})
    def login(self):
        r = self.post('session', {'username': 'admin', 'password': 'test-password'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('HttpOnly', r.headers['set-cookie'])
        self.assertIn('Secure', r.headers['set-cookie'])
        self.assertIn('SameSite=strict', r.headers['set-cookie'])
    def configure(self, **values):
        return self.post('config', {'settings': {'LLM_BINDING_API_KEY': 'synthetic-chat-key', 'EMBEDDING_BINDING_API_KEY': 'synthetic-embedding-key', **values}, 'reuse_chat_key': False})

    def test_no_key_boot_and_authenticated_configuration(self):
        self.assertEqual(self.client.get('/health').json()['status'], 'setup_required')
        self.assertEqual(self.client.get('/setup/config').status_code, 401)
        self.assertEqual(self.configure().status_code, 401)
        self.login()
        self.assertEqual(self.configure().status_code, 200)
        stored = load_saved(self.env)
        self.assertEqual(stored['LLM_BINDING_API_KEY'], 'synthetic-chat-key')
        self.assertNotIn(b'synthetic-chat-key', config_path(self.env).read_bytes())
        self.assertEqual(config_path(self.env).stat().st_mode & 0o777, 0o600)
        response = self.client.get('/setup/config')
        self.assertNotIn('synthetic-chat-key', response.text)
        self.assertNotIn('synthetic-embedding-key', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_cross_origin_and_guest_are_rejected(self):
        self.assertEqual(self.post('session', {'username': 'admin', 'password': 'test-password'}, 'https://evil.example').status_code, 403)
        self.assertEqual(self.post('session', {'username': 'member', 'password': 'member-password'}).status_code, 401)
        self.login()
        self.assertEqual(self.post('config', {'settings': {}}, 'https://evil.example').status_code, 403)
        token = self.auth.create_token('admin', metadata={})
        self.client.cookies.set(COOKIE, token, path='/setup', domain='app.example')
        self.assertEqual(self.client.get('/setup/config').status_code, 403)

    def test_rate_limit(self):
        for _ in range(5): self.assertEqual(self.post('session', {'username': 'admin', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.post('session', {'username': 'admin', 'password': 'wrong'}).status_code, 429)

    def test_railway_keys_override_product_configuration(self):
        save_settings(self.env, {'LLM_BINDING_API_KEY': 'saved-chat', 'LLM_MODEL': 'saved-model', 'EMBEDDING_BINDING_API_KEY': 'saved-embed'})
        railway = dict(self.env, LLM_BINDING_API_KEY='railway-chat', LLM_MODEL='railway-model')
        merged = effective_env(railway)
        self.assertEqual(merged['LLM_BINDING_API_KEY'], 'railway-chat')
        self.assertEqual(merged['LLM_MODEL'], 'railway-model')
        self.assertEqual(merged['EMBEDDING_BINDING_API_KEY'], 'saved-embed')
        c = TestClient(make_setup(self.auth, railway), base_url='https://app.example')
        c.post('/setup/session', json={'username': 'admin', 'password': 'test-password'}, headers={'Origin': 'https://app.example', 'X-Setup-Request': '1'})
        r = c.post('/setup/config', json={'settings': {'LLM_BINDING_API_KEY': 'replace'}}, headers={'Origin': 'https://app.example', 'X-Setup-Request': '1'})
        self.assertEqual(r.status_code, 409)

    def test_chat_key_reuse_and_preservation_after_restart(self):
        self.login()
        self.assertEqual(self.post('config', {'settings': {'LLM_BINDING_API_KEY': 'shared-key'}, 'reuse_chat_key': True}).status_code, 200)
        restored = effective_env(self.env)
        self.assertEqual(restored['EMBEDDING_BINDING_API_KEY'], 'shared-key')
        restored.update({'RAILWAY_SETUP_ORIGINAL_LLM_KEY_PRESENT': 'False', 'RAILWAY_SETUP_ORIGINAL_EMBEDDING_KEY_PRESENT': 'False'})
        c = TestClient(make_setup(self.auth, restored), base_url='https://app.example')
        headers = {'Origin': 'https://app.example', 'X-Setup-Request': '1'}
        c.post('/setup/session', json={'username': 'admin', 'password': 'test-password'}, headers=headers)
        self.assertEqual(c.post('/setup/config', json={'settings': {}}, headers=headers).status_code, 200)
        self.assertEqual(load_saved(self.env)['LLM_BINDING_API_KEY'], 'shared-key')
        self.assertEqual(c.post('/setup/config', json={'settings': {'EMBEDDING_DIM': '3072'}}, headers=headers).status_code, 409)
        self.assertEqual(c.post('/setup/config', json={'settings': {'EMBEDDING_BINDING_HOST': 'https://new.example/v1'}}, headers=headers).status_code, 409)

    def test_invalid_configuration_and_wrong_encryption_key(self):
        self.login()
        for field, value in [('EMBEDDING_DIM', '0'), ('LLM_BINDING_HOST', 'file:///etc/passwd'), ('LLM_BINDING_HOST', 'https://key:secret@example.com/v1')]:
            self.assertEqual(self.configure(**{field: value}).status_code, 400)
        self.assertEqual(self.configure().status_code, 200)
        with self.assertRaises(RuntimeError): load_saved(dict(self.env, TOKEN_SECRET='a-completely-different-token-secret'))

if __name__ == '__main__': unittest.main()
