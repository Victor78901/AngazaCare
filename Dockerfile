FROM python:3.12-slim

# Set a working directory
WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first for better caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy app source
COPY . /app

# Expose the Flask port
EXPOSE 5000

# Use gunicorn for production readiness
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app", "--workers", "4"]
