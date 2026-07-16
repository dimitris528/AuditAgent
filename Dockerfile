# Bakalocharto (Μπακαλοχαρτο) — container image for Render (Web Service,
# runtime: docker). Render injects PORT at runtime; we bind Streamlit to it
# (8501 fallback for local `docker run -p 8501:8501 bakalocharto`).

# Pinned to 3.11: the compiled wheels in requirements.txt (pydantic-core,
# pyarrow, ...) are locked against cp311 — a newer interpreter would force
# source builds / mismatched binaries (the exit-139 SIGSEGV crash).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so code edits don't bust the pip layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user; Streamlit needs a writable $HOME for ~/.streamlit.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

# Shell form on purpose: ${PORT:-8501} needs shell expansion.
# The CORS/XSRF/compression flags mirror .streamlit/config.toml so the
# stability settings hold even if the config file is missing from the image.
CMD streamlit run app.py \
    --server.port=${PORT:-8501} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --server.enableWebsocketCompression=false
