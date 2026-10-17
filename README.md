# LightRAG Railway setup extension

A small setup layer on the official LightRAG v1.5.7 container, pinned by digest in `Dockerfile`. A separate native Railway PostgreSQL service supplies the database and pgvector extension. No model provider credentials are bundled.

## First launch

You can supply `LLM_BINDING_API_KEY` and `EMBEDDING_BINDING_API_KEY` in Railway, or leave them empty and open the application's HTTPS domain. The setup screen is at `/setup` and uses the existing `ADMIN_USERNAME` / `ADMIN_PASSWORD` administrator account. Railway's `AUTH_ACCOUNTS` must reference those values. The template generates the administrator password, `LIGHTRAG_API_KEY` and `TOKEN_SECRET` independently.

The setup screen supports OpenAI and OpenAI-compatible chat/embedding connections. Defaults are `gpt-4o-mini`, `text-embedding-3-small`, dimension `1536` and API base `https://api.openai.com/v1`. Use native Railway variables for other LightRAG bindings.

To use Claude for chat, configure an OpenAI-compatible gateway such as LiteLLM with an Anthropic model and provide its URL, alias and proxy key. Choose a compatible embedding provider separately; the Claude chat model is not an embedding model. This extension does not provision the gateway or include inference credits.

## Precedence and storage

- A nonempty Railway chat key selects the Railway chat key, model and host as a group.
- A nonempty Railway embedding key selects the Railway embedding key, model, host and dimension as a group.
- If a group's Railway key is empty, the product's saved configuration supplies that group. You can reuse the chat key for embeddings or enter a separate key.
- Saved provider settings are encrypted with Fernet using a domain-separated key derived from `TOKEN_SECRET`, and written atomically to `/app/data/.railway-setup/providers.enc` (file `0600`, directory `0700`).
- Keep the `/app/data` volume and the original `TOKEN_SECRET` across restarts, redeploys and restores. Back up both the volume and the secret. If the secret changes, the original value is required to decrypt the saved settings.
- Keys are never returned by the setup API, embedded into HTML, stored in browser local storage or written into this repository.
- Saving gracefully restarts the LightRAG worker. Database/application lifespan shutdown is preserved. After configuration, `/webui/` serves the native application; `/setup` remains available to the administrator.
- After activation, the setup screen protects the embedding model, dimension and endpoint from accidental changes. Use Railway variables only after planning the required index migration/re-indexing.

## Authentication and readiness

The setup layer reuses LightRAG's password verification and signed tokens. Its admin session uses a secure, HTTP-only, SameSite Strict cookie scoped to `/setup`; ordinary LightRAG tokens and non-administrator accounts cannot manage provider settings. Mutations require same-origin JSON requests. Failed setup logins are throttled.

Without provider credentials, the onboarding server returns HTTP 200 at `/health` with `status: setup_required`. This means the configuration interface is running, not that document processing is ready. Once configured, health, document and query routes come from the upstream LightRAG application. Provider credentials and model permissions must still be checked with a real provider request.

## Local checks

With FastAPI, HTTPX and cryptography available:

```sh
python -m unittest -v test_setup.py
```

The checks cover unauthenticated/guest rejection, login throttling, origin protection, encrypted persistence, Railway precedence, separate/reused keys and embedding configuration protection. End-to-end validation also uses an isolated OpenAI-compatible test provider for ingestion, PostgreSQL graph/vector retrieval, streaming and restart checks. A synthetic provider test does not validate any external OpenAI or Anthropic account.
