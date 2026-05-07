FROM python:3.11-slim

# System deps for chromadb / httpx / grpc (minimal set for slim image)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create directories that may not exist in the image (data is volume-mounted)
RUN mkdir -p data/emails chroma_store

# Make entrypoint executable
RUN chmod +x entrypoint.sh

EXPOSE 8000

# Default values — override in .env or docker-compose environment block
ENV DB_PATH=data/supply_chain.db \
    EMAILS_DIR=data/emails \
    CHROMA_PATH=chroma_store \
    AUTONOMOUS_COST_THRESHOLD=5000 \
    CONFIDENCE_THRESHOLD=0.75 \
    GPS_LOOKBACK_HOURS=4

ENTRYPOINT ["./entrypoint.sh"]
