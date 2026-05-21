-- SilkVault synthetic darknet forum schema.
--
-- A second, deliberately *different* forum from DarkBay (onion_service/). It
-- exists to prove the SentinelX scraper is not hardcoded to one site: SilkVault
-- has no JSON API at all — only HTML pages — so the scraper must parse markup.
--
-- Structural differences from DarkBay, on purpose:
--   * "listings" + "messages" instead of "threads" + "posts"
--   * a "board" column with vault-marketplace board names, not phpBB categories
--   * a "vendor" column (the seller handle) distinct from per-message author
--
-- Timestamps are Unix epoch floats, same as DarkBay, so the scraper's cursor
-- logic does not have to special-case date formats.

CREATE TABLE IF NOT EXISTS listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    board       TEXT    NOT NULL,
    vendor      TEXT    NOT NULL,
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER NOT NULL,
    author      TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    created_at  REAL    NOT NULL,
    FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_listing ON messages(listing_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_listings_board   ON listings(board);
