FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py control_plane.py execution_broker.py isolated_worker.py artifact_verifier.py artifact_handoff.py execution_result_adapter.py cloud_run_bootstrap_client.py ./
CMD exec gunicorn --bind :${PORT:-8080} --workers 1 --threads 8 main:app
