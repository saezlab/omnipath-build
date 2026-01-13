FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy dependency files
COPY omnipath-present/ontology-service/pyproject.toml .
COPY omnipath-present/ontology-service/README.md .

# Adjust ontograph path for container build context
RUN sed -i 's#../../ontograph#/ontograph#' pyproject.toml

# Copy ontograph package (needed as path dependency)
COPY ontograph /ontograph

# Copy application code
COPY omnipath-present/ontology-service/ontology_service/ ontology_service/

# Install dependencies
RUN uv pip install --system -e . -e /ontograph

# Create data and cache directories
RUN mkdir -p /app/data /app/cache

ENV ONTOLOGY_DATA_DIR=/app/data
ENV ONTOLOGY_CACHE_DIR=/app/cache

EXPOSE 8081

CMD ["uvicorn", "ontology_service.main:app", "--host", "0.0.0.0", "--port", "8081"]
