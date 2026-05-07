import { faker } from "@faker-js/faker";

// Simulation anchor — matches seed_data.py and agents/utils.py SIM_NOW
export const SIM_NOW = new Date("2026-05-04T08:00:00");

// Domain constants (mirrors seed_data.py)
export const SUPPLIERS  = ["Bosch GmbH","Denso Corp","Continental AG","Magna Intl","Aptiv PLC"];
export const PLANTS     = ["Plant-DE","Plant-US","Plant-MX","Plant-CN","Plant-IN"];
export const CUSTOMERS  = ["AutoCo","DriveWorks","NexaVehicles","GlobalFleet","SpeedBuild"];
export const CARRIERS   = ["DHL","Maersk","FedEx Freight","DB Schenker","Kuehne+Nagel"];
export const MODES      = ["truck","ocean","air","rail"] as const;
export const MATERIALS  = ["ENG-3201","TRNS-0044","ELEC-7712","BRKE-1190","SUSP-4402"];
export const PRIORITIES = ["HIGH","MEDIUM","LOW"] as const;
export const MILESTONE_NAMES = [
  "pickup_confirmed","departed_origin","in_transit_hub1",
  "customs_clearance","arrived_dest_port","out_for_delivery","delivered",
];
const TRANSIT_DAYS: Record<string,number> = {truck:3,ocean:25,air:2,rail:7};

// ── Helpers ──────────────────────────────────────────────────────────────────

faker.seed(42);

function rndDate(base: Date, minDays: number, maxDays: number): Date {
  const d = new Date(base);
  d.setDate(d.getDate() + faker.number.int({ min: minDays, max: maxDays }));
  d.setHours(faker.number.int({ min: 0, max: 23 }));
  return d;
}

function fmt(d: Date): string {
  return d.toISOString().replace("T"," ").slice(0,19);
}

function pick<T>(arr: readonly T[]): T {
  return arr[faker.number.int({ min: 0, max: arr.length - 1 })];
}

// ── Generators ───────────────────────────────────────────────────────────────

export interface PurchaseOrder {
  po_id: string; supplier: string; material: string; qty: number;
  origin: string; dest_plant: string; required_delivery_date: string; priority: string;
}
export function genPurchaseOrders(n = 30): PurchaseOrder[] {
  return Array.from({ length: n }, (_, i) => ({
    po_id:                 `PO-${String(i + 1).padStart(4,"0")}`,
    supplier:              pick(SUPPLIERS),
    material:              pick(MATERIALS),
    qty:                   faker.number.int({ min: 50, max: 2000 }),
    origin:                faker.location.city(),
    dest_plant:            pick(PLANTS),
    required_delivery_date: fmt(rndDate(SIM_NOW, 5, 30)),
    priority:              pick(PRIORITIES),
  }));
}

export interface SalesOrder {
  so_id: string; customer: string; material: string; qty: number;
  delivery_commitment_date: string; priority: string;
}
export function genSalesOrders(pos: PurchaseOrder[]): SalesOrder[] {
  return pos.map((po, i) => ({
    so_id:                     `SO-${String(i + 1).padStart(4,"0")}`,
    customer:                  pick(CUSTOMERS),
    material:                  po.material,
    qty:                       faker.number.int({ min: 10, max: po.qty }),
    delivery_commitment_date:  fmt(rndDate(SIM_NOW, 3, 25)),
    priority:                  po.priority,
  }));
}

export interface Inventory {
  plant: string; material: string; current_stock: number;
  safety_stock: number; daily_consumption: number;
}
export function genInventory(): Inventory[] {
  return PLANTS.flatMap(plant =>
    MATERIALS.map(material => {
      const daily = faker.number.int({ min: 5, max: 50 });
      const safety = daily * faker.number.int({ min: 3, max: 7 });
      return {
        plant, material,
        current_stock:    faker.number.int({ min: Math.floor(safety / 2), max: safety * 4 }),
        safety_stock:     safety,
        daily_consumption: daily,
      };
    })
  );
}

export interface Shipment {
  shipment_id: string; po_id: string; carrier: string; mode: string;
  origin: string; dest: string; planned_departure: string; planned_arrival: string;
  current_location: string; status: string;
}
export function genShipments(pos: PurchaseOrder[]): Shipment[] {
  return pos.map(po => {
    const mode = pick(MODES);
    const transit = TRANSIT_DAYS[mode];
    const dep = rndDate(SIM_NOW, -transit - 5, -transit + 2);
    const arr = new Date(dep.getTime() + transit * 86_400_000 + faker.number.int({min:0,max:12}) * 3_600_000);
    const delayed = Math.random() < 0.30;
    return {
      shipment_id:       `SHP-${po.po_id}`,
      po_id:             po.po_id,
      carrier:           pick(CARRIERS),
      mode,
      origin:            po.origin,
      dest:              po.dest_plant,
      planned_departure: fmt(dep),
      planned_arrival:   fmt(arr),
      current_location:  faker.location.city(),
      status:            delayed ? "delayed" : pick(["in_transit","delivered"] as const),
    };
  });
}

