import { useEffect, useState } from 'react';
import { roomNotes, upcomingEvents } from '../data/mock.js';
import * as api from '../api.js';
import Pill from '../components/Pill.jsx';
import Feature from '../components/Feature.jsx';

const SCHEDULE_DAYS = 30;  // the API returns at most 30 days at a time
const EVENTS_PER_PAGE = 5;

const fmtDate = d => d.toLocaleDateString(undefined, { weekday:"short", month:"short", day:"numeric" });
const fmtTime = d => d.toLocaleTimeString(undefined, { hour:"numeric", minute:"2-digit" });

// One schedule item from GET /rooms/{id}/schedule -> the shape this list shows.
function adaptScheduleItem(it) {
  const start = new Date(it.event_start || it.start), end = new Date(it.event_end || it.end);
  const label =
    it.kind === "closed" ? "Closed"
    : it.kind === "waitlist" ? `Waitlist${it.waitlist_position ? ` #${it.waitlist_position}` : ""}`
    : it.source === "25live" ? "Booked"
    : it.status === "approved" ? "Approved"
    : it.status === "pending" ? "Pending request"
    : it.status || "";
  return { title: it.name, label, date: fmtDate(start), time: `${fmtTime(start)}–${fmtTime(end)}`, start };
}

export default function RoomDetail({ room, onBack, onBook, role, orgs = [], orgIds = {} }) {
  const notes = room.live ? { instructions: room.instructions || "", comments: [] } : roomNotes(room);
  const [comments, setComments] = useState(notes.comments);
  const [draft, setDraft] = useState("");

  // Live rooms: this room's real schedule from the API. Mock rooms: the mock events.
  const live = api.LIVE && room.live;

  // Comments: real ones from the API on live rooms, local sample comments in mock mode.
  const isAdmin = role === "admin";
  const canComment = !live || isAdmin || (role === "leader" && orgs.length > 0);
  const [asOrg, setAsOrg] = useState(orgs[0] || "");
  const [cLoading, setCLoading] = useState(live);
  const [cError, setCError] = useState("");
  const [posting, setPosting] = useState(false);
  const adaptComment = c => ({ id:c.id, org:c.posted_by, text:c.body, canDelete:c.can_delete,
    when:new Date(c.created_at).toLocaleDateString(undefined, { month:"short", day:"numeric" }) });
  useEffect(() => {
    if (!live) return;
    let off = false;
    setCLoading(true); setCError("");
    api.roomComments(room.id).then(l => { if (!off) setComments(l.map(adaptComment)); })
      .catch(e => { if (!off) setCError(e.message); })
      .finally(() => { if (!off) setCLoading(false); });
    return () => { off = true; };
  }, [room.id]); // eslint-disable-line
  const postComment = async () => {
    const text = draft.trim();
    if (!text || posting) return;
    if (!live) { setComments(c => [...c, { org:"Robotics Club", text }]); setDraft(""); return; }
    setPosting(true); setCError("");
    try {
      const orgId = isAdmin ? null : orgIds[asOrg];
      if (!isAdmin && !orgId) throw new Error("Pick a club to comment as.");
      const c = await api.postRoomComment(room.id, text, orgId);
      setComments(cs => [adaptComment(c), ...cs]); setDraft("");
    } catch (e) { setCError(e.message); }
    setPosting(false);
  };
  const removeComment = async c => {
    try { await api.deleteRoomComment(room.id, c.id); setComments(cs => cs.filter(x => x.id !== c.id)); }
    catch (e) { setCError(e.message); }
  };
  const [events, setEvents] = useState(live ? [] : upcomingEvents(room));
  const [eventsLoading, setEventsLoading] = useState(live);
  const [eventsError, setEventsError] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [events]);  // back to the first page when the list changes
  const pageCount = Math.max(1, Math.ceil(events.length / EVENTS_PER_PAGE));
  const current = Math.min(page, pageCount);
  const shownEvents = events.slice((current - 1) * EVENTS_PER_PAGE, current * EVENTS_PER_PAGE);
  const pageBtn = disabled => ({ padding:"6px 12px", borderRadius:8, fontSize:15, fontWeight:600, border:"2px solid var(--ink)",
    color:"var(--ink)", background: disabled ? "var(--paper)" : "var(--blue)", opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "default" : "pointer" });

  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    const today = new Date();
    const last = new Date(today); last.setDate(today.getDate() + SCHEDULE_DAYS);
    setEventsLoading(true); setEventsError("");
    api.roomSchedule(room.id, api.dayKeyOf(today), api.dayKeyOf(last))
      .then(items => {
        if (cancelled) return;
        const now = new Date();
        setEvents(items.map(adaptScheduleItem).filter(e => e.start >= now || e.label === "Closed")
          .sort((a, b) => a.start - b.start));
      })
      .catch(e => { if (!cancelled) setEventsError(e.message); })
      .finally(() => { if (!cancelled) setEventsLoading(false); });
    return () => { cancelled = true; };
  }, [live, room.id]);

  return (
    <main className="page rd-page" style={{ maxWidth:1180, margin:"0 auto", padding:"28px 30px 70px" }}>
      <button onClick={onBack} style={{ fontSize:18, fontWeight:600, color:"var(--link)", marginBottom:14 }}>‹ Back to search</button>

      <div className="rd-head" style={{ display:"flex", justifyContent:"space-between", flexWrap:"wrap", gap:12, marginBottom:18 }}>
        <div>
          <Pill status={room.status} small />
          <h1 style={{ fontSize:34, fontWeight:800, margin:"10px 0 4px" }}>{room.name}</h1>
          <div className="rd-sub" style={{ fontSize:19, color:"var(--ink-faint)" }}>{room.building} · Holds {room.cap} · {room.type}</div>
        </div>
        {!room.closed && (
          <button className="rd-book" onClick={()=>onBook(room)} style={{ padding:"12px 22px", borderRadius:10, fontSize:19, fontWeight:700,
            color:"var(--ink)", background:"var(--blue)", height:"fit-content" }}>Book this room</button>
        )}
      </div>

      <div className="rd-grid" style={{ display:"grid", gridTemplateColumns:"1.3fr 1fr", gap:32, alignItems:"start" }}>
        <div>
          <div style={{ fontSize:19, fontWeight:700, marginBottom:10 }}>3D model preview</div>
          <div className="rd-preview" style={{ height:280, borderRadius:18, marginBottom:28, position:"relative",
            background:"linear-gradient(135deg, #FFC93C 0%, #FF9B7A 100%)", display:"grid", placeItems:"center" }}>
            <div style={{ textAlign:"center", color:"var(--ink)" }}>
              <div style={{ fontSize:42, marginBottom:6 }}>⟳</div>
              <div style={{ fontSize:18, fontWeight:600 }}>3D model coming soon</div>
              <div style={{ fontSize:16, opacity:.8 }}>Venue hosts will be able to upload a scanned model here</div>
            </div>
          </div>

          <div style={{ fontSize:19, fontWeight:700, marginBottom:10 }}>What's in this room</div>
          <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginBottom:22 }}>
            {room.features.map(f=><Feature key={f}>{f}</Feature>)}
          </div>

          <div style={{ fontSize:19, fontWeight:700, marginBottom:10 }}>Instructions</div>
          <div style={{ fontSize:18, color: notes.instructions ? "var(--ink-soft)" : "var(--ink-faint)", background:"var(--paper)", padding:"18px 20px",
            borderRadius:14, marginBottom:28, lineHeight:1.65, whiteSpace:"pre-line" }}>{notes.instructions || "No instructions listed for this room."}</div>

          <div style={{ fontSize:19, fontWeight:700, marginBottom:10 }}>Comments from other clubs</div>
          {cLoading && <div style={{ fontSize:16, color:"var(--ink-faint)", marginBottom:12 }}>Loading comments…</div>}
          {cError && <div role="alert" style={{ fontSize:15, color:"var(--red)", fontWeight:600, marginBottom:10 }}>{cError}</div>}
          {!cLoading && comments.length === 0 && (
            <div style={{ fontSize:17, color:"var(--ink-faint)", background:"var(--paper)", padding:"16px 18px", borderRadius:12, marginBottom:12 }}>
              No comments yet. Be the first to leave a tip for other clubs.</div>
          )}
          <div style={{ display:"grid", gap:8, marginBottom:12 }}>
            {comments.map((c,i)=>(
              <div key={c.id || i} style={{ background:"var(--card)", border:"2px solid var(--ink)", borderRadius:10, padding:"13px 16px", fontSize:18 }}>
                <b>{c.org}:</b> {c.text}
                {(c.when || c.canDelete) && (
                  <div style={{ display:"flex", justifyContent:"space-between", marginTop:6, fontSize:13.5, color:"var(--ink-faint)" }}>
                    <span>{c.when}</span>
                    {c.canDelete && <button onClick={()=>removeComment(c)} style={{ fontSize:13.5, fontWeight:600, color:"var(--red)" }}>Delete</button>}
                  </div>
                )}
              </div>
            ))}
          </div>
          {canComment ? (
            <div style={{ display:"flex", gap:8, flexWrap:"wrap" }}>
              {live && !isAdmin && orgs.length > 1 && (
                <select value={asOrg} onChange={e=>setAsOrg(e.target.value)} aria-label="Comment as"
                  style={{ padding:"12px 10px", fontSize:16, border:"2px solid var(--ink)", borderRadius:8, background:"var(--paper)" }}>
                  {orgs.map(o => <option key={o}>{o}</option>)}
                </select>
              )}
              <input value={draft} onChange={e=>setDraft(e.target.value)} maxLength={1000} placeholder="Add a comment or tip for other clubs…"
                onKeyDown={e=>{ if (e.key === "Enter") postComment(); }}
                style={{ flex:1, minWidth:200, padding:"15px 18px", fontSize:18, border:"2px solid var(--ink)", borderRadius:8, background:"var(--paper)", outline:"none" }} />
              <button disabled={!draft.trim() || posting} onClick={postComment}
                style={{ padding:"12px 20px", borderRadius:8, fontSize:18, fontWeight:600, color:"var(--ink)",
                background: draft.trim() ? "var(--blue)" : "var(--line-soft)" }}>{posting ? "Posting…" : "Post"}</button>
            </div>
          ) : (
            <div style={{ fontSize:15, color:"var(--ink-faint)" }}>Comments can be posted by verified club leaders and admins.</div>
          )}
        </div>

        <div>
          <div style={{ fontSize:19, fontWeight:700, marginBottom:10 }}>
            Other events in this room{live && <span style={{ fontWeight:500, fontSize:15, color:"var(--ink-faint)" }}> · next {SCHEDULE_DAYS} days</span>}
          </div>
          <div style={{ background:"var(--card)", border:"2px solid var(--ink)", borderRadius:14, overflow:"hidden" }}>
            {eventsLoading ? (
              <div style={{ padding:16, fontSize:18, color:"var(--ink-faint)" }}>Loading this room's schedule…</div>
            ) : eventsError ? (
              <div role="alert" style={{ padding:16, fontSize:16, color:"var(--ink-soft)" }}>Couldn't load the schedule: {eventsError}</div>
            ) : events.length===0 ? (
              <div style={{ padding:16, fontSize:18, color:"var(--ink-faint)" }}>No upcoming events scheduled.</div>
            ) : shownEvents.map((e,i)=>(
              <div key={i} style={{ padding:"12px 15px", borderBottom: i<shownEvents.length-1 ? "1px solid var(--line-soft)" : "none" }}>
                <div style={{ display:"flex", justifyContent:"space-between", gap:8 }}>
                  <div style={{ fontWeight:700, fontSize:18 }}>{e.org || e.title}</div>
                  {e.label && <span style={{ fontSize:14, fontWeight:600, color:"var(--ink-faint)", whiteSpace:"nowrap" }}>{e.label}</span>}
                </div>
                <div style={{ fontSize:16, color:"var(--ink-faint)" }}>{e.org ? `${e.title} · ` : ""}{e.date} · {e.time}</div>
              </div>
            ))}
          </div>
          {!eventsLoading && !eventsError && pageCount > 1 && (
            <nav aria-label="Event pages" style={{ display:"flex", justifyContent:"space-between", alignItems:"center", gap:10, marginTop:10 }}>
              <button onClick={()=>setPage(current-1)} disabled={current===1} style={pageBtn(current===1)}>‹ Previous</button>
              <span style={{ fontSize:15, fontWeight:600, color:"var(--ink-soft)" }} aria-current="page">
                {(current-1)*EVENTS_PER_PAGE+1}–{Math.min(current*EVENTS_PER_PAGE, events.length)} of {events.length}
              </span>
              <button onClick={()=>setPage(current+1)} disabled={current===pageCount} style={pageBtn(current===pageCount)}>Next ›</button>
            </nav>
          )}
        </div>
      </div>
    </main>
  );
}
