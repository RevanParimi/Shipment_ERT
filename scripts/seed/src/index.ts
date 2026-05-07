import Database from "better-sqlite3";
import * as fs from "fs";
import * as path from "path";
import {
  genPurchaseOrders, genSalesOrders, genInventory,
  genShipments, genMilestones, genGpsFeed, genEmails,
} from "./generators";

const DB_PATH    = process.env.DB_PATH    ?? "data/supply_chain.db";
const EMAILS_DIR = process.env.EMAILS_DIR ?? "data/emails";
const SCHEMA_SQL = path.resolve(__dirname, "../../../schema.sql");

function insertRows(db: Database.Database, table: string, rows: Record<string,unknown>[]): void {
  if (rows.length === 0) return;
  const cols = Object.keys(rows[0]);
  const placeholders = cols.map(() => "?").join(", ");
  const stmt = db.prepare(`INSERT OR REPLACE INTO ${table} (${cols.join(", ")}) VALUES (${placeholders})`);
  const insertMany = db.transaction((data: Record<string,unknown>[]) => {
    for (const row of data) stmt.run(Object.values(row));
  });
  insertMany(rows);
}

function main(): void {
  // Ensure directories exist
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  fs.mkdirSync(EMAILS_DIR, { recursive: true });

  console.log(`[seed] DB     : ${DB_PATH}`);
  console.log(`[seed] Emails : ${EMAILS_DIR}`);

  // Apply schema
  const db = new Database(DB_PATH);
  if (fs.existsSync(SCHEMA_SQL)) {
    const schema = fs.readFileSync(SCHEMA_SQL, "utf8");
    db.exec(schema);
  } else {
    console.warn("[seed] schema.sql not found — assuming tables already exist");
  }

  // Generate data
  const pos       = genPurchaseOrders(30);
  const sos       = genSalesOrders(pos);
  const inv       = genInventory();
  const shps      = genShipments(pos);
  const milestones = genMilestones(shps);
  const gps       = genGpsFeed(shps);
  const emails    = genEmails(shps);

  // Persist to DB
  insertRows(db, "purchase_orders", pos as unknown as Record<string,unknown>[]);
  insertRows(db, "sales_orders",    sos as unknown as Record<string,unknown>[]);
  insertRows(db, "inventory",       inv as unknown as Record<string,unknown>[]);
  insertRows(db, "shipments",       shps as unknown as Record<string,unknown>[]);
  insertRows(db, "milestones",      milestones as unknown as Record<string,unknown>[]);
  insertRows(db, "gps_feed",        gps as unknown as Record<string,unknown>[]);
  db.close();

  // Persist emails as JSON files
  for (const email of emails) {
    const outPath = path.join(EMAILS_DIR, `${email.email_id}.json`);
    fs.writeFileSync(outPath, JSON.stringify(email, null, 2));
  }

  console.log(`[seed] purchase_orders : ${pos.length}`);
  console.log(`[seed] sales_orders    : ${sos.length}`);
  console.log(`[seed] inventory rows  : ${inv.length}`);
  console.log(`[seed] shipments       : ${shps.length}`);
  console.log(`[seed] milestones      : ${milestones.length}`);
  console.log(`[seed] gps_feed rows   : ${gps.length}`);
  console.log(`[seed] emails (json)   : ${emails.length}`);
  console.log("[seed] Done.");
}

main();
