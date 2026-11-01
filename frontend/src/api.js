// Thin client for the CampusReserve FastAPI server.
// Live mode is ON only when VITE_API_URL is set (see .env.example); otherwise the app runs on mock data.
const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
export const LIVE = !!BASE;

let token = null;
try { token = localStorage.getItem("cr_token"); } catch { /* storage unavailable */ }
export const hasToken = () => !!token;
export function setToken(t) {
  token = t;
  try { t ? localStorage.setItem("cr_token", t) : localStorage.removeItem("cr_token"); } catch { /* ignore */ }
}

function errorMessage(data, status) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (d && typeof d.message === "string") return d.message;
  if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
  return `Request failed (${status})`;
}

// withTotal: true returns { items, total } instead of just the data. `total` comes from the
// server's X-Total-Count header (set by paginated endpoints: ?limit=&offset=).
async function call(path, { method = "GET", json, form, withTotal = false } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) { headers["Content-Type"] = "application/x-www-form-urlencoded"; body = new URLSearchParams(form).toString(); }
  let res;
  try { res = await fetch(BASE + path, { method, headers, body }); }
  catch { throw new Error("Can't reach the server. Is it running on " + BASE + "?"); }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    if (res.status === 401 && token && path !== "/auth/token") setToken(null);
    throw new Error(errorMessage(data, res.status));
  }
  if (withTotal) {
    const header = res.headers.get("X-Total-Count");
    return { items: data || [], total: header != null ? Number(header) : (data || []).length };
  }
  return data;
}

// Query string for a paginated request, e.g. qs({ search: "dance", limit: 12, offset: 0 }).
// Empty values are left out.
const qs = params => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, v);
  const str = p.toString();
  return str ? `?${str}` : "";
};

// ---- auth -------------------------------------------------------------
export async function login(email, password) {
  const t = await call("/auth/token", { method: "POST", form: { username: email.trim(), password } });
  setToken(t.access_token);
  return call("/auth/me");
}
export const me = () => call("/auth/me");
export const register = (email, name, password) =>
  call("/auth/register", { method: "POST", json: { email: email.trim(), name: name.trim(), password } });
export const searchOrgs = q => call("/organizations?search=" + encodeURIComponent(q));
export const joinOrg = (id, position_title) =>
  call(`/organizations/${id}/join`, { method: "POST", json: { position_title: position_title || null } });
export const memberships = () => call("/me/memberships");
export const orgs = () => call("/organizations");
// One page of clubs: resolves to { items, total }. e.g. orgsPage({ search: "dance", limit: 12, offset: 0 })
export const orgsPage = ({ search, limit, offset = 0 } = {}) =>
  call("/organizations" + qs({ search, limit, offset }), { withTotal: true });
export const orgDetail = id => call(`/organizations/${id}`);
export const adaptClub = o => ({ id:o.id, name:o.name, category:null, bio:o.description || "", count:o.verified_member_count || 0, eboard:null, live:true });

// ---- rooms ------------------------------------------------------------
export const rooms = () => call("/rooms");
export const roomSchedule = (id, startKey, endKey) =>
  call(`/rooms/${encodeURIComponent(id)}/schedule?start=${startKey}&end=${endKey}`);

// Rooms that are free for a whole window. Rooms missing from the list are taken;
// rooms with overlapping_pending_requests > 0 have another club's request on them.
export const availableRooms = ({ date, from, to }) => {
  const [y, m, d] = date.split("-").map(Number);
  const t12 = v => { const [h, mi] = v.split(":").map(Number); return `${((h + 11) % 12) + 1}:${p2(mi)} ${h < 12 ? "AM" : "PM"}`; };
  return call(`/rooms/available?year=${y}&month=${m}&day=${d}&start_time=${encodeURIComponent(t12(from))}&end_time=${encodeURIComponent(t12(to))}`);
};

// ---- room comments ----------------------------------------------------
export const roomComments = id => call(`/rooms/${encodeURIComponent(id)}/comments`);
export const postRoomComment = (id, body, organization_id) =>
  call(`/rooms/${encodeURIComponent(id)}/comments`, { method: "POST", json: { body, organization_id: organization_id || null } });
export const deleteRoomComment = (id, commentId) =>
  call(`/rooms/${encodeURIComponent(id)}/comments/${commentId}`, { method: "DELETE" });

// ---- requests ---------------------------------------------------------
export const myRequests = () => call("/requests/mine");
export const adminRequests = status => call(`/admin/requests?status=${status}&limit=200`).then(items => items.map(i => i.request));
export const createRequest = body => call("/requests", { method: "POST", json: body });
export const approve = id => call(`/requests/${id}/approve`, { method: "POST", json: {} });
export const cancelRequest = id => call(`/requests/${id}/cancel`, { method: "POST", json: {} });
export const deny = (id, reason) => call(`/requests/${id}/deny`, { method: "POST", json: { reason } });
export const analytics = days => call(`/admin/analytics?days=${days}`);

// ---- waitlist ---------------------------------------------------------
export const roomWaitlist = id => call(`/waitlist/rooms/${encodeURIComponent(id)}`);
export const leaveWaitlist = id => call(`/requests/${id}/waitlist`, { method: "DELETE" });

// ---- adapters: server shapes -> the shapes the UI already uses ---------
export const SLOTS = ["9:00","10:00","11:00","12:00","13:00","14:00","15:00","16:00","17:00","18:00","19:00","20:00"];
export const ROLE_FROM_SERVER = { club_leader: "leader", admin: "admin", venue_host: "venue" };

