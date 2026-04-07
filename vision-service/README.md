# Vision Service

Analyzes user-provided product images and identifies base features (model name, RAM, colors) using large Vision models via `OpenRouter` APIs.

**Responsibilities:**
1. Serves the `POST /identify` local pipeline payload.
2. Extracts visual features and normalizes them into JSON output payload.
3. Passes the baseline product name downward to the Orchestrator for initiating multi-channel scraped searches.
