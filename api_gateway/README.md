# API Gateway & Orchestrator

The main `entrypoint` layer for the backend logic routing. Mounted natively by `run.py`.

**Responsibilities:**
1. Coordinates internal HTTP traffic and Server-Sent-Events (SSE) out to the React frontend.
2. Executes `db.py` to communicate directly with PostgreSQL / TimescaleDB databases for insertion logic.
3. The `orchestrator.py` module explicitly awaits scraper promises and filters the outputs prior to ML processing, maintaining session states securely.
