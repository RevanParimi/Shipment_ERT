"""
Seed script — generates all mock data and persists to:
  • SQLite  : ../data/supply_chain.db
  • JSON    : ../data/emails/<email_id>.json
Run once before starting the API: python scripts/seed_data.py
"""

import json
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEED = 42
random.seed(SEED)
fake = Faker()
fake.seed_instance(SEED)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "supply_chain.db"
EMAILS_DIR = BASE_DIR / "data" / "emails"
EMAILS_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime(2026, 5, 4, 8, 0, 0)  # simulation anchor

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
SUPPLIERS = ["Bosch GmbH", "Denso Corp", "Continental AG", "Magna Intl", "Aptiv PLC"]
PLANTS = ["Plant-DE", "Plant-US", "Plant-MX", "Plant-CN", "Plant-IN"]
CUSTOMERS = ["AutoCo", "DriveWorks", "NexaVehicles", "GlobalFleet", "SpeedBuild"]
CARRIERS = ["DHL", "Maersk", "FedEx Freight", "DB Schenker", "Kuehne+Nagel"]
MODES = ["truck", "ocean", "air", "rail"]
MATERIALS = ["ENG-3201", "TRNS-0044", "ELEC-7712", "BRKE-1190", "SUSP-4402"]
PRIORITIES = ["HIGH", "MEDIUM", "LOW"]

MILESTONE_NAMES = [
    "pickup_confirmed",
    "departed_origin",
    "in_transit_hub1",
    "customs_clearance",
    "arrived_dest_port",
    "out_for_delivery",
    "delivered",
]

# Delay thresholds per mode (hours) used downstream by risk agent
DELAY_THRESHOLDS = {"truck": 4, "ocean": 24, "air": 6, "rail": 8}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rnd_date(base: datetime, delta_days_min: int, delta_days_max: int) -> datetime:
    return base + timedelta(days=random.randint(delta_days_min, delta_days_max),
                            hours=random.randint(0, 23))


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY,
    supplier TEXT,
    material TEXT,
    qty INTEGER,
    origin TEXT,
    dest_plant TEXT,
    required_delivery_date TEXT,
    priority TEXT
);

CREATE TABLE IF NOT EXISTS sales_orders (
    so_id TEXT PRIMARY KEY,
    customer TEXT,
    material TEXT,
    qty INTEGER,
    delivery_commitment_date TEXT,
    priority TEXT
);

