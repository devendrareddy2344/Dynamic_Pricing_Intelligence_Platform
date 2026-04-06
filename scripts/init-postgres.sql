CREATE TABLE IF NOT EXISTS price_history (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    product_hash TEXT NOT NULL,
    product_name TEXT NOT NULL,
    source TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    product_title TEXT,
    product_url TEXT,
    seller_rating DOUBLE PRECISION,
    review_count INTEGER,
    in_stock BOOLEAN,
    title_match_score DOUBLE PRECISION,
    is_anomaly BOOLEAN DEFAULT FALSE,
    cluster_tier TEXT,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_price_history_product_time
    ON price_history (product_hash, scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_history_session ON price_history (session_id);
CREATE INDEX IF NOT EXISTS idx_price_history_source ON price_history (source, scraped_at DESC);

CREATE TABLE IF NOT EXISTS vision_cache (
    image_md5 TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
