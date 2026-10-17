"""Gracefully reload LightRAG after an authenticated provider configuration change."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from config_store import effective_env, fingerprint

def main():
    # Railway custom commands may bypass Docker's inherited ENTRYPOINT.
    # Preserve upstream volume ownership repair and privilege drop explicitly.
    if os.getuid() == 0:
        entrypoint = '/usr/local/bin/docker-entrypoint.sh'
        os.execv(entrypoint, [entrypoint, sys.executable, str(Path(__file__).resolve())])
    print('Railway setup worker uid=' + str(os.getuid()), flush=True)
    original = dict(os.environ)
    stopping = False
    process = None
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        mark = fingerprint(original)
        env = effective_env(original)
        # Child receives the effective keys; original values retain precedence
        # information for the setup API, without writing any keys to command lines.
        env['RAILWAY_SETUP_ORIGINAL_LLM_KEY_PRESENT'] = str(bool(original.get('LLM_BINDING_API_KEY', '').strip()))
        env['RAILWAY_SETUP_ORIGINAL_EMBEDDING_KEY_PRESENT'] = str(bool(original.get('EMBEDDING_BINDING_API_KEY', '').strip()))
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('serve.py'))], env=env)
        while not stopping and process.poll() is None and fingerprint(original) == mark:
            time.sleep(0.5)
        if process.poll() is not None:
            sys.exit(process.returncode or 1)
        process.terminate()
        try: process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if not stopping:
            print('Provider settings saved; restarting LightRAG.', flush=True)
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=45)

if __name__ == '__main__': main()