CREATE TABLE IF NOT EXISTS inventory (
    plant TEXT,
    material TEXT,
    current_stock INTEGER,
    safety_stock INTEGER,
    daily_consumption INTEGER,
    PRIMARY KEY (plant, material)
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id TEXT PRIMARY KEY,
    po_id TEXT,
    carrier TEXT,
    mode TEXT,
    origin TEXT,
    dest TEXT,
    planned_departure TEXT,
    planned_arrival TEXT,
    current_location TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT,
    milestone TEXT,
    planned_timestamp TEXT,
    actual_timestamp TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS gps_feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT,
    timestamp TEXT,
    lat REAL,
    lon REAL,
    speed REAL,
    delay_indicator INTEGER
);
"""

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def gen_purchase_orders(n: int = 30) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "po_id": f"PO-{i:04d}",
            "supplier": random.choice(SUPPLIERS),
            "material": random.choice(MATERIALS),
            "qty": random.randint(50, 2000),
            "origin": fake.city(),
            "dest_plant": random.choice(PLANTS),
            "required_delivery_date": fmt(rnd_date(NOW, 5, 30)),
            "priority": random.choice(PRIORITIES),
        })
    return rows


def gen_sales_orders(pos: list[dict]) -> list[dict]:
    rows = []
    for i, po in enumerate(pos, 1):
        rows.append({
            "so_id": f"SO-{i:04d}",
            "customer": random.choice(CUSTOMERS),
            "material": po["material"],
            "qty": random.randint(10, po["qty"]),
            "delivery_commitment_date": fmt(rnd_date(NOW, 3, 25)),
            "priority": po["priority"],
        })
    return rows


def gen_inventory() -> list[dict]:
    rows = []
    for plant in PLANTS:
        for mat in MATERIALS:
            daily = random.randint(5, 50)
            safety = daily * random.randint(3, 7)
            rows.append({
                "plant": plant,
                "material": mat,
                "current_stock": random.randint(safety // 2, safety * 4),
                "safety_stock": safety,
                "daily_consumption": daily,
            })
    return rows


def gen_shipments(pos: list[dict]) -> list[dict]:
    rows = []
    for po in pos:
        mode = random.choice(MODES)
        transit_days = {"truck": 3, "ocean": 25, "air": 2, "rail": 7}[mode]
        planned_dep = rnd_date(NOW, -transit_days - 5, -transit_days + 2)
        planned_arr = planned_dep + timedelta(days=transit_days, hours=random.randint(0, 12))

        # ~30% of shipments are delayed/stuck
        is_delayed = random.random() < 0.30
        status_choices = ["in_transit", "delayed", "customs_hold", "delivered"]
        status = "delayed" if is_delayed else random.choice(["in_transit", "delivered"])

        rows.append({
            "shipment_id": f"SHP-{po['po_id']}",
            "po_id": po["po_id"],
            "carrier": random.choice(CARRIERS),
            "mode": mode,
            "origin": po["origin"],
            "dest": po["dest_plant"],
            "planned_departure": fmt(planned_dep),
            "planned_arrival": fmt(planned_arr),
            "current_location": fake.city(),
            "status": status,
        })
    return rows


def gen_milestones(shipments: list[dict]) -> list[dict]:
    rows = []
    for shp in shipments:
        planned_dep = datetime.strptime(shp["planned_departure"], "%Y-%m-%d %H:%M:%S")
        planned_arr = datetime.strptime(shp["planned_arrival"], "%Y-%m-%d %H:%M:%S")
        total_hours = (planned_arr - planned_dep).total_seconds() / 3600
        is_delayed = shp["status"] == "delayed"

        for idx, name in enumerate(MILESTONE_NAMES):
            frac = idx / (len(MILESTONE_NAMES) - 1)
            planned_ts = planned_dep + timedelta(hours=total_hours * frac)

            # For delayed shipments inject slippage on mid milestones
            if is_delayed and 1 <= idx <= 4:
                slip_hrs = random.randint(5, 48)
                actual_ts = planned_ts + timedelta(hours=slip_hrs)
                status = "delayed"
            elif shp["status"] == "delivered" or idx == 0:
                actual_ts = planned_ts + timedelta(hours=random.randint(-1, 2))
                status = "completed"
            else:
                # Future milestone — not yet hit
                actual_ts = None
                status = "pending"

            rows.append({
                "shipment_id": shp["shipment_id"],
                "milestone": name,
                "planned_timestamp": fmt(planned_ts),
                "actual_timestamp": fmt(actual_ts) if actual_ts else None,
                "status": status,
            })
    return rows


def gen_gps_feed(shipments: list[dict]) -> list[dict]:
    rows = []
    for shp in shipments:
        if shp["status"] == "delivered":
            continue
        planned_dep = datetime.strptime(shp["planned_departure"], "%Y-%m-%d %H:%M:%S")
        is_stuck = shp["status"] == "delayed" and random.random() < 0.6

        # Base lat/lon near a plausible region
        base_lat = random.uniform(20.0, 55.0)
        base_lon = random.uniform(-10.0, 140.0)

        ticks = random.randint(8, 15)
        for t in range(ticks):
            ts = planned_dep + timedelta(hours=t * 4)
            if ts > NOW:
                break

            # Last 3-5 ticks stuck if is_stuck
            stuck_start = ticks - random.randint(3, 5)
            if is_stuck and t >= stuck_start:
                speed = 0.0
                delay = True
                lat = base_lat + (t * 0.05)
                lon = base_lon + (t * 0.08)
            else:
                speed = random.uniform(40, 90) if shp["mode"] == "truck" else random.uniform(10, 30)
                delay = random.random() < 0.1
                lat = base_lat + (t * 0.05) + random.uniform(-0.01, 0.01)
                lon = base_lon + (t * 0.08) + random.uniform(-0.01, 0.01)

            rows.append({
                "shipment_id": shp["shipment_id"],
                "timestamp": fmt(ts),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "speed": round(speed, 2),
                "delay_indicator": int(delay),
            })
    return rows


# ---------------------------------------------------------------------------
# Email generator
# ---------------------------------------------------------------------------
DELAY_SUBJECTS = [
    "Urgent: Shipment {sid} delayed at customs",
    "RE: {sid} — missed pickup window",
    "Alert: {sid} stuck at transshipment hub",
    "Update on {sid}: road closure causing delay",
    "IMPORTANT: {sid} customs hold — docs missing",
]

DELAY_BODIES = [
    "Hi team, please be advised that shipment {sid} has encountered a customs hold at {city}. "
    "We expect a delay of approximately {hrs} hours. Our team is working to resolve.",
    "Unfortunately {sid} missed the scheduled pickup from {city}. The next available slot is "
    "{hrs} hours away. Please update your ETA accordingly.",
    "Shipment {sid} is currently stuck at the {city} transshipment hub due to port congestion. "
    "Estimated additional delay: {hrs} hours.",
    "Due to an unexpected road closure near {city}, {sid} is delayed by roughly {hrs} hours.",
    "Customs clearance for {sid} has been put on hold — missing certificate of origin. "
    "Please advise ASAP. Estimated delay: {hrs}+ hours.",
]

NORMAL_SUBJECTS = [
    "{sid} departed on schedule",
    "Delivery confirmation for {sid}",
    "{sid} cleared customs — on track",
]

NORMAL_BODIES = [
    "Just confirming {sid} departed {city} as planned. No issues to report.",
    "Shipment {sid} has been delivered to {city}. Please sign off.",
    "Customs clearance complete for {sid}. Vehicle is en route to final destination.",
]


def gen_emails(shipments: list[dict]) -> list[dict]:
    emails = []
    for i, shp in enumerate(shipments, 1):
        is_delay_email = shp["status"] in ("delayed", "customs_hold")
        count = random.randint(1, 3) if is_delay_email else random.randint(0, 1)

        for j in range(count):
            city = fake.city()
            hrs = random.randint(6, 72)
            sid = shp["shipment_id"]
            eid = f"EMAIL-{i:04d}-{j}"

            if is_delay_email:
                subject = random.choice(DELAY_SUBJECTS).format(sid=sid)
                body = random.choice(DELAY_BODIES).format(sid=sid, city=city, hrs=hrs)
            else:
                subject = random.choice(NORMAL_SUBJECTS).format(sid=sid)
                body = random.choice(NORMAL_BODIES).format(sid=sid, city=city)

            ts = rnd_date(NOW, -10, -1)
            email = {
                "email_id": eid,
                "shipment_id": sid,
                "sender": fake.email(),
                "subject": subject,
                "body": body,
                "timestamp": fmt(ts),
            }
            emails.append(email)

            # Persist individual JSON file
            out_path = EMAILS_DIR / f"{eid}.json"
            out_path.write_text(json.dumps(email, indent=2))

    return emails


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------

def insert_rows(cur: sqlite3.Cursor, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"
    cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"[seed] DB     : {DB_PATH}")
    print(f"[seed] Emails : {EMAILS_DIR}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    pos = gen_purchase_orders(30)
    sos = gen_sales_orders(pos)
    inv = gen_inventory()
    shps = gen_shipments(pos)
    mils = gen_milestones(shps)
    gps = gen_gps_feed(shps)
    emails = gen_emails(shps)

    insert_rows(cur, "purchase_orders", pos)
    insert_rows(cur, "sales_orders", sos)
    insert_rows(cur, "inventory", inv)
    insert_rows(cur, "shipments", shps)
    insert_rows(cur, "milestones", mils)
    insert_rows(cur, "gps_feed", gps)

    conn.commit()
    conn.close()

    print(f"[seed] purchase_orders : {len(pos)}")
    print(f"[seed] sales_orders    : {len(sos)}")
    print(f"[seed] inventory rows  : {len(inv)}")
    print(f"[seed] shipments       : {len(shps)}")
    print(f"[seed] milestones      : {len(mils)}")
    print(f"[seed] gps_feed rows   : {len(gps)}")
    print(f"[seed] emails (json)   : {len(emails)}")
    print("[seed] Done.")


if __name__ == "__main__":
    main()
