// Demo behaviour for the 25Live export page when the app runs without the server (no VITE_API_URL).
// Same shapes as the server's responses (server/routers/admin.py), kept in memory: a reload resets it.
// The CSV columns mirror server/services/live25_csv.py.
const HEADERS = [
  "EVENT_ID", "EVENT_NAME", "EVENT_TITLE", "DESCRIPTION", "INTERNAL_NOTES", "EVENT_TYPE",
  "PRIMARY_ORGANIZATION", "REG_HEADCOUNT", "EXP_HEADCOUNT", "CATEGORIES", "INSTRUCTOR",
  "SCHEDULER", "REQUESTOR", "PROFILE_ID", "PROFILE_NAME", "FIRST_OCCURRENCE_DATE",
  "PRE_EVENT_TIME", "START_TIME", "END_TIME", "POST_EVENT_TIME", "REPEATING_RULE",
  "ADHOC_DATES", "LOCATIONS", "BOUND_PROFILE_ID", "RELATED_EVENT_ID", "CUSTOM_ATTRIBUTES",
];
const DOW = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const p2 = n => String(n).padStart(2, "0");
const clock = d => `${p2(d.getHours())}:${p2(d.getMinutes())}:00`;
const nice = d => `${d.getHours() % 12 || 12}:${p2(d.getMinutes())} ${d.getHours() < 12 ? "AM" : "PM"}`;

function booking(id, name, org, room, roomId, dayOffset, from, hours, extra = {}) {
  const start = new Date(); start.setDate(start.getDate() + dayOffset); start.setHours(from, 0, 0, 0);
  const end = new Date(start.getTime() + hours * 3600000);
  return { id, name, org, room, roomId, start, end, attendance: 30, state: "ready", batch: null, exportedAt: null, cancelHandled: false, ...extra };
}

const hoursAgo = h => new Date(Date.now() - h * 3600000);
let bookings = [
  booking("demo-1", "Robotics Demo Night", "Robotics Club", "Javits 101", "48213", 2, 18, 2),
  booking("demo-2", "Spring Dance Rehearsal", "Dance Collective", "SAC Ballroom A", "50122", 3, 17, 3),
  booking("demo-3", "Hackathon Kickoff", "Tech Club", "Engineering 145", null, 4, 12, 2),
  booking("demo-4", "Overnight Game Jam", "Media Club", "Library W4510", "47730", 5, 9, 30),
  booking("demo-5", "Debate Practice", "Debate Society", "Javits 102", "48214", 1, 19, 2,
    { state: "exported", batch: "demo-batch-1", exportedAt: hoursAgo(5) }),
  booking("demo-6", "Chess Night", "Chess Club", "SAC 302", "50340", 2, 20, 2,
    { state: "cancelled", batch: "demo-batch-1", exportedAt: hoursAgo(5) }),
];
const problemsOf = b => (b.end - b.start >= 24 * 3600000 ? ["Longer than 24 hours: 25Live's CSV import can't take it. Enter this one in 25Live by hand."] : []);
const warningsOf = b => (b.roomId ? [] : [`${b.room} has no 25Live ID, so its name is used: it must match the 25Live location name exactly.`]);
const item = b => ({
  id: b.id, event_name: b.name, organization: b.org, room: b.room,
  date: `${DOW[b.start.getDay()]} ${MON[b.start.getMonth()]} ${b.start.getDate()}`,
  time: `${nice(b.start)}\u2013${nice(b.end)}`, problems: problemsOf(b), warnings: warningsOf(b),
});

const cell = v => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
function csvFor(list) {
  const rows = list.map(b => {
    const v = {
      EVENT_ID: b.id, EVENT_NAME: b.name.slice(0, 40), EVENT_TITLE: b.name,
      INTERNAL_NOTES: `CampusReserve request ${b.id}. Organization: ${b.org}.`,
      EVENT_TYPE: "Meeting", PRIMARY_ORGANIZATION: b.org, EXP_HEADCOUNT: String(b.attendance),
      PROFILE_ID: `P-${b.id}`, START_TIME: clock(b.start), END_TIME: clock(b.end),
      ADHOC_DATES: `${b.start.getFullYear()}-${p2(b.start.getMonth() + 1)}-${p2(b.start.getDate())}`,
      LOCATIONS: b.roomId || b.room,
    };
    return HEADERS.map(h => cell(v[h] || "")).join(",");
  });
  return [HEADERS.join(","), ...rows].join("\r\n") + "\r\n";
}

const wait = v => new Promise(r => setTimeout(() => r(v), 150));
const fail = msg => wait().then(() => { throw new Error(msg); });
const inBatch = id => bookings.filter(b => b.batch === id);
const fileName = () => { const d = new Date(); return `25live-import-${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}.csv`; };
let batchCount = 1;

export function exportPreview() {
  const queue = bookings.filter(b => b.state === "ready");
  const ids = [...new Set(bookings.filter(b => b.batch).map(b => b.batch))];
  const batches = ids.map(id => {
    const list = inBatch(id);
    return { id, exported_at: list[0].exportedAt.toISOString(), count: list.length, confirmed: list.filter(b => b.state === "synced").length };
  }).sort((a, b) => b.exported_at.localeCompare(a.exported_at));
  return wait({
    ready: queue.filter(b => !problemsOf(b).length).map(item),
    skipped: queue.filter(b => problemsOf(b).length).map(item),
    cancellations: bookings.filter(b => b.state === "cancelled" && b.batch && !b.cancelHandled)
      .map(b => ({ ...item(b), event_id: `CSV-${b.id}` })),
    batches,
    settings: { event_type: "Meeting", primary_organization: null },
  });
}

export function exportNew() {
  const take = bookings.filter(b => b.state === "ready" && !problemsOf(b).length);
  if (!take.length) return fail("Nothing new to export: every approved booking is already in a file.");
  const id = `demo-batch-${++batchCount}`;
  take.forEach(b => { b.state = "exported"; b.batch = id; b.exportedAt = new Date(); });
  const skipped = bookings.filter(b => b.state === "ready" && problemsOf(b).length).map(item);
  return wait({ batch_id: id, filename: fileName(), count: take.length, csv: csvFor(take), skipped });
}

export function exportBatch(id) {
  const list = inBatch(id).filter(b => b.state !== "cancelled");
  return list.length ? wait({ batch_id: id, filename: fileName(), count: list.length, csv: csvFor(list), skipped: [] })
    : fail("Every booking in this export has since been cancelled.");
}

export function confirmBatch(id) {
  const todo = inBatch(id).filter(b => b.state === "exported");
  todo.forEach(b => { b.state = "synced"; });
  return wait({ confirmed: todo.length });
}

export function undoBatch(id) {
  const todo = inBatch(id).filter(b => b.state !== "synced");
  if (!todo.length) return fail("This export is already confirmed as uploaded.");
  todo.forEach(b => { b.batch = null; b.exportedAt = null; if (b.state === "exported") b.state = "ready"; });
  return wait({ released: todo.length });
}

export function cancellationDone(id) {
  const b = bookings.find(x => x.id === id);
  if (b) b.cancelHandled = true;
  return wait({ ok: true });
}
