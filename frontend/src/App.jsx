import { useState, useEffect, useRef } from 'react';
import { ROOMS, THREADS, FORUM_POSTS, PROFILES, CLUBS, PEOPLE, getDays } from './data/mock.js';
import Header, { NAV } from './components/Header.jsx';
import { useRoute, enc, dec } from './router.js';
import FindSpace from './pages/FindSpace.jsx';
import RoomDetail from './pages/RoomDetail.jsx';
import BookingDrawer from './pages/BookingDrawer.jsx';
import MyRequests from './pages/MyRequests.jsx';
import Approvals from './pages/Approvals.jsx';
import RoomsAdmin from './pages/RoomsAdmin.jsx';
import VenueRooms from './pages/VenueRooms.jsx';
import Messages from './pages/Messages.jsx';
import Forum from './pages/Forum.jsx';
import Clubs from './pages/Clubs.jsx';
import ClubProfile from './pages/ClubProfile.jsx';
import Profile from './pages/Profile.jsx';
import Login from './pages/Login.jsx';
import CreateAccount from './pages/CreateAccount.jsx';
import Verifications from './pages/Verifications.jsx';
import { setKnownClubs } from './components/OrgLink.jsx';
import * as api from './api.js';
import LoadingScreen from './components/LoadingScreen.jsx';
import Analytics from './pages/Analytics.jsx';

const D = getDays(3);
const HOME = { leader:"find", admin:"approvals", venue:"rooms" };

