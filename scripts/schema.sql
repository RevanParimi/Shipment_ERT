-- Supply Chain AI — SQLite Schema
-- Extracted from scripts/seed_data.py so schema lives in SQL, not a Python string.
-- Run: sqlite3 data/supply_chain.db < scripts/schema.sql

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id                TEXT PRIMARY KEY,
    supplier             TEXT NOT NULL,
    material             TEXT NOT NULL,
    qty                  INTEGER NOT NULL,
    origin               TEXT NOT NULL,
    dest_plant           TEXT NOT NULL,
    required_delivery_date TEXT NOT NULL,
    priority             TEXT NOT NULL CHECK (priority IN ('HIGH','MEDIUM','LOW'))
);

CREATE TABLE IF NOT EXISTS sales_orders (
    so_id                      TEXT PRIMARY KEY,
    customer                   TEXT NOT NULL,
    material                   TEXT NOT NULL,
    qty                        INTEGER NOT NULL,
    delivery_commitment_date   TEXT NOT NULL,
    priority                   TEXT NOT NULL CHECK (priority IN ('HIGH','MEDIUM','LOW'))
);

CREATE TABLE IF NOT EXISTS inventory (
    plant              TEXT NOT NULL,
    material           TEXT NOT NULL,
    current_stock      INTEGER NOT NULL,
    safety_stock       INTEGER NOT NULL,
    daily_consumption  INTEGER NOT NULL,
    PRIMARY KEY (plant, material)
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id        TEXT PRIMARY KEY,
    po_id              TEXT NOT NULL REFERENCES purchase_orders(po_id),
    carrier            TEXT NOT NULL,
    mode               TEXT NOT NULL CHECK (mode IN ('truck','ocean','air','rail')),
    origin             TEXT NOT NULL,
    dest               TEXT NOT NULL,
    planned_departure  TEXT NOT NULL,
    planned_arrival    TEXT NOT NULL,
    current_location   TEXT,
    status             TEXT NOT NULL CHECK (status IN ('in_transit','delayed','customs_hold','delivered'))
);

CREATE TABLE IF NOT EXISTS milestones (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id        TEXT NOT NULL REFERENCES shipments(shipment_id),
    milestone          TEXT NOT NULL,
    planned_timestamp  TEXT NOT NULL,
    actual_timestamp   TEXT,
    status             TEXT NOT NULL CHECK (status IN ('completed','delayed','pending'))
);

CREATE TABLE IF NOT EXISTS gps_feed (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id      TEXT NOT NULL REFERENCES shipments(shipment_id),
    timestamp        TEXT NOT NULL,
    lat              REAL NOT NULL,
    lon              REAL NOT NULL,
    speed            REAL NOT NULL,
    delay_indicator  INTEGER NOT NULL CHECK (delay_indicator IN (0,1))
);

-- Production indexes: speed up the filtered queries in agents/ingestion.py
CREATE INDEX IF NOT EXISTS idx_shipments_status
    ON shipments(status);

CREATE INDEX IF NOT EXISTS idx_milestones_shipment
    ON milestones(shipment_id);

CREATE INDEX IF NOT EXISTS idx_gps_shipment_time
    ON gps_feed(shipment_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_pos_po_id
    ON purchase_orders(po_id);

CREATE INDEX IF NOT EXISTS idx_sos_material
    ON sales_orders(material);

CREATE INDEX IF NOT EXISTS idx_inventory_plant_material
    ON inventory(plant, material);