export function adaptRoom(r) {
  return {
    id: r.id, name: r.name, building: r.building || "Other locations", cap: r.capacity || 0,
    type: r.room_type || "Space",
    features: [...new Set([...(r.live25_features || []), ...(r.features || [])])],
    status: r.club_bookable === false ? "unavailable" : "available",
    closed: r.club_bookable === false,
    note: r.pending_requests ? `${r.pending_requests} pending request${r.pending_requests === 1 ? "" : "s"}` : "Open for requests",
    slots: SLOTS, live: true,
    instructions: [r.instructions, r.comments].filter(Boolean).join("\n\n"),
  };
}

const DOW = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const p2 = n => String(n).padStart(2, "0");
export const dayKeyOf = d => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;

export function adaptRequest(r, roomsById = {}) {
  const s = new Date(r.start_time);
  const room = roomsById[r.room_id] || { id: r.room_id, name: r.room_name || "Room", building: "" };
  const status = r.status;
  return { id: r.id, room, org: r.organization_name || "", date: `${DOW[s.getDay()]}, ${MON[s.getMonth()]} ${s.getDate()}`,
    time: `${p2(s.getHours())}:${p2(s.getMinutes())}–${p2(new Date(r.end_time).getHours())}:${p2(new Date(r.end_time).getMinutes())}`, status, reason: r.decision_reason,
    event: r.event_name, position: r.waitlist_position };
}

// Busy intervals per day from schedule items: { "2026-10-21": [[startMin, endMin, "booked"|"requested"], ...] }
export function busyFromSchedule(items) {
  const out = {};
  for (const it of items) {
    if (it.kind === "waitlist") continue;   // waitlist entries don't block a slot
    const start = new Date(it.start), end = new Date(it.end);
    const state = it.source === "25live" || it.status === "approved" || it.kind === "closed" ? "booked" : "requested";
    for (let d = new Date(start.getFullYear(), start.getMonth(), start.getDate()); d < end; d.setDate(d.getDate() + 1)) {
      const dayStart = d.getTime(), dayEnd = dayStart + 86400000;
      const s0 = Math.max(start.getTime(), dayStart), e0 = Math.min(end.getTime(), dayEnd);
      if (e0 <= s0) continue;
      (out[dayKeyOf(d)] ||= []).push([Math.round((s0 - dayStart) / 60000), Math.round((e0 - dayStart) / 60000), state]);
    }
  }
  return out;
}

const hhmm = m => `${p2(Math.floor(m / 60))}:${p2(m % 60)}`;
// One booking request for a picked day and time range ("HH:MM" start/end).
export function requestBody(room, orgId, pick, eventName) {
  return { organization_id: orgId, room_id: String(room.id), event_name: eventName || "Club event",
    start_time: `${pick.key}T${pick.start}`, end_time: `${pick.key}T${pick.end}` };
}
export { hhmm };

// ---- verification ------------------------------------------------------
// Admin: every unverified membership request, one row each.
export async function pendingVerifications() {
  const users = await call("/admin/users?awaiting_verification=true");
  return users.flatMap(u => u.memberships.filter(m => !m.verified).map(m => ({
    id: `${m.organization_id}|${u.id}`, userId: u.id, userName: u.name, email: u.email,
    orgId: m.organization_id, orgName: m.organization_name, position: m.position_title || "" })));
}
export const verifyMember = (orgId, userId, position) =>
  call(`/admin/organizations/${orgId}/members/${userId}/verify`, { method: "POST", json: { position_title: position || null } });
export const rejectMember = (orgId, userId) =>
  call(`/admin/organizations/${orgId}/members/${userId}`, { method: "DELETE" });

// ---- messages + forum -----------------------------------------------------
// Expected server contract (built from models/communication.py). Change only this section if the real routes differ.
//   GET  /users/search?q=            -> [{ id, name, email }]
//   GET  /conversations              -> [{ id, title, participants:[{id,name}], messages:[{id,sender_id,sender_name,body,created_at}] }]
//   POST /conversations              {participant_ids:[...], title?} -> a conversation (existing 1:1 if there is one)
//   POST /conversations/{id}/messages {body} -> a message
//   GET  /forum                      -> [{ id, title, body, category, author_id, author_name, organization_id, organization_name,
//                                          created_at, replies:[{id, body, author_id, author_name, organization_name, created_at}] }]
//   POST /forum                      {title, body, category, organization_id?} -> a post
//   POST /forum/{id}/replies         {body, organization_id?} -> a reply
export const searchUsers = q => call("/users/search?q=" + encodeURIComponent(q));
export const conversations = () => call("/conversations");
export const createConversation = (participantIds, title) =>
  call("/conversations", { method: "POST", json: { participant_ids: participantIds, title: title || null } });
export const sendMessage = (id, body) => call(`/conversations/${id}/messages`, { method: "POST", json: { body } });
export const forumPosts = () => call("/forum");
export const createPost = body => call("/forum", { method: "POST", json: body });
export const createReply = (id, body) => call(`/forum/${id}/replies`, { method: "POST", json: body });

function fmtTime(iso) {
  const d = new Date(iso), now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
export const adaptConversation = c => ({
  id: c.id, subject: c.title || "",
  participants: (c.participants || []).map(p => ({ id: p.id, name: p.name })),
  messages: (c.messages || []).map(m => ({ id: m.id, fromId: m.sender_id, from: m.sender_name || "Former user", text: m.body, time: fmtTime(m.created_at) })),
});
export const adaptPost = p => ({
  id: p.id, author: p.author_name || "Former user", org: p.organization_name || "", category: p.category || "general", title: p.title || "", body: p.body,
  replies: (p.replies || []).map(r => ({ author: r.author_name || "Former user", org: r.organization_name || "", text: r.body })),
});
