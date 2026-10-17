# ---------- frontend ----------
FROM node:22-bookworm-slim AS web
WORKDIR /web/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY docs/ /web/docs/
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM node:22-bookworm-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    BINANCECLAW_DATA=/data BINANCECLAW_STATIC=/app/frontend/dist \
    BINANCECLAW_HOST=0.0.0.0 BINANCECLAW_PORT=8686
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code --no-audit --no-fund
WORKDIR /app
COPY backend/ ./backend/
RUN python3 -m venv /venv && /venv/bin/pip install ./backend
ENV PATH="/venv/bin:$PATH"
COPY --from=web /web/frontend/dist ./frontend/dist
VOLUME ["/data"]
EXPOSE 8686
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8686/api/health',timeout=4).status==200 else 1)"
ENTRYPOINT ["binanceclaw"]
CMD ["serve"]
