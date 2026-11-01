import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import './CampusMap.css';
import * as api from '../api.js';
import Pill from './Pill.jsx';
import Feature from './Feature.jsx';

// A full-page campus map in the style of Google Maps: drag to move, scroll or pinch to zoom,
// a floating search box that flies to a building or room, and one pin per building.
// Clicking a pin opens a card with that building's rooms and their details.
//
// Building positions come from GET /rooms/buildings?include_location=true (25Live data).
// The "All buildings" list in the card lists every building too, so everything is reachable
// without using the map itself (keyboard and screen reader users).

const SBU_CENTER = [40.9126, -73.1234];   // middle of the main campus
const CAMPUS_ZOOM = 16, BUILDING_ZOOM = 18;
const SIZES = [0, 20, 50, 100, 200];      // "Holds at least" choices
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const CREDIT = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

let buildingsCache = null;   // one fetch per page load, shared by every map
function loadBuildings() {
  if (!api.LIVE) return Promise.resolve([]);
  if (!buildingsCache) buildingsCache = api.buildings({ includeLocation: true }).catch(e => { buildingsCache = null; throw e; });
  return buildingsCache;
}

export default function CampusMap({ rooms, onView, onBook, getBuildings = loadBuildings }) {
  const [places, setPlaces] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [card, setCard] = useState(null);           // null | "list" | building name
  const [focusRoom, setFocusRoom] = useState(null); // room id to highlight in the card
  const [q, setQ] = useState("");
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [minCap, setMinCap] = useState(0);
  const [bookableOnly, setBookableOnly] = useState(false);

  useEffect(() => {
    let off = false;
    setError("");
    getBuildings()
      .then(list => { if (!off) setPlaces(list || []); })
      .catch(e => { if (!off) { setPlaces([]); setError(e.message || "Couldn't load building locations."); } });
    return () => { off = true; };
  }, [attempt]); // eslint-disable-line react-hooks/exhaustive-deps

  // Rooms that pass the chips, grouped by building, with each building's position (if known).
  const buildings = useMemo(() => {
    const where = new Map((places || []).filter(p => p.latitude != null && p.longitude != null)
      .map(p => [p.name, [p.latitude, p.longitude]]));
    const groups = new Map();
    for (const r of rooms) {
      if (minCap && r.cap < minCap) continue;
      if (bookableOnly && r.closed) continue;
      const name = r.building || "Other locations";
      if (!groups.has(name)) groups.set(name, { name, rooms: [], pos: where.get(name) || null });
      groups.get(name).rooms.push(r);
    }
    for (const g of groups.values()) g.rooms.sort((a, b) => a.name.localeCompare(b.name));
    return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [rooms, places, minCap, bookableOnly]);
  const onMap = buildings.filter(b => b.pos);
  const current = card && card !== "list" ? buildings.find(b => b.name === card) : null;
  useEffect(() => { if (card && card !== "list" && !current) setCard(null); }, [card, current]);  // filtered away

  // ---- search suggestions: buildings first, then rooms ----
  const suggestions = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    const bs = buildings.filter(b => b.name.toLowerCase().includes(t)).slice(0, 5)
      .map(b => ({ key: `b:${b.name}`, kind: "building", label: b.name, sub: `${b.rooms.length} room${b.rooms.length === 1 ? "" : "s"}`, building: b }));
    const rs = buildings.flatMap(b => b.rooms.filter(r => r.name.toLowerCase().includes(t)).map(r => ({ r, b }))).slice(0, 6)
      .map(({ r, b }) => ({ key: `r:${r.id}`, kind: "room", label: r.name, sub: b.name, building: b, room: r }));
    return [...bs, ...rs];
  }, [q, buildings]);

  // ---- the Leaflet map ----
  const box = useRef(null), map = useRef(null), pins = useRef(null);
  useEffect(() => {
    const m = L.map(box.current, { zoomControl: false, scrollWheelZoom: true, minZoom: 13, maxZoom: 19 })
      .setView(SBU_CENTER, CAMPUS_ZOOM);
    L.tileLayer(TILES, { attribution: CREDIT, maxZoom: 19 }).addTo(m);
    L.control.zoom({ position: "bottomright" }).addTo(m);
    map.current = m;
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => m.invalidateSize()) : null;
    ro?.observe(box.current);
    return () => { ro?.disconnect(); m.remove(); map.current = null; };
  }, []);

  // When the locations first arrive, frame all the buildings once. After that the map
  // only moves when someone drags it, searches, or picks a building.
  const framed = useRef(false);
  useEffect(() => {
    if (framed.current || !map.current || onMap.length === 0) return;
    framed.current = true;
    // Extra room at the top so no pin starts hidden under the search bar and chips.
    // (On phones the chips wrap under the search bar, so leave more.)
    const top = window.innerWidth < 640 ? 170 : 130;
    if (onMap.length > 1) map.current.fitBounds(L.latLngBounds(onMap.map(b => b.pos)),
      { paddingTopLeft: [50, top], paddingBottomRight: [50, 50], maxZoom: 17 });
    else map.current.setView(onMap[0].pos, 17);
  }, [onMap]);

  const goTo = (b, roomId = null) => {
    setCard(b.name); setFocusRoom(roomId); setSuggestOpen(false);
    if (b.pos && map.current) map.current.flyTo(b.pos, Math.max(map.current.getZoom(), BUILDING_ZOOM), { duration: 0.8 });
  };

  // Pins, redrawn when the results or the open building change.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    pins.current?.remove();
    const layer = L.layerGroup();
    for (const b of onMap) {
      const n = b.rooms.length, on = b.name === card;
      L.marker(b.pos, {
        icon: L.divIcon({ className: `cr-pin${on ? " cr-pin-on" : ""}`,
          html: `<span class="cr-pin-dot"><span>${n}</span></span>`, iconSize: [40, 48], iconAnchor: [20, 44] }),
        title: `${b.name}: ${n} room${n === 1 ? "" : "s"}`, alt: b.name, keyboard: true, riseOnHover: true,
        zIndexOffset: on ? 1000 : 0,
      }).bindTooltip(b.name, { direction: "top", offset: [0, -44] })
        .on("click", () => goTo(b))
        .addTo(layer);
    }
    layer.addTo(m);
    pins.current = layer;
  }, [onMap, card]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll the searched-for room into view in the card.
  useEffect(() => {
    if (!focusRoom) return;
    document.getElementById(`cr-room-${focusRoom}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusRoom, card]);

  const pickSuggestion = s => { setQ(s.label); goTo(s.building, s.room?.id || null); };
  const loading = places === null;

  return (
    <div className="gm">
      <div ref={box} className="gm-map" role="region" aria-label="Campus map. Drag to move, scroll to zoom." />

      {/* search, like Google Maps */}
      <div className="gm-top">
        <div className="gm-search" role="combobox" aria-expanded={suggestOpen && suggestions.length > 0} aria-haspopup="listbox">
          <button className="gm-list-btn" onClick={() => setCard(c => c === "list" ? null : "list")} aria-label="All buildings" title="All buildings">☰</button>
          <input value={q} onChange={e => { setQ(e.target.value); setSuggestOpen(true); }} onFocus={() => setSuggestOpen(true)}
            onKeyDown={e => { if (e.key === "Enter" && suggestions[0]) pickSuggestion(suggestions[0]); if (e.key === "Escape") setSuggestOpen(false); }}
            placeholder="Search buildings and rooms" aria-label="Search buildings and rooms" aria-autocomplete="list" aria-controls="gm-suggest" />
          {q && <button className="gm-clear" onClick={() => { setQ(""); setSuggestOpen(false); }} aria-label="Clear search">×</button>}
          {suggestOpen && suggestions.length > 0 && (
            <ul id="gm-suggest" className="gm-suggest" role="listbox">
              {suggestions.map(s => (
                <li key={s.key} role="option" aria-selected="false">
                  <button onClick={() => pickSuggestion(s)}>
                    <span className="gm-suggest-icon" aria-hidden="true">{s.kind === "building" ? "🏛" : "🚪"}</span>
                    <span><b>{s.label}</b><small>{s.sub}</small></span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {suggestOpen && q.trim() && suggestions.length === 0 && <div className="gm-suggest gm-suggest-empty">No buildings or rooms match.</div>}
        </div>
        <div className="gm-chips" role="group" aria-label="Filters">
          <label className="gm-chip">
            <span className="sr-only">Holds at least</span>
            <select value={minCap} onChange={e => setMinCap(+e.target.value)}>
              {SIZES.map(n => <option key={n} value={n}>{n ? `Holds ${n}+` : "Any size"}</option>)}
            </select>
          </label>
          <button className="gm-chip" aria-pressed={bookableOnly} onClick={() => setBookableOnly(v => !v)}>
            {bookableOnly ? "✓ " : ""}Bookable only
          </button>
        </div>
      </div>

      {loading && !card && <div className="gm-toast" role="status">Finding buildings… the first load can take a little while.</div>}
      {error && <div className="gm-toast gm-toast-error" role="alert">{error} <button onClick={() => setAttempt(a => a + 1)}>Try again</button></div>}
      {!api.LIVE && !card && <div className="gm-toast">Building pins come from 25Live, so they show when the app is connected to the server.</div>}

      {/* the card: a building's rooms, or the list of all buildings */}
      {card && (
        <section className="gm-card" aria-label={current ? `Rooms in ${current.name}` : "All buildings"}>
          <div className="gm-card-head">
            {current ? (
              <div>
                <h2>{current.name}</h2>
                <div className="gm-sub">{current.rooms.length} room{current.rooms.length === 1 ? "" : "s"}{!current.pos && " · location not on the map"}</div>
              </div>
            ) : (
              <div><h2>All buildings</h2><div className="gm-sub">{buildings.length} buildings · pick one to see its rooms</div></div>
            )}
            <button className="gm-close" onClick={() => setCard(null)} aria-label="Close">×</button>
          </div>

          {current ? (
            <ul className="gm-rooms">
              {current.rooms.map(r => (
                <li key={r.id} id={`cr-room-${r.id}`} className={`gm-room${focusRoom === r.id ? " gm-room-focus" : ""}`}>
                  <div className="gm-room-top">
                    <h3>{r.name}</h3>
                    <Pill status={r.status} small />
                  </div>
                  <div className="gm-sub">Holds {r.cap} · {r.type}</div>
                  {r.features?.length > 0 && (
                    <div className="gm-features">
                      {r.features.slice(0, 4).map(f => <Feature key={f}>{f}</Feature>)}
                      {r.features.length > 4 && <Feature>+{r.features.length - 4} more</Feature>}
                    </div>
                  )}
                  {r.note && <div className="gm-note">{r.note}</div>}
                  {!r.closed && (onView || onBook) && (
                    <div className="gm-actions">
                      {onView && <button onClick={() => onView(r)} className="gm-link">Room details ›</button>}
                      {onBook && <button onClick={() => onBook(r)} className="gm-book">Book</button>}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <ul className="gm-buildings">
              {buildings.map(b => (
                <li key={b.name}>
                  <button onClick={() => goTo(b)}>
                    <span>{b.name}{!b.pos && <small> · not on the map</small>}</span>
                    <span className="gm-count">{b.rooms.length}</span>
                  </button>
                </li>
              ))}
              {buildings.length === 0 && <li className="gm-sub">No rooms match those filters.</li>}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
