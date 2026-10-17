"""Small admin-only setup surface. Keys are never returned to the browser."""
import asyncio
from collections import defaultdict, deque
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config_store import GROUPS, effective_env, load_saved, ready, save_settings, validate

COOKIE = '__Secure-lightrag-setup'

def make_setup(auth, env=None):
    env = dict(os.environ) if env is None else dict(env)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    attempts = defaultdict(lambda: deque(maxlen=10))
    html = Path(__file__).with_name('setup.html').read_text()
    locked = {p: env.get('RAILWAY_SETUP_ORIGINAL_' + p + '_KEY_PRESENT', str(bool(env.get(p + '_BINDING_API_KEY', '').strip()))).lower() == 'true' for p in GROUPS}

    @app.middleware('http')
    async def headers(request, next_call):
        response = await next_call(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})
        return response

    async def body(request):
        if request.headers.get('x-setup-request') != '1' or request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(403, 'Same-origin JSON requests are required')
        origin = urlsplit(request.headers.get('origin', ''))
        if origin.netloc != request.headers.get('host') or origin.scheme not in ('https', 'http'):
            raise HTTPException(403, 'Origin rejected')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 16384: raise HTTPException(413, 'Request too large')
        try:
            result = json.loads(data)
            if not isinstance(result, dict): raise ValueError()
            return result
        except (ValueError, TypeError): raise HTTPException(400, 'Invalid request') from None

    def admin(request):
        try: payload = auth.validate_token(request.cookies.get(COOKIE, ''))
        except Exception: raise HTTPException(401, 'Sign in with the administrator account') from None
        username = env.get('ADMIN_USERNAME', 'admin')
        if payload.get('username') != username or username not in auth.accounts or payload.get('metadata', {}).get('railway_setup') is not True:
            raise HTTPException(403, 'Administrator access required')

    @app.get('/setup')
    @app.get('/setup/')
    async def page(): return HTMLResponse(html)

    @app.post('/setup/session')
    async def login(request: Request):
        data = await body(request)
        ip = request.client.host if request.client else 'unknown'
        now = time.monotonic()
        bucket = attempts[ip]
        while bucket and now - bucket[0] >= 300: bucket.popleft()
        if len(bucket) >= 5: raise HTTPException(429, 'Too many attempts. Try again in five minutes.')
        bucket.append(now)  # reserve before awaiting password verification
        username, password = data.get('username'), data.get('password')
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 256 or len(password) > 1024:
            raise HTTPException(401, 'Incorrect credentials')
        valid = await asyncio.to_thread(auth.verify_password, username, password)
        if not valid or username != env.get('ADMIN_USERNAME', 'admin') or username not in auth.accounts:
            raise HTTPException(401, 'Incorrect credentials')
        bucket.clear()
        response = JSONResponse({'ok': True})
        response.set_cookie(COOKIE, auth.create_token(username=username, custom_expire_hours=1, metadata={'railway_setup': True}), httponly=True, secure=True, samesite='strict', path='/setup', max_age=3600)
        return response

    @app.post('/setup/logout')
    async def logout(request: Request):
        await body(request)
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/setup', secure=True, httponly=True, samesite='strict')
        return response

    @app.get('/setup/config')
    async def get_config(request: Request):
        admin(request)
        saved = load_saved(env)
        current = effective_env(env, saved)
        groups = {}
        for prefix, fields in GROUPS.items():
            groups[prefix] = {'locked': locked[prefix], 'source': 'Railway' if locked[prefix] else 'Product', 'has_key': bool(current.get(prefix + '_BINDING_API_KEY')), 'binding': current.get(prefix + '_BINDING', 'openai'), 'values': {key: current.get(key, '') for key in fields if not key.endswith('_API_KEY')}}
        return {'ready': ready(current), 'reuse_chat_key': current.get('LLM_BINDING_API_KEY', '') == current.get('EMBEDDING_BINDING_API_KEY', ''), 'groups': groups}

    @app.post('/setup/config')
    async def configure(request: Request):
        admin(request)
        data = await body(request)
        if set(data) - {'settings', 'reuse_chat_key'}: raise HTTPException(400, 'Unsupported request fields')
        try: changes = validate(data.get('settings'))
        except ValueError as exc: raise HTTPException(400, str(exc)) from None
        saved = load_saved(env)
        for prefix, fields in GROUPS.items():
            if locked[prefix] and any(key in changes for key in fields):
                raise HTTPException(409, 'This provider is configured in Railway. Update its Railway variables instead.')
            if not locked[prefix]:
                for key in fields:
                    if key in changes: saved[key] = changes[key]
        if data.get('reuse_chat_key') is True and not locked['EMBEDDING']:
            saved['EMBEDDING_BINDING_API_KEY'] = saved.get('LLM_BINDING_API_KEY') or env.get('LLM_BINDING_API_KEY', '')
        # Applying a saved group must not change a Railway-owned group.
        current = dict(env)
        for prefix, fields in GROUPS.items():
            if not locked[prefix]:
                current.update({key: saved[key] for key in fields if saved.get(key)})
        if not ready(current): raise HTTPException(400, 'Provide credentials for both chat and embeddings')
        # Once configured, embedding semantics stay fixed to protect existing indexes.
        for key in ['EMBEDDING_MODEL', 'EMBEDDING_DIM', 'EMBEDDING_BINDING_HOST']:
            old = env.get(key)
            if ready(env) and old and current.get(key) != old:
                raise HTTPException(409, 'Changing the embedding model, host or dimension requires an index migration. Use Railway variables after planning the migration.')
        save_settings(env, saved)
        return {'ok': True, 'restarting': True, 'message': 'Saved. LightRAG is restarting with your provider configuration.'}

    @app.get('/health')
    async def setup_health(): return {'status': 'setup_required', 'provider_configured': False}

    @app.get('/')
    async def root(): return RedirectResponse('/setup', status_code=307)

    return app

class Dispatch:
    def __init__(self, setup, upstream): self.setup, self.upstream = setup, upstream
    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] == 'http' and (path == '/setup' or path.startswith('/setup/')):
            await self.setup(scope, receive, send)
        else:
            await self.upstream(scope, receive, send)
