"""Data Ingestion Agent — scope-filtered DB reads.

Filtering decisions:

  Shipments   : WHERE status != 'delivered'
                Delivered shipments need no further action.

  Milestones  : WHERE shipment_id IN (<active_ids>)
                Milestone history only matters for open shipments.

  GPS feed    : WHERE shipment_id IN (<active_ids>)
                  AND timestamp  >= <SIM_NOW minus GPS_LOOKBACK_HOURS>
                Recent movement is what matters for freeze detection.
                Default lookback = 4 h (configurable via GPS_LOOKBACK_HOURS env var).
                At a 4-hour ping cadence this returns 1-3 recent pings; Agent 2
                uses the last 3 to detect freeze, so 12 h gives a safer window.
                Tune via GPS_LOOKBACK_HOURS=12 for stricter freeze detection.

  purchase_orders : WHERE po_id IN (<po_ids of active shipments>)
                    Only POs linked to open shipments are actionable.

  sales_orders    : WHERE material IN (<materials of active POs>)
                    Impact analysis only needs SOs for materials currently at risk.

  inventory       : WHERE (plant || '|' || material) IN (<active dest_plant|material pairs>)
                    Filtered to only the (plant, material) combinations that appear in
                    active shipments. SQLite has no native multi-column IN, so we
                    concatenate plant and material with a separator and match against
                    a set of pre-built "plant|material" strings.
"""

import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from agents.utils import SIM_NOW

load_dotenv()

_DB_PATH = Path(os.getenv("DB_PATH", "data/supply_chain.db"))
_EMAILS_DIR = Path(os.getenv("EMAILS_DIR", "data/emails"))
_GPS_LOOKBACK_HOURS = int(os.getenv("GPS_LOOKBACK_HOURS", "4"))


def run_ingestion(state: dict) -> dict:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row

    def fetch(sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    # -----------------------------------------------------------------------
    # Active shipments
    # -----------------------------------------------------------------------
    active_shipments = fetch("SELECT * FROM shipments WHERE status != 'delivered'")

    milestones: list[dict] = []
    gps_feed: list[dict] = []
    purchase_orders: list[dict] = []
    sales_orders: list[dict] = []

    if active_shipments:
        active_ids = tuple(s["shipment_id"] for s in active_shipments)
        id_ph = ",".join("?" * len(active_ids))

        # --- Milestones for active shipments only ---
        milestones = fetch(
            f"SELECT * FROM milestones WHERE shipment_id IN ({id_ph})",
            active_ids,
        )

        # --- GPS: recent pings only (time-based window, not ping-count) ---
        gps_cutoff = (SIM_NOW - timedelta(hours=_GPS_LOOKBACK_HOURS)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        gps_feed = fetch(
            f"SELECT * FROM gps_feed "
            f"WHERE shipment_id IN ({id_ph}) AND timestamp >= ? "
            f"ORDER BY shipment_id, timestamp",
            active_ids + (gps_cutoff,),
        )

        # --- POs linked to active shipments ---
        po_ids = tuple(
            s["po_id"] for s in active_shipments if s.get("po_id")
        )
        if po_ids:
            po_ph = ",".join("?" * len(po_ids))
            purchase_orders = fetch(
                f"SELECT * FROM purchase_orders WHERE po_id IN ({po_ph})",
                po_ids,
            )

        # --- SOs for materials of active POs ---
        materials = tuple({po["material"] for po in purchase_orders if po.get("material")})
        if materials:
            mat_ph = ",".join("?" * len(materials))
            sales_orders = fetch(
                f"SELECT * FROM sales_orders WHERE material IN ({mat_ph})",
                materials,
            )

    # --- Inventory: filtered to (dest_plant, material) pairs of active shipments ---
    # SQLite has no native multi-column IN clause, so we build a "plant|material"
    # composite key per pair and match against it.  This scales to millions of rows.
    po_material_map = {po["po_id"]: po.get("material", "") for po in purchase_orders}
    active_pairs = {
        f"{s['dest']}|{po_material_map.get(s['po_id'], '')}"
        for s in active_shipments
        if s.get("po_id") and po_material_map.get(s["po_id"])
    }
    if active_pairs:
        pair_ph = ",".join("?" * len(active_pairs))
        inventory = fetch(
            f"SELECT * FROM inventory "
            f"WHERE (plant || '|' || material) IN ({pair_ph})",
            tuple(active_pairs),
        )
    else:
        inventory = []

    conn.close()

    raw_erp = {
        "purchase_orders": purchase_orders,
        "sales_orders": sales_orders,
        "inventory": inventory,
    }
    raw_transport = {
        "shipments": active_shipments,
        "milestones": milestones,
        "gps_feed": gps_feed,
    }

    # --- Email files ---
    parsed_emails: list[dict] = []
    if _EMAILS_DIR.exists():
        for path in sorted(_EMAILS_DIR.glob("*.json")):
            try:
                parsed_emails.append(json.loads(path.read_text()))
            except json.JSONDecodeError:
                pass

    return {
        "raw_erp": raw_erp,
        "raw_transport": raw_transport,
        "parsed_emails": parsed_emails,
    }