export default function App() {
  const { path, navigate, back } = useRoute();
  const [signedIn,setSignedIn]=useState(false);
  // live mode: if a saved token exists, ask the server who it belongs to before showing anything
  const [booting,setBooting]=useState(() => api.LIVE && api.hasToken());
  const [bootError,setBootError]=useState("");
  const afterLogin=useRef(null); // deep link the user wanted before being sent to /login
  const [role,setRole]=useState("leader");
  const [profiles,setProfiles]=useState(PROFILES);
  const [openRoom,setOpenRoom]=useState(null);

  const [requests,setRequests]=useState(api.LIVE ? [] : [
    { id:1, room:ROOMS[1], org:"Dance Collective", date:D[1].label, time:"12:00", status:"pending" },
    { id:2, room:ROOMS[0], org:"Robotics Club", date:D[2].label, time:"18:00", status:"approved" },
    { id:3, room:ROOMS[2], org:"Tech Club", date:D[1].label, time:"16:00", status:"approved" },
    { id:4, room:ROOMS[4], org:"Media Club", date:D[2].label, time:"14:00", status:"approved" },
  ]);
  // ---- live mode (FastAPI server) ----
  const [dataLoading,setDataLoading]=useState(false);   // true while a live login loads its first data
  const [rooms,setRooms]=useState(api.LIVE ? [] : ROOMS);
  const [roomsLoading,setRoomsLoading]=useState(false);
  const [apiError,setApiError]=useState("");

  // clubs: mock data by default; the database's organizations in live mode
  const [clubs,setClubs]=useState(() => api.LIVE ? [] : CLUBS.map(c => ({ ...c, count:c.eboard.length })));
  const [clubsLoading,setClubsLoading]=useState(false);
  setKnownClubs(clubs.map(c => c.name));
  const loadClubs = async () => {
    setClubsLoading(true);
    try { setClubs((await api.orgs()).map(api.adaptClub)); } catch (e) { setApiError(e.message); }
    setClubsLoading(false);
  };

  // memberships: this leader's clubs and whether an admin has verified them
  const [memberships,setMemberships]=useState(() => api.LIVE ? [] : CLUBS.flatMap(c => c.eboard.filter(m => m.email === PROFILES.leader.email)
    .map(m => ({ orgId:null, name:c.name, position:m.title, verified:true }))));
  // admin: membership requests waiting for verification
  const [verifs,setVerifs]=useState(api.LIVE ? [] : [
    { id:"v1", userName:"Priya Shah", email:"priya.shah@stonybrook.edu", orgName:"Debate Society", position:"Treasurer" },
    { id:"v2", userName:"Marcus Lee", email:"marcus.lee@stonybrook.edu", orgName:"Media Club", position:"Vice President" },
  ]);
  const [memLoaded,setMemLoaded]=useState(true); // false while a live login is still fetching the user's clubs
  const leaderOrgs = memberships.filter(m => m.verified).map(m => m.name);
  const orgIds = Object.fromEntries(memberships.filter(m => m.verified && m.orgId).map(m => [m.name, m.orgId]));

  const loadMemberships = async () => {
    try {
      const ms = await api.memberships();
      setMemberships(ms.map(m => ({ orgId:m.organization_id, name:m.organization_name, position:m.position_title || "", verified:m.verified })));
    } catch (e) { setApiError(e.message); }
    setMemLoaded(true);
  };
  const loadVerifs = async () => { try { setVerifs(await api.pendingVerifications()); } catch (e) { setApiError(e.message); } };
  const searchOrgOptions = (q) => api.LIVE
    ? api.searchOrgs(q).then(l => l.map(o => ({ id:o.id, name:o.name })))
    : Promise.resolve(CLUBS.filter(c => c.name.toLowerCase().includes(q.toLowerCase())).map(c => ({ id:null, name:c.name })));
  const joinOrg = async (org, position) => {
    if (api.LIVE) { await api.joinOrg(org.id, position); await loadMemberships(); return; }
    setMemberships(ms => [...ms, { orgId:null, name:org.name, position, verified:false }]);
    setVerifs(v => [...v, { id:`m-${Date.now()}`, userName:profiles.leader.name, email:profiles.leader.email, orgName:org.name, position, mock:true }]);
  };
  const approveVerif = async (it) => {
    try {
      if (api.LIVE) { await api.verifyMember(it.orgId, it.userId, it.position); await loadVerifs(); }
      else {
        setVerifs(v => v.filter(x => x.id !== it.id));
        if (it.mock) setMemberships(ms => ms.map(m => m.name === it.orgName ? { ...m, verified:true } : m));
      }
      setApiError("");
    } catch (e) { setApiError(e.message); throw e; }
  };
  const rejectVerif = async (it) => {
    try {
      if (api.LIVE) { await api.rejectMember(it.orgId, it.userId); await loadVerifs(); }
      else {
        setVerifs(v => v.filter(x => x.id !== it.id));
        if (it.mock) setMemberships(ms => ms.filter(m => m.name !== it.orgName));
      }
      setApiError("");
    } catch (e) { setApiError(e.message); throw e; }
  };

  const loadRequests = async (r, roomList) => {
    const byId = Object.fromEntries(roomList.map(x => [x.id, x]));
    try {
      let raw;
      if (r==="admin") {
        const [p,a,d] = await Promise.all([api.adminRequests("pending"), api.adminRequests("approved"), api.adminRequests("denied")]);
        const w = await api.adminRequests("waitlisted");
        raw = [...p,...a,...d,...w];
      } else raw = await api.myRequests();
      setRequests(raw.map(x => api.adaptRequest(x, byId)));
    } catch (e) { setApiError(e.message); }
  };
  const loadLive = async (r, user) => {
    setDataLoading(true); setRooms([]); setClubs([]);
    setRequests([]); setRoomsLoading(true); setApiError("");
    setMemberships([]); setVerifs([]); setMemLoaded(r !== "leader"); loadClubs();
    setProfiles(p => ({ ...p, [r]: { ...p[r], id:user.id, name:user.name, email:user.email } }));
    setThreads([]); setPosts([]); loadConvos();
    if (r !== "venue") { setPostsLoading(true); loadForum().then(() => setPostsLoading(false)); }
    let roomList = [];
    try { roomList = (await api.rooms()).map(api.adaptRoom); setRooms(roomList); }
    catch (e) { setApiError(e.message); }
    setRoomsLoading(false);
    if (r==="leader") await loadMemberships();
    if (r==="admin") await loadVerifs();
    await loadRequests(r, roomList);
    setDataLoading(false);
  };

  // returns the picks that failed ([] when every day was requested)
  const submitRequest = async (room,picks,org,eventName) => {
    if (api.LIVE) {
      const orgId = orgIds[org];
      if (!orgId) throw new Error("You aren't verified for a club yet. Add your club in your profile and wait for an admin to verify you.");
      const failed = [];
      for (const p of picks) {
        try { await api.createRequest(api.requestBody(room, orgId, p, eventName || `${org} event`)); }
        catch (e) { failed.push({ pick:p, msg:e.message }); }
      }
      await loadRequests(role, rooms);
      return failed;
    }
    setRequests(r => [...r, ...picks.map((p,i) => ({ id:Date.now()+i, room, org, date:p.label, time:`${p.start}–${p.end}`, status:"pending", event:eventName }))]);
    return [];
  };
  const decide = async (id,status,reason) => {
    if (api.LIVE) {
      try { status==="approved" ? await api.approve(id) : await api.deny(id, reason); setApiError(""); }
      catch (e) { setApiError(e.message); }
      await loadRequests(role, rooms);
      return;
    }
    setRequests(r => r.map(req => req.id===id ? { ...req, status, reason } : req));
  };

  // ---- waitlist (live server only) ----
  const [waitlistLine,setWaitlistLine]=useState([]);
  const [wlLoading,setWlLoading]=useState(false);
  const [mockWl,setMockWl]=useState({});   // waitlist lines when running without the live server
  const wlRoom = openRoom;   // waitlist is only used inside the booking popup
  const loadWaitlist = async room => {
    if (!api.LIVE || !room) { setWaitlistLine([]); return; }
    setWlLoading(true);
    try { setWaitlistLine(await api.roomWaitlist(room.id)); } catch (e) { setApiError(e.message); }
    setWlLoading(false);
  };
  useEffect(() => { setWaitlistLine([]); loadWaitlist(wlRoom); }, [wlRoom?.id]);

  // returns the picks that failed, same contract as submitRequest
  const joinWaitlist = async (room,picks,org,eventName) => {
    if (!api.LIVE) {
      const cur = mockWl[room.id] || [];
      const pos = picks.map(p => cur.filter(e => e.start.startsWith(p.key)).length + 1);
      setMockWl(m => ({ ...m, [room.id]:[...(m[room.id] || []), ...picks.map((p,i) => ({ position:pos[i],
        start:`${p.key}T${p.start}:00`, end:`${p.key}T${p.end}:00`, is_yours:true, request_id:`mock-${Date.now()}-${i}` }))] }));
      setRequests(r => [...r, ...picks.map((p,i) => ({ id:Date.now()+i, room, org, date:p.label, time:`${p.start}–${p.end}`,
        status:"waitlisted", position:pos[i], event:eventName }))]);
      return [];
    }
    const orgId = orgIds[org];
    if (!orgId) throw new Error("You aren't verified for a club yet.");
    const failed = [];
    for (const p of picks) {
      try { await api.createRequest({ ...api.requestBody(room, orgId, p, eventName), join_waitlist_if_taken:true }); }
      catch (e) { failed.push({ pick:p, msg:e.message }); }
    }
    await loadWaitlist(room);
    await loadRequests(role, rooms);
    return failed;
  };

  const cancelRequest = async (id) => {
    if (api.LIVE) {
      try {
        const req = requests.find(r => r.id===id);
        if (req?.status==="waitlisted") await api.leaveWaitlist(id); else await api.cancelRequest(id);
        setApiError("");
      }
      catch (e) { setApiError(e.message); throw e; }
      await loadRequests(role, rooms);
      return;
    }
    setRequests(r => r.map(req => req.id===id ? { ...req, status:"cancelled", reason:"" } : req));
  };

  // ---- messages: conversations between people ----
  const [threads,setThreads]=useState(THREADS);
  const [activeThread,setActiveThread]=useState(null);
  const selfName = profiles[role].name;
  const selfId = profiles[role].id || profiles[role].email;

  const loadConvos = async () => {
    try { setThreads((await api.conversations()).map(api.adaptConversation)); } catch (e) { setApiError(e.message); }
  };
  const searchPeople = (q) => api.LIVE
    ? api.searchUsers(q).then(l => l.map(u => ({ id:u.id, name:u.name, sub:u.email })))
    : Promise.resolve(PEOPLE.filter(p => p.name.toLowerCase().includes(q.toLowerCase())));
  const sendMessage = async (threadId,text) => {
    if (api.LIVE) {
      try { await api.sendMessage(threadId, text); await loadConvos(); setApiError(""); } catch (e) { setApiError(e.message); }
      return;
    }
    setThreads(ts => ts.map(t => t.id===threadId
      ? { ...t, messages:[...t.messages, { id:Date.now(), fromId:selfId, from:selfName, text, time:"Just now" }] } : t));
  };
  const startChat = async (person) => {
    if (person.id === selfId) return;
    const existing = threads.find(t => t.participants.length===2 && t.participants.some(p => p.id===person.id) && t.participants.some(p => p.id===selfId));
    if (existing) { setActiveThread(existing.id); return; }
    if (api.LIVE) {
      try {
        const c = api.adaptConversation(await api.createConversation([person.id]));
        setThreads(ts => ts.some(t => t.id===c.id) ? ts : [...ts, c]);
        setActiveThread(c.id); setApiError("");
      } catch (e) { setApiError(e.message); }
      return;
    }
    const id = Date.now();
    setThreads(ts => [...ts, { id, participants:[{ id:selfId, name:selfName },{ id:person.id, name:person.name }], subject:"", messages:[] }]);
    setActiveThread(id);
  };

  // unread = messages from other people that this user hasn't opened yet (counts remembered per user in this browser)
  const [seen,setSeen]=useState(() => { try { return JSON.parse(localStorage.getItem("cr_seen") || "{}"); } catch { return {}; } });
  useEffect(() => { try { localStorage.setItem("cr_seen", JSON.stringify(seen)); } catch { /* ignore */ } }, [seen]);
  const myThreads = threads.filter(t => t.participants.some(p => p.id===selfId));
  const unreadMap = Object.fromEntries(myThreads.map(t => {
    const n = seen[`${selfId}|${t.id}`] ?? 0;
    return [t.id, t.messages.slice(n).filter(m => m.fromId !== selfId).length];
  }));
  const unreadTotal = Object.values(unreadMap).reduce((x,y)=>x+y, 0);

  // ---- routing: the URL is the source of truth for which page is showing ----
  const seg = path.split("/").filter(Boolean).map(dec);
  const authView = seg[0]==="login" ? "login" : seg[0]==="signup" ? "signup" : null;
  const page = seg[0] || "";
  const viewingClub = seg[0]==="clubs" && seg[1] ? seg[1] : null;
  const viewingRoomId = seg[0]==="find" && seg[1]==="rooms" && seg[2] ? seg[2] : null;
  const viewingRoom = viewingRoomId!=null ? rooms.find(r => String(r.id)===viewingRoomId) : null;

    // live: keep conversations fresh (fast on the Messages page, slower elsewhere, and on tab focus)
  const onMessages = page === "messages";
  useEffect(() => {
    if (!api.LIVE || !signedIn) return;
    const refresh = () => { if (!document.hidden) loadConvos(); };
    const t = setInterval(refresh, onMessages ? 3000 : 12000);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [signedIn, role, onMessages]);
  
  useEffect(() => {
    if (!signedIn || page!=="messages" || activeThread==null) return;
    const t = myThreads.find(x => x.id===activeThread);
    const key = `${selfId}|${activeThread}`;
    if (t && seen[key] !== t.messages.length) setSeen(s => ({ ...s, [key]: t.messages.length }));
  });

  // ---- forum ----
  const [posts,setPosts]=useState(FORUM_POSTS);
  const [postsLoading,setPostsLoading]=useState(false);
  const loadForum = async () => {
    try { setPosts((await api.forumPosts()).map(api.adaptPost)); } catch (e) { setApiError(e.message); }
  };
  const addPost = async ({ title, body, category, org }) => {
    if (api.LIVE) {
      await api.createPost({ title, body, category, organization_id: orgIds[org] || null });
      await loadForum(); return;
    }
    setPosts(ps => [{ id:Date.now(), author:selfName, org, category, title, body, replies:[] }, ...ps]);
  };
  const addReply = async (postId, text, org) => {
    if (api.LIVE) { await api.createReply(postId, { body:text, organization_id: orgIds[org] || null }); await loadForum(); return; }
    setPosts(ps => ps.map(p => p.id===postId ? { ...p, replies:[...p.replies, { author:selfName, org, text }] } : p));
  };

  const allowed = (r, p) => { const s0 = p.split("/").filter(Boolean)[0]; return s0==="profile" || NAV[r].some(([id]) => id===s0); };
  const goPage = (p) => navigate("/"+p);
  const openClub = (name) => navigate("/clubs/"+enc(name));
  const openRoomDetail = (room) => navigate("/find/rooms/"+enc(room.id));
  const messagePerson = (person) => { startChat(person); navigate("/messages"); };

  // Entering the app after login/signup or after restoring a saved session.
  const enter = (r, user, { restored=false } = {}) => {
    setRole(r); setActiveThread(null); setSignedIn(true);
    const want = restored ? window.location.pathname : afterLogin.current;
    afterLogin.current = null;
    if (!want || !allowed(r, want)) navigate("/"+HOME[r], { replace:true });
    else if (!restored) navigate(want, { replace:true });
    if (api.LIVE && user) loadLive(r, user);
  };
  const signIn = (r, user) => enter(r, user);
  const logout = () => { setOpenRoom(null); api.setToken(null); setSignedIn(false); navigate("/login"); };

  // Restore the session on page load (refresh, new tab, shared link).
  const restore = () => {
    setBooting(true); setBootError("");
    api.me()
      .then(u => enter(api.ROLE_FROM_SERVER[u.role] || "leader", u, { restored:true }))
      .catch(e => { if (api.hasToken()) setBootError(e.message); }) // token still set => server problem, not an expired login
      .finally(() => setBooting(false));
  };
  useEffect(() => { if (booting) restore(); }, []); // eslint-disable-line

  // Route guards: signed-out users go to /login; signed-in users can't sit on /login or a page their role doesn't have.
  useEffect(() => {
    if (booting || bootError) return;
    if (!signedIn) {
      if (!authView) { afterLogin.current = path==="/" ? null : path; navigate("/login", { replace:true }); }
    } else if (authView || path==="/" || !allowed(role, path)) {
      navigate("/"+HOME[role], { replace:true });
    }
  }, [booting, bootError, signedIn, path, role]); // eslint-disable-line

  if (booting) return <LoadingScreen />;
  if (bootError) return (
    <div role="alert" style={{ minHeight:"100vh", display:"grid", placeItems:"center", textAlign:"center", padding:24 }}>
      <div><p style={{ fontWeight:600, color:"var(--red)" }}>{bootError}</p>
        <button onClick={restore} style={{ padding:"10px 18px", borderRadius:10, border:"2px solid var(--ink)", fontWeight:700 }}>Try again</button></div>
    </div>);
  if (!signedIn && authView==="signup") return <CreateAccount onCreate={signIn} onLogin={()=>navigate("/login")} />;
  if (!signedIn) return <Login onLogin={signIn} onSignup={()=>navigate("/signup")} />;
  if (authView) return null; // redirecting away from /login

  return (
    <div>
      <Header role={role} page={page} setPage={goPage}
        onProfile={()=>goPage("profile")} unread={unreadTotal} verifCount={verifs.length} name={profiles[role].name} onLogout={logout} />
      {apiError && <div role="alert" className="app-banner" style={{ maxWidth:900, margin:"14px auto 0", padding:"10px 16px", background:"var(--red-wash)", color:"var(--red)", border:"2px solid var(--red)", borderRadius:10, fontWeight:600, fontSize:14.5 }}>{apiError}</div>}

      {role==="leader" && !dataLoading && memLoaded && leaderOrgs.length===0 && page!=="profile" && (
        <div role="status" className="app-banner" style={{ maxWidth:900, margin:"14px auto 0", padding:"10px 16px", background:"var(--amber-wash)", border:"2px solid var(--amber)",
          borderRadius:10, fontWeight:600, fontSize:14.5, color:"var(--ink)", display:"flex", gap:12, alignItems:"center", flexWrap:"wrap" }}>
          <span style={{ flex:1, minWidth:200 }}>Your account is not verified yet, so you can't request bookings.</span>
          <button onClick={()=>goPage("profile")} style={{ padding:"6px 12px", borderRadius:8, border:"2px solid var(--ink)", color:"var(--ink)", background:"var(--card)", fontWeight:700, fontSize:14 }}>Add your club</button>
        </div>
      )}

      {viewingClub ? (
        <ClubProfile name={viewingClub} clubs={clubs} requests={requests} selfId={selfId} onBack={()=>back("/clubs")} onMessagePerson={messagePerson} />
      ) : viewingRoomId!=null ? (
        viewingRoom
          ? <RoomDetail room={viewingRoom} onBack={()=>back("/find")} onBook={setOpenRoom} role={role} orgs={leaderOrgs} orgIds={orgIds} />
          : <main className="page" style={{ maxWidth:900, margin:"0 auto", padding:"24px", fontWeight:600 }}>
              {roomsLoading ? "Loading room…" : <>Room not found. <button onClick={()=>navigate("/find")} style={{ color:"var(--link)", fontWeight:700 }}>Back to search</button></>}
            </main>
      ) : (
        <>
          {dataLoading && ["requests","approvals","verifications"].includes(page) && <LoadingScreen inline label="Loading your data…" />}
          {role==="leader" && page==="find" && <FindSpace rooms={rooms} loading={roomsLoading} onView={openRoomDetail} onBook={setOpenRoom} />}
          {role==="leader" && page==="requests" && !dataLoading && <MyRequests onCancel={cancelRequest} clubs={clubs} requests={requests.filter(r=>leaderOrgs.includes(r.org))} showOrg={leaderOrgs.length>1} />}
          {role==="admin" && page==="approvals" && !dataLoading && <Approvals clubs={clubs} requests={requests} onDecide={decide} onOpenClub={openClub} />}
          {role==="admin" && page==="verifications" && !dataLoading && <Verifications items={verifs} onApprove={approveVerif} onReject={rejectVerif} onOpenClub={openClub} />}
          {role==="admin" && page==="rooms" && <RoomsAdmin rooms={rooms} requests={requests} loading={roomsLoading} />}
          {role==="admin" && page==="analytics" && <Analytics />}
          {role==="venue" && page==="rooms" && <VenueRooms requests={requests} onOpenClub={openClub} />}
          {(role==="leader" || role==="admin") && page==="forum" && <Forum posts={posts} onPost={addPost} onReply={addReply} clubs={role==="leader" ? leaderOrgs : []} loading={postsLoading} onOpenClub={openClub} />}
          {page==="clubs" && <Clubs clubs={clubs} loading={clubsLoading} onOpenClub={openClub} />}
          {page==="profile" && <Profile key={role} role={role} profile={profiles[role]} onOpenClub={openClub}
            onSave={patch=>setProfiles(p=>({ ...p, [role]:{ ...p[role], ...patch } }))}
            memberships={memberships} membershipsLoaded={memLoaded} onSearchOrgs={searchOrgOptions} onJoin={joinOrg} />}
          {page==="messages" && <Messages threads={threads} onSend={sendMessage} onStartChat={startChat}
            activeId={activeThread} setActiveId={setActiveThread} selfId={selfId} onSearchPeople={searchPeople} unreadMap={unreadMap} />}
        </>
      )}

      {openRoom && <BookingDrawer room={openRoom} onClose={()=>setOpenRoom(null)} onSubmit={submitRequest} orgs={leaderOrgs}
        onJoinWaitlist={joinWaitlist} waitlistLine={api.LIVE ? waitlistLine : (mockWl[openRoom?.id] || [])} />}
    </div>
  );
}
