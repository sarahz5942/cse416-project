import { useState, useEffect, useRef } from 'react';
import { slotState } from '../data/mock.js';
import { LIVE, roomSchedule, busyFromSchedule, dayKeyOf } from '../api.js';
import Pill from '../components/Pill.jsx';
import WaitlistOffer from '../components/WaitlistOffer.jsx';
import useDialog from '../a11y/useDialog.js';
import { useScrollLock, useIsCompact } from '../useMedia.js';

const MIN = 360, MAX = 1410, STEP = 15; // bookable window: 6:00 AM to 11:30 PM
const DOW = ["S","M","T","W","T","F","S"];
const p2 = n => String(n).padStart(2, "0");
const toHHMM = m => `${p2(Math.floor(m / 60))}:${p2(m % 60)}`;
const fromHHMM = v => { const [h, m] = (v || "").split(":").map(Number); return Number.isNaN(h) || Number.isNaN(m) ? null : h * 60 + m; };
const fmt12 = m => { const h = Math.floor(m / 60); return `${((h + 11) % 12) + 1}:${p2(m % 60)} ${h < 12 ? "AM" : "PM"}`; };
const keyToDate = k => { const [y, m, d] = k.split("-").map(Number); return new Date(y, m - 1, d); };
const labelOf = k => keyToDate(k).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const timeBox = { padding:"9px 10px", fontSize:15.5, fontWeight:600, border:"2px solid var(--ink)", borderRadius:8, background:"var(--paper)", color:"var(--ink)" };
const navBtn = { width:34, height:34, borderRadius:8, border:"2px solid var(--ink)", color:"var(--ink)", background:"var(--card)", fontWeight:700, fontSize:16 };

