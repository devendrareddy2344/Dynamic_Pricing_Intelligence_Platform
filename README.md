# Dynamic Pricing Intelligence Platform

Image-only product identification (Gemini Vision), parallel live scrapers (httpx + Playwright), ML analysis (K-Means + Isolation Forest), and streamed Gemini pricing rationale. Observability via Prometheus and Grafana. Data persists in TimescaleDB; vision results cache in Redis (MD5).

## Quick start (Docker Compose)

1. **Install** [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.

2. **Configure environment**

   - Copy `.env.example` to `.env` (a starter `.env` is included with empty `GEMINI_API_KEY`).
   - Set **`GEMINI_API_KEY`** (required for vision + GenAI). Get a key from [Google AI Studio](https://aistudio.google.com/apikey).
   - Optional: **`EBAY_CLIENT_ID`** / **`EBAY_CLIENT_SECRET`** for the eBay Browse API (better eBay results than HTML-only).
   - Set **`REGION=us`** (default) for US scrapers: Amazon, eBay, Walmart, Best Buy, Target. Use **`REGION=in`** and adjust **`ENABLED_SCRAPERS`** for India-focused runs (e.g. Flipkart, Croma).

3. **Run the stack**

   ```bash
   docker compose up --build
   ```

4. **Open**

   - **UI:** http://localhost:5173  
   - **API docs:** http://localhost:8000/docs  
   - **Prometheus:** http://localhost:9090  
   - **Grafana:** http://localhost:3000 (default admin / `changeme` unless overridden)

## Local development (without Docker for the UI)

- Backend: install `requirements.txt`, set `REDIS_URL`, `DATABASE_URL`, `GEMINI_API_KEY`, then:

  ```bash
  set PYTHONPATH=api_gateway;vision-service;scraping-service;ml-service;genai-service
  uvicorn api_gateway.main:app --reload --port 8000
  ```

- Frontend:

  ```bash
  cd frontend && npm install && npm run dev
  ```

  Vite proxies `/api` to `http://localhost:8000`.

## Architecture (summary)

- **`POST /api/v1/vision`** — multipart image → Gemini JSON product schema + `session_id`.
- **`POST /api/v1/sessions/{id}/scrape`** — starts parallel scrapers with jitter, retries, 25s timeout, circuit breaker (Redis), title match ≥ 70% (RapidFuzz).
- **`GET /api/v1/sessions/{id}/stream`** — SSE: `price_scraped`, `scraper_failed`, `analysis_ready`, `genai_token`, `done`.
- **`GET /api/v1/history/{product_hash}`** — price history from TimescaleDB.
- **`GET /metrics`** — Prometheus metrics.

## Constraints and notes

- **Scraping is best-effort.** Retail sites change often, use CAPTCHAs, or block bots. The stack is built for **resilience**: partial results, clear failure reasons, and observability—not a guarantee that every site succeeds every time.
- **No paid scraping APIs** — only direct httpx / Playwright / BeautifulSoup as specified.
- **Single currency per deployment** — set `CURRENCY` / `REGION` in `.env`; mixing INR and USD in one ML run is not supported by default.

## Database without TimescaleDB

If PostgreSQL has no Timescale extension (common on a local **PostgreSQL 18** install), use **`scripts/init-postgres.sql`** on database `dynamic_pricing` instead of `init-timescale.sql`. The app does not require a hypertable.

## Project layout

- `vision-service/` — Gemini vision + Redis MD5 cache  
- `scraping-service/` — orchestrator, site scrapers, normalisation helpers, circuit breaker  
- `ml-service/` — clustering, anomaly detection, demand proxy  
- `genai-service/` — streamed Gemini recommendation text  
- `api_gateway/` — FastAPI, SSE, metrics, DB writes  
- `frontend/` — React + Vite + Tailwind + Recharts  
- `observability/` — Prometheus config; Grafana datasource provisioning  

## Evaluation / demo tips

- Use a **clear, well-lit product photo** for the vision step.
- For **eBay API** mode, add eBay developer keys to `.env`.
- If a scraper always times out, check Grafana (`/metrics`) and logs for that site; consider `ENABLED_SCRAPERS` to temporarily disable a flaky source.
