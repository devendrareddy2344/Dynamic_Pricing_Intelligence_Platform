-- ============================================================
--  Synycs Dynamic Pricing — MySQL Setup Script
--  Run as root:  mysql -u root -p < scripts/mysql_setup.sql
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 1. DATABASE
-- ────────────────────────────────────────────────────────────
CREATE DATABASE IF NOT EXISTS dynamic_pricing
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE dynamic_pricing;

-- ────────────────────────────────────────────────────────────
-- 2. DEDICATED APP USER  (change password as needed)
-- ────────────────────────────────────────────────────────────
CREATE USER IF NOT EXISTS 'pricing_user'@'localhost' IDENTIFIED BY 'Pricing@123';
GRANT ALL PRIVILEGES ON dynamic_pricing.* TO 'pricing_user'@'localhost';
FLUSH PRIVILEGES;

-- ────────────────────────────────────────────────────────────
-- 3. CORE TABLE: price_history
--    Written by the API gateway after every scrape session.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_history (
    id                BIGINT UNSIGNED    NOT NULL AUTO_INCREMENT,
    session_id        VARCHAR(64)        NOT NULL COMMENT 'UUID of the vision+scrape session',
    product_hash      VARCHAR(64)        NOT NULL COMMENT 'MD5 of the uploaded image',
    product_name      VARCHAR(512)       DEFAULT NULL,
    source            VARCHAR(64)        DEFAULT NULL COMMENT 'amazon | ebay | walmart | …',
    price             DECIMAL(12, 2)     DEFAULT NULL,
    currency          VARCHAR(8)         DEFAULT 'USD',
    product_title     VARCHAR(512)       DEFAULT NULL COMMENT 'Title as returned by the scraper',
    product_url       TEXT               DEFAULT NULL,
    seller_rating     DECIMAL(3, 2)      DEFAULT NULL COMMENT '0.00 – 5.00',
    review_count      INT UNSIGNED       DEFAULT NULL,
    in_stock          TINYINT(1)         NOT NULL DEFAULT 1,
    title_match_score DECIMAL(5, 4)      DEFAULT NULL COMMENT 'rapidfuzz score 0–1',
    is_anomaly        TINYINT(1)         NOT NULL DEFAULT 0 COMMENT 'IsolationForest flag',
    cluster_tier      ENUM('budget','mid','premium') DEFAULT NULL,
    scraped_at        DATETIME           NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata          JSON               DEFAULT NULL,

    PRIMARY KEY (id),
    INDEX idx_product_hash_scraped  (product_hash, scraped_at),
    INDEX idx_session               (session_id),
    INDEX idx_source_scraped        (source, scraped_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ────────────────────────────────────────────────────────────
-- 4. ML ANALYSIS RESULTS
--    Stores the structured output from ml_service.analyser
--    so the frontend can re-fetch without re-running ML.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ml_analysis (
    id                BIGINT UNSIGNED    NOT NULL AUTO_INCREMENT,
    session_id        VARCHAR(64)        NOT NULL,
    product_hash      VARCHAR(64)        NOT NULL,
    analysed_at       DATETIME           NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- price statistics
    market_min        DECIMAL(12, 2)     DEFAULT NULL,
    market_max        DECIMAL(12, 2)     DEFAULT NULL,
    market_avg        DECIMAL(12, 2)     DEFAULT NULL,
    recommended_price DECIMAL(12, 2)     DEFAULT NULL,
    price_range_low   DECIMAL(12, 2)     DEFAULT NULL,
    price_range_high  DECIMAL(12, 2)     DEFAULT NULL,

    -- strategy fields
    strategy          VARCHAR(32)        DEFAULT NULL COMMENT 'penetration | competitive | premium',
    competitive_score DECIMAL(5, 2)      DEFAULT NULL,
    demand_score      DECIMAL(5, 2)      DEFAULT NULL,

    -- raw JSON blobs (clusters array, offers_detail, anomaly_mask)
    clusters_json     JSON               DEFAULT NULL,
    offers_detail     JSON               DEFAULT NULL,
    anomaly_mask      JSON               DEFAULT NULL,

    PRIMARY KEY (id),
    INDEX idx_ml_session       (session_id),
    INDEX idx_ml_product_hash  (product_hash, analysed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ────────────────────────────────────────────────────────────
-- 5. VISION IDENTIFICATION CACHE
--    Mirrors the Redis cache in durable storage so it survives
--    Redis restarts.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vision_cache (
    image_md5         VARCHAR(32)        NOT NULL COMMENT 'MD5 hex of raw image bytes',
    identified_at     DATETIME           NOT NULL DEFAULT CURRENT_TIMESTAMP,
    product_name      VARCHAR(512)       DEFAULT NULL,
    brand             VARCHAR(256)       DEFAULT NULL,
    category          VARCHAR(512)       DEFAULT NULL,
    key_specs         JSON               DEFAULT NULL,
    search_queries    JSON               DEFAULT NULL,
    confidence        DECIMAL(4, 3)      DEFAULT NULL COMMENT '0.000 – 1.000',
    notes             TEXT               DEFAULT NULL,
    model_used        VARCHAR(128)       DEFAULT NULL COMMENT 'ollama model name',

    PRIMARY KEY (image_md5),
    INDEX idx_vision_identified (identified_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ────────────────────────────────────────────────────────────
-- 6. SESSIONS
--    Lightweight row per search session for the /observability
--    endpoint.  Written at session creation, updated on completion.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    session_id        VARCHAR(64)        NOT NULL,
    product_hash      VARCHAR(64)        DEFAULT NULL,
    created_at        DATETIME           NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      DATETIME           DEFAULT NULL,
    status            ENUM('pending','scraping','analysing','done','error')
                                         NOT NULL DEFAULT 'pending',
    error_message     TEXT               DEFAULT NULL,

    PRIMARY KEY (session_id),
    INDEX idx_sessions_created (created_at),
    INDEX idx_sessions_status  (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ────────────────────────────────────────────────────────────
-- 7. SCRAPER EVENT LOG
--    Optional audit-trail of every scraper_failed / price_scraped
--    SSE event emitted during a session.
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scraper_events (
    id                BIGINT UNSIGNED    NOT NULL AUTO_INCREMENT,
    session_id        VARCHAR(64)        NOT NULL,
    event_type        VARCHAR(32)        NOT NULL COMMENT 'price_scraped | scraper_failed',
    source            VARCHAR(64)        DEFAULT NULL,
    price             DECIMAL(12, 2)     DEFAULT NULL,
    currency          VARCHAR(8)         DEFAULT NULL,
    reason            VARCHAR(255)       DEFAULT NULL COMMENT 'failure reason if failed',
    retry_count       TINYINT UNSIGNED   DEFAULT 0,
    latency_ms        DECIMAL(10, 2)     DEFAULT NULL,
    occurred_at       DATETIME           NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_payload       JSON               DEFAULT NULL,

    PRIMARY KEY (id),
    INDEX idx_scraper_events_session (session_id, occurred_at),
    INDEX idx_scraper_events_source  (source, occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ────────────────────────────────────────────────────────────
-- Done — verify with:
--   SHOW TABLES;
--   DESCRIBE price_history;
-- ────────────────────────────────────────────────────────────
