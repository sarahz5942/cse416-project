import { useEffect, useRef, useState } from 'react';
import { useIsCompact } from '../useMedia.js';
import useDialog from '../a11y/useDialog.js';
import NavIcon from './NavIcon.jsx';

export const NAV = {
  leader: [["requests","My requests"],["find","Find a space"],["forum","Forum"],["messages","Messages"],["clubs","Clubs"], ["map", "Campus map"]],
  admin: [["approvals","Approvals"],["verifications","Verifications"],["rooms","Rooms"],["analytics","Analytics"],["forum","Forum"],["messages","Messages"],["clubs","Clubs"], ["map", "Campus map"]],
  venue: [["rooms","Rooms"],["messages","Messages"],["clubs","Clubs"], ["map", "Campus map"]],
};
// Shorter names for the phone tab bar, where each tab is about 75px wide.
const SHORT = { requests:"Requests", find:"Find", verifications:"Verify", map:"Map" };
const TABS_MAX = 5;  // more than this and the extras go under "More"

function Badge({ n, label, dot = false }) {
  if (!n) return null;
  if (dot) return <span className="tab-badge tab-badge-dot" aria-hidden="true" />;
  return (
    <span className="tab-badge" style={{ marginLeft:7, minWidth:20, height:20, padding:"0 6px", borderRadius:999,
      background:"var(--red-wash)", color:"var(--red)", fontSize:12, fontWeight:700, display:"inline-grid", placeItems:"center", verticalAlign:"middle" }}>
      <span aria-hidden="true">{n>9 ? "9+" : n}</span>
      <span className="sr-only">, {n} {label}</span>
    </span>
  );
}

export default function Header(props) {
  const compact = useIsCompact();
  // Lets page CSS (and the accessibility button, which lives outside #root) make room for the tab bar.
  useEffect(() => {
    document.documentElement.classList.toggle("has-tabbar", compact);
    return () => document.documentElement.classList.remove("has-tabbar");
  }, [compact]);
  return compact ? <CompactHeader {...props} /> : <DesktopHeader {...props} />;
}

// ---- Desktop: the original top bar --------------------------------------------
function DesktopHeader({ role, page, setPage, unread = 0, verifCount = 0, name = "", onLogout, onProfile }) {
  const nav = NAV[role];
  return (
    <header className="desk-header" style={{ display:"flex", alignItems:"center", justifyContent:"space-between", padding:"0 24px",
      height:64, background:"var(--card)", borderBottom:"3px solid var(--ink)", position:"sticky", top:0, zIndex:20, gap:8 }}>
      <div className="desk-left" style={{ display:"flex", alignItems:"center", gap:28, minWidth:0 }}>
        <div style={{ display:"flex", alignItems:"center", gap:9 }}>
          <span style={{ fontFamily:"var(--display)", fontSize:22, letterSpacing:".01em", whiteSpace:"nowrap" }}>CampusReserve</span>
        </div>
        <nav aria-label="Main" className="desk-nav" style={{ display:"flex", gap:4 }}>
          {nav.map(([id,label]) => (
            <button key={id} onClick={() => setPage(id)} aria-current={page===id ? "page" : undefined} style={{
              fontSize:15, fontWeight:500, padding:"8px 13px", borderRadius:8, whiteSpace:"nowrap",
              color: page===id ? "var(--ink)" : "var(--ink-faint)",
              background: page===id ? "var(--line-soft)" : "transparent" }}>
              {label}
              {id==="verifications" && <Badge n={verifCount} label="waiting" />}
              {id==="messages" && <Badge n={unread} label="unread" />}
            </button>
          ))}
        </nav>
      </div>
      <div style={{ display:"flex", alignItems:"center", gap:14 }}>
        <button onClick={onProfile} title="My profile" aria-label="My profile" aria-current={page==="profile" ? "page" : undefined}
          style={{ width:36, height:36, borderRadius:999, background:"var(--blue-wash)", color:"var(--link)",
          display:"grid", placeItems:"center", fontWeight:700, fontSize:14, border: page==="profile" ? "2px solid var(--blue)" : "2px solid transparent" }}>
          {(name.trim()[0] || "?").toUpperCase()}
        </button>
        <button onClick={onLogout} style={{ fontSize:14, fontWeight:600, color:"var(--ink-soft)", padding:"7px 12px",
          borderRadius:8, border:"2px solid var(--ink)", whiteSpace:"nowrap" }}>Log out</button>
      </div>
    </header>
  );
}

