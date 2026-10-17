FROM ghcr.io/hkuds/lightrag@sha256:5bdbd524931b011df246fe20888d110cef691e6804c12cde636a2b746d7de27e
COPY --chown=lightrag:lightrag config_store.py launcher.py serve.py setup_app.py setup.html /opt/railway/
RUN python -c "from cryptography.fernet import Fernet"
CMD ["python", "/opt/railway/launcher.py"]
