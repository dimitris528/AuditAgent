# AuditAgent.ai — container image for Render (Web Service, runtime: docker).
# Render injects PORT at runtime; we bind Streamlit to it (8501 fallback for
# local `docker run -p 8501:8501 auditagent`).

FROM python:3.12-slim

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
CMD streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true