export interface Milestone {
  shipment_id: string; milestone: string; planned_timestamp: string;
  actual_timestamp: string | null; status: string;
}
export function genMilestones(shipments: Shipment[]): Milestone[] {
  const rows: Milestone[] = [];
  for (const shp of shipments) {
    const dep = new Date(shp.planned_departure);
    const arr = new Date(shp.planned_arrival);
    const totalHrs = (arr.getTime() - dep.getTime()) / 3_600_000;
    const isDelayed = shp.status === "delayed";

    MILESTONE_NAMES.forEach((name, idx) => {
      const frac = idx / (MILESTONE_NAMES.length - 1);
      const planned = new Date(dep.getTime() + totalHrs * frac * 3_600_000);
      let actual: Date | null = null;
      let status: string;

      if (isDelayed && idx >= 1 && idx <= 4) {
        actual = new Date(planned.getTime() + faker.number.int({min:5,max:48}) * 3_600_000);
        status = "delayed";
      } else if (shp.status === "delivered" || idx === 0) {
        actual = new Date(planned.getTime() + faker.number.int({min:-1,max:2}) * 3_600_000);
        status = "completed";
      } else {
        actual = null;
        status = "pending";
      }

      rows.push({
        shipment_id:       shp.shipment_id,
        milestone:         name,
        planned_timestamp: fmt(planned),
        actual_timestamp:  actual ? fmt(actual) : null,
        status,
      });
    });
  }
  return rows;
}

export interface GpsPing {
  shipment_id: string; timestamp: string; lat: number;
  lon: number; speed: number; delay_indicator: number;
}
export function genGpsFeed(shipments: Shipment[]): GpsPing[] {
  const rows: GpsPing[] = [];
  for (const shp of shipments) {
    if (shp.status === "delivered") continue;
    const dep = new Date(shp.planned_departure);
    const isStuck = shp.status === "delayed" && Math.random() < 0.6;
    const baseLat = faker.number.float({ min: 20, max: 55, fractionDigits: 4 });
    const baseLon = faker.number.float({ min: -10, max: 140, fractionDigits: 4 });
    const ticks = faker.number.int({ min: 8, max: 15 });
    const stuckStart = ticks - faker.number.int({ min: 3, max: 5 });

    for (let t = 0; t < ticks; t++) {
      const ts = new Date(dep.getTime() + t * 4 * 3_600_000);
      if (ts > SIM_NOW) break;

      const stuck = isStuck && t >= stuckStart;
      rows.push({
        shipment_id:    shp.shipment_id,
        timestamp:      fmt(ts),
        lat:            parseFloat((baseLat + t * 0.05 + (stuck ? 0 : faker.number.float({min:-0.01,max:0.01,fractionDigits:4}))).toFixed(6)),
        lon:            parseFloat((baseLon + t * 0.08 + (stuck ? 0 : faker.number.float({min:-0.01,max:0.01,fractionDigits:4}))).toFixed(6)),
        speed:          stuck ? 0 : parseFloat(faker.number.float({min:10,max:90,fractionDigits:2}).toFixed(2)),
        delay_indicator: (stuck || Math.random() < 0.1) ? 1 : 0,
      });
    }
  }
  return rows;
}

// ── Email generators ──────────────────────────────────────────────────────────

const DELAY_SUBJECTS = [
  (sid: string) => `Urgent: Shipment ${sid} delayed at customs`,
  (sid: string) => `RE: ${sid} — missed pickup window`,
  (sid: string) => `Alert: ${sid} stuck at transshipment hub`,
  (sid: string) => `Update on ${sid}: road closure causing delay`,
];
const DELAY_BODIES = [
  (sid: string, city: string, hrs: number) =>
    `Hi team, please be advised that shipment ${sid} has encountered a customs hold at ${city}. We expect a delay of approximately ${hrs} hours.`,
  (sid: string, city: string, hrs: number) =>
    `Unfortunately ${sid} missed the scheduled pickup from ${city}. The next available slot is ${hrs} hours away.`,
  (sid: string, city: string, hrs: number) =>
    `Shipment ${sid} is currently stuck at the ${city} transshipment hub due to port congestion. Estimated additional delay: ${hrs} hours.`,
];
const NORMAL_SUBJECTS = [
  (sid: string) => `${sid} departed on schedule`,
  (sid: string) => `Delivery confirmation for ${sid}`,
];
const NORMAL_BODIES = [
  (sid: string, city: string) => `Just confirming ${sid} departed ${city} as planned. No issues to report.`,
  (sid: string, city: string) => `Shipment ${sid} has been delivered to ${city}. Please sign off.`,
];

export interface Email {
  email_id: string; shipment_id: string; sender: string;
  subject: string; body: string; timestamp: string;
}
export function genEmails(shipments: Shipment[]): Email[] {
  const emails: Email[] = [];
  shipments.forEach((shp, i) => {
    const isDelay = ["delayed","customs_hold"].includes(shp.status);
    const count = isDelay
      ? faker.number.int({ min: 1, max: 3 })
      : faker.number.int({ min: 0, max: 1 });

    for (let j = 0; j < count; j++) {
      const city = faker.location.city();
      const hrs  = faker.number.int({ min: 6, max: 72 });
      const sid  = shp.shipment_id;
      const eid  = `EMAIL-${String(i + 1).padStart(4,"0")}-${j}`;

      const subjectFn = isDelay ? pick(DELAY_SUBJECTS) : pick(NORMAL_SUBJECTS);
      const bodyFn    = isDelay
        ? (pick(DELAY_BODIES) as (sid:string,city:string,hrs:number)=>string)
        : ((pick(NORMAL_BODIES) as (sid:string,city:string)=>string) as (sid:string,city:string,hrs:number)=>string);

      emails.push({
        email_id:    eid,
        shipment_id: sid,
        sender:      faker.internet.email(),
        subject:     subjectFn(sid),
        body:        bodyFn(sid, city, hrs),
        timestamp:   fmt(rndDate(SIM_NOW, -10, -1)),
      });
    }
  });
  return emails;
}
