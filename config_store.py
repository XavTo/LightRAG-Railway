"""Provider settings stored on the volume, encrypted with the stable token secret."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

GROUPS = {
    'LLM': ['LLM_BINDING_API_KEY', 'LLM_MODEL', 'LLM_BINDING_HOST'],
    'EMBEDDING': ['EMBEDDING_BINDING_API_KEY', 'EMBEDDING_MODEL', 'EMBEDDING_BINDING_HOST', 'EMBEDDING_DIM'],
}
FIELDS = {key for fields in GROUPS.values() for key in fields}

def config_path(env):
    return Path(env.get('RAILWAY_SETUP_DIR', '/app/data/.railway-setup')) / 'providers.enc'

def cipher(env):
    secret = env.get('TOKEN_SECRET', '')
    if len(secret) < 24:
        raise ValueError('A stable TOKEN_SECRET of at least 24 characters is required')
    key = hashlib.sha256(b'lightrag-railway-provider-store-v1\0' + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def load_saved(env):
    path = config_path(env)
    if not path.exists():
        return {}
    try:
        value = json.loads(cipher(env).decrypt(path.read_bytes()))
        return validate(value)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise RuntimeError('Cannot read saved provider settings. Restore the original TOKEN_SECRET and provider settings backup.') from None

def validate(value):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError('Unsupported configuration fields')
    result = {}
    for key, item in value.items():
        if not isinstance(item, str) or len(item) > 4096:
            raise ValueError('Invalid configuration value')
        item = item.strip()
        if not item:
            continue
        if any(ord(c) < 32 for c in item):
            raise ValueError('Configuration values cannot contain control characters')
        if key.endswith('_HOST'):
            url = urlsplit(item)
            if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.fragment or url.query:
                raise ValueError('Use an HTTP(S) API base URL without embedded credentials')
        if key == 'EMBEDDING_DIM' and (not item.isdigit() or not 1 <= int(item) <= 16000):
            raise ValueError('Embedding dimension must be between 1 and 16000')
        result[key] = item
    return result

def save_settings(env, value):
    value = validate(value)
    path = config_path(env)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    sealed = cipher(env).encrypt(json.dumps(value).encode())
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.providers-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(sealed)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def effective_env(env, saved=None):
    saved = load_saved(env) if saved is None else saved
    result = dict(env)
    for prefix, fields in GROUPS.items():
        # An explicitly supplied Railway key selects Railway's complete group.
        if env.get(prefix + '_BINDING_API_KEY', '').strip():
            continue
        for key in fields:
            if saved.get(key): result[key] = saved[key]
    return result

def ready(env):
    return all(env.get(prefix + '_BINDING', 'openai') in ('ollama', 'lollms', 'bedrock') or bool(env.get(prefix + '_BINDING_API_KEY', '').strip()) for prefix in GROUPS)

def fingerprint(env):
    path = config_path(env)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