export default function BookingDrawer({ room, onClose, onSubmit, onJoinWaitlist, waitlistLine = [], orgs = [] }) {
  const todayKey = dayKeyOf(new Date());
  const now = new Date();
  const pf = room?.prefill;   // date + time picked in Find a Space's "Available on" filter
  const pfStart = pf ? fromHHMM(pf.from) : null, pfEnd = pf ? fromHHMM(pf.to) : null;
  const [month, setMonth] = useState(pf ? { y: +pf.date.slice(0, 4), m: +pf.date.slice(5, 7) - 1 } : { y: now.getFullYear(), m: now.getMonth() });
  const [dates, setDates] = useState(pf ? [pf.date] : []);   // selected day keys
  const [start, setStart] = useState(pfStart != null ? clamp(pfStart, MIN, MAX) : 18 * 60);
  const [end, setEnd] = useState(pfEnd != null ? clamp(pfEnd, MIN, MAX) : 19 * 60);
  const [org, setOrg] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [wlMsg, setWlMsg] = useState("");
  const [eventName, setEventName] = useState("");
  const [error, setError] = useState("");
  const [live, setLive] = useState({});                // live busy intervals by day key
  const [schedErr, setSchedErr] = useState("");   // set when the room's schedule can't be loaded
  const loaded = useRef(new Set());
  const panel = useRef(null);
  // Keyboard and screen readers: focus moves into the drawer, Tab stays inside, Escape closes it.
  useDialog(panel, { open: !!room, onClose });
  // Phones and tablets: the page behind doesn't scroll while the drawer is open (desktop unchanged).
  const compact = useIsCompact();
  useScrollLock(!!room && compact);

  useEffect(() => {
    if (!done) return;
    const t = setTimeout(onClose, 1300);
    return () => clearTimeout(t);
  }, [done]);

  // Load the room's schedule for the month on screen (live mode only).
  useEffect(() => {
    if (!LIVE || !room) return;
    const id = `${room.id}|${month.y}-${month.m}`;
    if (loaded.current.has(id)) return;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const first = new Date(month.y, month.m, 1), last = new Date(month.y, month.m + 1, 0);
    if (last < today) return;
    loaded.current.add(id);
    setSchedErr("");
    roomSchedule(room.id, dayKeyOf(first < today ? today : first), dayKeyOf(last))
      .then(items => setLive(prev => {
        const next = { ...prev }, add = busyFromSchedule(items);
        for (const k in add) next[k] = [...(next[k] || []), ...add[k]];
        return next;
      })).catch(e => { loaded.current.delete(id); setSchedErr(e.message || "Couldn't load this room's schedule."); });
  }, [room?.id, month.y, month.m]);

  if (!room) return null;

  // [startMin, endMin, "booked" | "requested"] for a day
  const intervalsFor = key => {
    if (LIVE) return live[key] || [];
    const out = [];
    for (let h = 9; h <= 20; h++) {
      const st = slotState(room, key, `${h}:00`);
      if (st !== "open") out.push([h * 60, (h + 1) * 60, st === "taken" ? "booked" : "requested"]);
    }
    return out;
  };
  const overlaps = key => {
    const hit = intervalsFor(key).filter(([s, e]) => s < end && e > start);
    return { booked: hit.filter(h => h[2] === "booked"), requested: hit.filter(h => h[2] === "requested") };
  };

  const sorted = [...dates].sort();
  const rangeOk = end > start;
  // a conflict = another club's booking OR request overlaps the chosen date + time
  const conflicts = sorted.filter(k => { const o = overlaps(k); return o.booked.length > 0 || o.requested.length > 0; });
  const needOrg = orgs.length > 1 && !org;
  const bookingOrg = orgs.length > 1 ? org : orgs[0];
  const canReview = sorted.length > 0 && rangeOk && conflicts.length === 0 && !needOrg && eventName.trim().length > 0;

  const setStartClamped = m => { const s = clamp(m, MIN, MAX - STEP); setStart(s); if (end <= s) setEnd(Math.min(s + 60, MAX)); };
  const setEndClamped = m => { setEnd(clamp(m, start + STEP, MAX)); };
  const toggleDate = k => setDates(ds => ds.includes(k) ? ds.filter(x => x !== k) : [...ds, k]);

  const shiftMonth = d => setMonth(({ y, m }) => { const x = new Date(y, m + d, 1); return { y: x.getFullYear(), m: x.getMonth() }; });
  const cur = new Date(now.getFullYear(), now.getMonth(), 1);
  const canPrev = new Date(month.y, month.m, 1) > cur;
  const canNext = new Date(month.y, month.m, 1) < new Date(now.getFullYear(), now.getMonth() + 6, 1);
  const lead = new Date(month.y, month.m, 1).getDay();
  const dim = new Date(month.y, month.m + 1, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: dim }, (_, i) => `${month.y}-${p2(month.m + 1)}-${p2(i + 1)}`)];
  const monthLabel = new Date(month.y, month.m, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });

  const submit = async () => {
    setBusy(true); setError("");
    try {
      const picks = sorted.map(k => ({ key: k, label: labelOf(k), start: toHHMM(start), end: toHHMM(end) }));
      const failed = (await onSubmit(room, picks, bookingOrg, eventName.trim())) || [];
      if (failed.length === 0) { setDone(true); return; }
      setDates(failed.map(f => f.pick.key)); setConfirming(false);
      setError(failed.map(f => `${f.pick.label}: ${f.msg}`).join(" "));
    } catch (e) { setError(e.message || "Couldn't send the request."); setConfirming(false); }
    finally { setBusy(false); }
  };

  const joinWl = async () => {
    setBusy(true); setError(""); setWlMsg("");
    try {
      const picks = conflicts.map(k => ({ key: k, label: labelOf(k), start: toHHMM(start), end: toHHMM(end) }));
      const failed = (await onJoinWaitlist(room, picks, bookingOrg, eventName.trim())) || [];
      const ok = picks.length - failed.length;
      if (ok) setWlMsg(`✓ You joined the waitlist for ${ok} time slot${ok === 1 ? "" : "s"}. See your spot under My requests.`);
      setDates(ds => ds.filter(k => !conflicts.includes(k) || failed.some(f => f.pick.key === k)));
      if (failed.length) setError(failed.map(f => `${f.pick.label}: ${f.msg}`).join(" "));
    } catch (e) { setError(e.message || "Couldn't join the waitlist."); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div onClick={onClose} aria-hidden="true" style={{ position:"fixed", inset:0, background:"rgba(27,42,74,.28)", zIndex:40 }} />
      <div ref={panel} className="booking-panel" role="dialog" aria-modal="true" aria-labelledby="booking-title" aria-describedby="booking-sub" tabIndex={-1}
        style={{ position:"fixed", top:0, right:0, bottom:0, width:500, maxWidth:"94vw", background:"var(--card)",
        zIndex:50, boxShadow:"-8px 0 40px rgba(27,42,74,.18)", display:"flex", flexDirection:"column" }}>
        <div className="booking-head" style={{ padding:"20px 22px", borderBottom:"1px solid var(--line)" }}>
          <Pill status={room.status} small />
          <h2 id="booking-title" style={{ fontSize:24, fontWeight:800, margin:"10px 0 3px" }}>{room.name}</h2>
          <div id="booking-sub" style={{ fontSize:15.6, color:"var(--ink-faint)" }}>{room.building} · Holds {room.cap}</div>
          <button className="booking-close" onClick={onClose} aria-label="Close booking" style={{ position:"absolute", top:18, right:20, width:30, height:30,
            borderRadius:8, background:"var(--line-soft)" }}>×</button>
        </div>

        <div className="booking-body" style={{ padding:"18px 22px", overflowY:"auto", flex:1, overscrollBehavior:"contain" }}>
          {error && <div role="alert" style={{ color:"var(--red)", fontWeight:600, fontSize:14.5, marginBottom:12 }}>{error}</div>}

          {confirming && (
            <div>
              <div style={{ fontSize:22, fontWeight:700, marginBottom:6 }}>Confirm your request</div>
              <div style={{ fontSize:15, color:"var(--ink-soft)", marginBottom:14 }}>Please check the details. Nothing is sent until you confirm.</div>
              <div style={{ border:"2px solid var(--ink)", borderRadius:10, padding:"14px 16px", background:"var(--paper)" }}>
                <div style={{ fontWeight:700, fontSize:17 }}>{room.name}</div>
                <div style={{ fontSize:14, color:"var(--ink-faint)", marginBottom:8 }}>{room.building}</div>
                <div style={{ fontSize:15, marginBottom:6 }}>Event: <b>{eventName.trim()}</b></div>
                {bookingOrg && <div style={{ fontSize:15, marginBottom:6 }}>Booking for: <b>{bookingOrg}</b></div>}
                <div style={{ fontSize:15, marginBottom:6 }}>Time: <b>{fmt12(start)} – {fmt12(end)}</b></div>
                {sorted.map(k => <div key={k} style={{ fontSize:15, padding:"3px 0" }}>{labelOf(k)}</div>)}
              </div>
            </div>
          )}

          <div style={{ display: confirming ? "none" : "block" }}>
            {schedErr && <div role="alert" style={{ marginBottom:14, padding:"10px 14px", borderRadius:10, background:"var(--red-wash)",
              color:"var(--red)", border:"2px solid var(--red)", fontSize:14, fontWeight:600 }}>
              Couldn't load this room's schedule ({schedErr}), so some times may look free when they aren't. Change months or reopen to retry.</div>}
            <div style={{ marginBottom:18 }}>
              <div style={{ fontSize:15.6, fontWeight:700, marginBottom:8 }}>Event name</div>
              <input value={eventName} onChange={e => setEventName(e.target.value)} maxLength={100} placeholder="e.g. Spring showcase rehearsal"
                style={{ width:"100%", padding:"10px 12px", fontSize:15, border:"2px solid var(--ink)", borderRadius:8, background:"var(--paper)", outline:"none" }} />
            </div>
            {orgs.length > 1 && (
              <div style={{ marginBottom:18 }}>
                <div style={{ fontSize:15.6, fontWeight:700, marginBottom:8 }}>Booking for which club?</div>
                <div style={{ display:"flex", gap:8, flexWrap:"wrap" }}>
                  {orgs.map(o => (
                    <button key={o} onClick={() => setOrg(o)} style={{ padding:"9px 15px", borderRadius:999, fontSize:15, fontWeight:700,
                      border:"2px solid var(--ink)", color:"var(--ink)", background: org === o ? "var(--blue)" : "var(--card)" }}>{o}</button>
                  ))}
                </div>
              </div>
            )}

            <div style={{ fontSize:15.6, fontWeight:700, marginBottom:10 }}>Choose days — tap more than one</div>
            <div style={{ border:"2px solid var(--ink)", borderRadius:12, padding:12, background:"var(--paper)" }}>
              <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:10 }}>
                <button onClick={() => shiftMonth(-1)} disabled={!canPrev} aria-label="Previous month" style={{ ...navBtn, opacity: canPrev ? 1 : .35 }}>‹</button>
                <div style={{ fontWeight:700, fontSize:16.5 }}>{monthLabel}</div>
                <button onClick={() => shiftMonth(1)} disabled={!canNext} aria-label="Next month" style={{ ...navBtn, opacity: canNext ? 1 : .35 }}>›</button>
              </div>
              <div style={{ display:"grid", gridTemplateColumns:"repeat(7, 1fr)", gap:4, textAlign:"center" }}>
                {DOW.map((d, i) => <div key={i} style={{ fontSize:12.5, fontWeight:700, color:"var(--ink-faint)", padding:"2px 0" }}>{d}</div>)}
                {cells.map((k, i) => {
                  if (!k) return <div key={`e${i}`} />;
                  const past = k < todayKey, sel = dates.includes(k);
                  const clash = sel && conflicts.includes(k);
                  return (
                    <button key={k} className="cal-day" disabled={past} onClick={() => toggleDate(k)} aria-label={labelOf(k)} aria-pressed={sel}
                      style={{ height:40, borderRadius:8, fontSize:15, fontWeight:700,
                        border: `2px solid ${clash ? "var(--red)" : sel ? "var(--ink)" : k === todayKey ? "var(--ink-faint)" : "transparent"}`,
                        background: clash ? "var(--red-wash)" : sel ? "var(--blue)" : "var(--card)",
                        color: past ? "var(--ink-faint)" : clash ? "var(--red)" : "var(--ink)", opacity: past ? .45 : 1,
                        cursor: past ? "not-allowed" : "pointer" }}>{Number(k.slice(8))}</button>
                  );
                })}
              </div>
            </div>

            <div style={{ fontSize:15.6, fontWeight:700, margin:"20px 0 10px" }}>Choose a time — same for every day</div>
            <div style={{ border:"2px solid var(--ink)", borderRadius:12, padding:"14px 14px 10px", background:"var(--paper)" }}>
              <div style={{ display:"flex", gap:12, alignItems:"center", flexWrap:"wrap", marginBottom:10 }}>
                <label style={{ fontSize:14, fontWeight:600, color:"var(--ink-soft)" }}>From{" "}
                  <input id="book-start" type="time" step={900} value={toHHMM(start)} style={timeBox}
                    onChange={e => { const v = fromHHMM(e.target.value); if (v !== null) setStartClamped(v); }} />
                </label>
                <label style={{ fontSize:14, fontWeight:600, color:"var(--ink-soft)" }}>To{" "}
                  <input id="book-end" type="time" step={900} value={toHHMM(end)} style={timeBox}
                    onChange={e => { const v = fromHHMM(e.target.value); if (v !== null) setEndClamped(v); }} />
                </label>
                <div style={{ marginLeft:"auto", fontWeight:700, fontSize:15 }}>{fmt12(start)} – {fmt12(end)}</div>
              </div>
              <label style={{ display:"block", fontSize:13.5, fontWeight:600, color:"var(--ink-soft)" }}>Start
                <input id="slide-start" type="range" min={MIN} max={MAX - STEP} step={STEP} value={clamp(start, MIN, MAX - STEP)}
                  onChange={e => setStartClamped(+e.target.value)} style={{ width:"100%", accentColor:"#1B2A4A" }} />
              </label>
              <label style={{ display:"block", fontSize:13.5, fontWeight:600, color:"var(--ink-soft)", marginTop:4 }}>End
                <input id="slide-end" type="range" min={MIN + STEP} max={MAX} step={STEP} value={clamp(end, MIN + STEP, MAX)}
                  onChange={e => setEndClamped(+e.target.value)} style={{ width:"100%", accentColor:"#1B2A4A" }} />
              </label>
              <div style={{ display:"flex", justifyContent:"space-between", fontSize:12.5, color:"var(--ink-faint)" }}>
                <span>6 AM</span><span>12 PM</span><span>6 PM</span><span>11:30 PM</span>
              </div>
            </div>

            {sorted.length > 0 && (
              <div style={{ background:"var(--blue-wash)", borderRadius:10, padding:"13px 15px", marginTop:18 }}>
                <div style={{ fontSize:15.6, fontWeight:700, marginBottom:8 }}>Your selection ({sorted.length} day{sorted.length === 1 ? "" : "s"})</div>
                {sorted.map(k => {
                  const o = overlaps(k);
                  return (
                    <div key={k} style={{ padding:"5px 0", color:"var(--ink-soft)", fontSize:15.5 }}>
                      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
                        <span><b style={{ color:"var(--ink)" }}>{labelOf(k)}</b> · {fmt12(start)} – {fmt12(end)}</span>
                        <button onClick={() => toggleDate(k)} aria-label={`Remove ${labelOf(k)}`} style={{ fontSize:19, color:"var(--ink-faint)" }}>×</button>
                      </div>
                      {(o.booked.length > 0 || o.requested.length > 0) && <div style={{ color:"var(--red)", fontWeight:600, fontSize:14 }}>
                        Time conflict: {o.booked.length > 0
                          ? `already booked ${o.booked.map(([s, e]) => `${fmt12(s)}–${fmt12(e)}`).join(", ")}`
                          : "another club has already requested this time"}. Join the waitlist below, or pick another time or day.</div>}
                    </div>
                  );
                })}
              </div>
            )}

            {wlMsg && <div role="status" style={{ color:"var(--green)", fontWeight:600, fontSize:15, marginTop:14 }}>{wlMsg}</div>}
            {conflicts.length > 0 && !needOrg && onJoinWaitlist && (
              <WaitlistOffer eventName={eventName} roomName={room.name} days={conflicts} start={start} end={end} line={waitlistLine} busy={busy} onJoin={joinWl} />
            )}
          </div>
        </div>

        <div className="booking-foot" style={{ padding:"16px 22px", borderTop:"1px solid var(--line)", background:"var(--paper)" }}>
          {done ? (
            <div role="status" style={{ color:"var(--green)", fontWeight:600, fontSize:16.8, textAlign:"center" }}>
              ✓ {sorted.length} request{sorted.length === 1 ? "" : "s"} sent — you'll get a notification when reviewed
            </div>
          ) : confirming ? (
            <div style={{ display:"flex", gap:10 }}>
              <button onClick={() => setConfirming(false)} style={{ flex:1, padding:"13px", borderRadius:10, fontSize:16, fontWeight:700,
                border:"2px solid var(--ink)", background:"var(--card)", color:"var(--ink)" }}>Back</button>
              <button disabled={busy} onClick={submit} style={{ flex:2, padding:"13px", borderRadius:10,
                fontSize:16, fontWeight:700, color:"var(--ink)", background:"var(--blue)" }}>{busy ? "Sending…" : "Confirm request"}</button>
            </div>
          ) : (
            <button disabled={!canReview} onClick={() => { setError(""); setConfirming(true); }} style={{
              width:"100%", padding:"13px", borderRadius:10, fontSize:18, fontWeight:700, color:"var(--ink)",
              background: canReview ? "var(--blue)" : "var(--line-soft)" }}>
              {needOrg ? "Choose a club to continue"
                : sorted.length === 0 ? "Pick at least one day"
                : conflicts.length ? "Time conflict: join the waitlist or change the time"
                : !eventName.trim() ? "Add an event name to continue"
                : `Review ${sorted.length} request${sorted.length === 1 ? "" : "s"}`}
            </button>
          )}
        </div>
      </div>
    </>
  );
}
