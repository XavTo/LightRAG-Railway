import os
from config_store import ready
from setup_app import Dispatch, make_setup

from lightrag.api import lightrag_server as upstream
from lightrag.api.auth import auth_handler

original_create = upstream.create_app

def create_app(args):
    setup = make_setup(auth_handler)
    if not ready(os.environ):
        print('Provider setup required. Open /setup and sign in with your administrator account.', flush=True)
        return setup
    return Dispatch(setup, original_create(args))

upstream.create_app = create_app
upstream.main()
