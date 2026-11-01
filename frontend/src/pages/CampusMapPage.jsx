import CampusMap from '../components/CampusMap.jsx';

// The "Campus map" tab: a full-page map. Search, filters and the room card live on the map.
// onView / onBook are only passed for club leaders (the room page and booking are theirs).
export default function CampusMapPage({ rooms = [], loading = false, onView, onBook }) {
  return (
    <main className="gm-page" style={{ position:"relative" }}>
      <h1 className="sr-only">Campus map</h1>
      {loading
        ? <div role="status" style={{ textAlign:"center", padding:40, color:"var(--ink-faint)", fontWeight:600 }}>Loading rooms…</div>
        : <CampusMap rooms={rooms} onView={onView} onBook={onBook} />}
    </main>
  );
}
