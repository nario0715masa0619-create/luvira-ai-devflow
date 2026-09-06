FROM python:3.12-slim@sha256:0301f4e34fbcb5b071ab58b68d46be37dc5cb6f6e5068830fd5034aef4a82c61

WORKDIR /workspace
COPY orchestrator/isolated_worker.py /app/isolated_worker.py

# This image intentionally contains no Git client, Cloud SDK, provider SDK, or
# credential material. Runtime input is a broker-signed envelope only.
CMD ["python", "/app/isolated_worker.py"]