// ---- Phones and tablets: slim top bar + tab bar along the bottom ------------------
function CompactHeader({ role, page, setPage, unread = 0, verifCount = 0, name = "", onLogout, onProfile }) {
  const nav = NAV[role];
  const tabs = nav.length > TABS_MAX ? nav.slice(0, TABS_MAX - 1) : nav;
  const extra = nav.length > TABS_MAX ? nav.slice(TABS_MAX - 1) : [];
  const [moreOpen, setMoreOpen] = useState(false);
  const [a11yOpen, setA11yOpen] = useState(false);
  const count = id => id==="messages" ? unread : id==="verifications" ? verifCount : 0;
  const moreCount = extra.reduce((n, [id]) => n + count(id), 0);
  const moreActive = page==="profile" || extra.some(([id]) => id===page);

  // The accessibility panel is its own React root; talk to it with window events.
  useEffect(() => {
    const on = e => setA11yOpen(!!e.detail?.open);
    window.addEventListener("cr:a11y-state", on);
    return () => window.removeEventListener("cr:a11y-state", on);
  }, []);
  const go = id => { setMoreOpen(false); id==="profile" ? onProfile() : setPage(id); window.scrollTo(0, 0); };

  return (
    <>
      <header className="topbar">
        <span className="topbar-logo">CampusReserve</span>
        <div className="topbar-actions">
          <button className="topbar-icon" onClick={() => window.dispatchEvent(new Event("cr:a11y-toggle"))}
            aria-label="Accessibility settings" aria-expanded={a11yOpen} title="Accessibility settings">
            <NavIcon name="access" size={22} />
          </button>
          <button onClick={onProfile} className="topbar-avatar" aria-label="My profile" aria-current={page==="profile" ? "page" : undefined}
            data-active={page==="profile" || undefined}>
            {(name.trim()[0] || "?").toUpperCase()}
          </button>
          <button onClick={onLogout} className="topbar-logout">Log out</button>
        </div>
      </header>

      <nav className="tabbar" aria-label="Main">
        {tabs.map(([id,label]) => (
          <button key={id} className="tab" onClick={() => go(id)} aria-current={page===id ? "page" : undefined}>
            <span className="tab-icon"><NavIcon name={id} /><Badge n={count(id)} dot /></span>
            <span className="tab-label">{SHORT[id] || label}</span>
            {count(id) > 0 && <span className="sr-only">, {count(id)} {id==="messages" ? "unread" : "waiting"}</span>}
          </button>
        ))}
        {extra.length > 0 && (
          <button className="tab" onClick={() => setMoreOpen(true)} aria-haspopup="dialog" aria-expanded={moreOpen}
            aria-current={moreActive ? "page" : undefined}>
            <span className="tab-icon"><NavIcon name="more" /><Badge n={moreCount} dot /></span>
            <span className="tab-label">More</span>
            {moreCount > 0 && <span className="sr-only">, {moreCount} new</span>}
          </button>
        )}
      </nav>

      {moreOpen && <MoreSheet items={extra} page={page} count={count} onGo={go} onLogout={() => { setMoreOpen(false); onLogout(); }}
        onClose={() => setMoreOpen(false)} />}
    </>
  );
}

function MoreSheet({ items, page, count, onGo, onLogout, onClose }) {
  const panel = useRef(null);
  useDialog(panel, { open: true, onClose });
  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className="more-sheet" role="dialog" aria-modal="true" aria-labelledby="more-title" tabIndex={-1}>
        <div className="sheet-grab" aria-hidden="true" />
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:6 }}>
          <h2 id="more-title" style={{ fontSize:20 }}>More</h2>
          <button onClick={onClose} className="sheet-close" aria-label="Close menu">×</button>
        </div>
        {[...items, ["profile","My profile"]].map(([id,label]) => (
          <button key={id} className="more-item" onClick={() => onGo(id)} aria-current={page===id ? "page" : undefined}>
            <NavIcon name={id} /><span style={{ flex:1 }}>{label}</span>
            <Badge n={count(id)} label={id==="messages" ? "unread" : "waiting"} />
          </button>
        ))}
        <button className="more-item" onClick={onLogout}><NavIcon name="logout" /><span style={{ flex:1 }}>Log out</span></button>
      </div>
    </>
  );
}
