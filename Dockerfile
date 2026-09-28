FROM python:3.11-slim-bookworm

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    ffmpeg \
    curl \
    mosquitto-clients \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Create and activate a Python virtual environment
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Upgrade pip in venv
RUN /venv/bin/pip install --upgrade pip --quiet

# Install Python dependencies (lightweight — no PyTorch/transformers)
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy source code and necessary files
COPY src/ ./src/
COPY setup.py .
COPY README.md .
COPY prompt.txt .
COPY system_prompt.txt .
COPY tests/ ./tests/
COPY dashboard/ ./dashboard/

# Create required directories
RUN mkdir -p /app/events_store /app/test_videos

# Copy test data
COPY tests/data/ /app/test_videos/

# Copy injection script and make it executable
COPY inject_test_events.sh /app/
RUN chmod +x /app/inject_test_events.sh

# Install the package in development mode
RUN /venv/bin/pip install -e . --quiet

# Default command: start the dashboard server
CMD ["/venv/bin/python", "-m", "uvicorn", "sunny_osprey.dashboard:app", \
     "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
