ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /opt/benchmark
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip freeze > /opt/benchmark/dependencies.lock.txt
WORKDIR /workspace
