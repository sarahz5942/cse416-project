import { useEffect, useRef, useState } from 'react';

const API = import.meta.env.VITE_API_URL;
const EXT = ["jpg", "png", "webp"];
const RETRY_DELAY_MS = 2000;
// Shown for rooms with no photo. The file goes in frontend/public/sbulogo.png.
const PLACEHOLDER_LOGO = "/sbulogo.png";

// Google Form where people can send in a photo of a room that has none. "" hides the link.
// Paste the form's link (Send > link icon), e.g. "https://docs.google.com/forms/d/e/1FAIpQL.../viewform"
const PHOTO_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSduXPiNpuNF2JGSKzO_-3RFhJeboBinCPwzmUkLEXGfKgZfCg/viewform?usp=pp_url&entry.1129355335=ROOM&entry.834608001=roomId&entry.901803129=BUILDING";
// Optional: fill in the form's answers for the room. Get the ids from the form's
// ⋮ menu > "Get pre-filled link" (they look like "entry.123456789"). Leave "" to skip one.
const PHOTO_FORM_FIELDS = { room: "entry.1129355335", building: "entry.901803129", roomId: "entry.834608001" };

function photoFormLink(room) {
  try {
    const url = new URL(PHOTO_FORM_URL);
    const answers = { room: room.name, building: room.building, roomId: String(room.id) };
    let prefilled = false;
    for (const [key, entry] of Object.entries(PHOTO_FORM_FIELDS)) {
      if (entry && answers[key]) { url.searchParams.set(entry, answers[key]); prefilled = true; }
    }
    if (prefilled) url.searchParams.set("usp", "pp_url");
    return url.toString();
  } catch { return null; }   // not a valid link
}

// A soft pulsing box shown while a photo loads (and during the retry wait).
const PULSE_CSS = `@keyframes roomPhotoPulse { 0%,100% { opacity:.55 } 50% { opacity:1 } }
@media (prefers-reduced-motion: reduce) { .room-photo-loading { animation:none !important } }`;

// Shows the room's 25Live photo (retried once after 2 seconds, since the first fetch can be
// slow), then public/rooms/<id>.jpg/.png/.webp, then the SBU logo with "No photo available".
// Failed attempts are never shown, so there's no broken-image icon.
export default function RoomPhoto({ room }) {
  const isLive = /^\d+$/.test(String(room.id));
  const sources = [
    ...(isLive ? [`${API}/rooms/${room.id}/photo`, `${API}/rooms/${room.id}/photo?retry=1`] : []),
    ...EXT.map(ext => `/rooms/${room.id}.${ext}`),
  ];
  const [i, setI] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [logoOk, setLogoOk] = useState(true);  // false if sbulogo.png is missing
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);  // stop a pending retry if the card goes away

  const next = () => {
    setLoaded(false);
    if (isLive && i === 0) timer.current = setTimeout(() => setI(1), RETRY_DELAY_MS);
    else setI(i + 1);
  };

  if (i >= sources.length) {
    const formLink = PHOTO_FORM_URL ? photoFormLink(room) : null;
    return (
      <div
        style={{ width:"100%", height:"100%", minHeight:150, borderRadius:10, border:"2px solid var(--ink)",
          overflow:"hidden", background:"var(--paper)", display:"flex", flexDirection:"column",
          alignItems:"center", justifyContent:"center", gap:8, padding:12 }}>
        {logoOk && (
          <img src={PLACEHOLDER_LOGO} alt="" onError={() => setLogoOk(false)}
            style={{ maxWidth:"70%", maxHeight:80, objectFit:"contain", opacity:0.85 }} />
        )}
        <span role="img" aria-label={`No photo available for ${room.name}`}
          style={{ fontSize:12.5, fontWeight:600, color:"var(--ink-faint)" }}>No photo available</span>
        {formLink && (
          <a href={formLink} target="_blank" rel="noopener noreferrer"
            onClick={e => e.stopPropagation()}   // don't also open the room card the photo sits in
            aria-label={`Share a photo of ${room.name} (opens a Google Form in a new tab)`}
            style={{ fontSize:13, fontWeight:700, color:"var(--link)", textDecoration:"underline", textAlign:"center" }}>
            Have a photo? Share it ↗
          </a>
        )}
      </div>
    );
  }
  return (
    <div style={{ position:"relative", width:"100%", height:"100%", minHeight:150, borderRadius:10,
      border:"2px solid var(--ink)", overflow:"hidden", background:"var(--paper)" }}>
      {!loaded && (
        <>
          <style>{PULSE_CSS}</style>
          <div className="room-photo-loading" aria-hidden="true"
            style={{ position:"absolute", inset:0, display:"grid", placeItems:"center",
              background:"linear-gradient(135deg, var(--paper), var(--card))",
              animation:"roomPhotoPulse 1.4s ease-in-out infinite",
              fontSize:13, fontWeight:600, color:"var(--ink-faint)" }}>
            Loading photo…
          </div>
        </>
      )}
      <img key={sources[i]} src={sources[i]} alt={`${room.name} layout`} loading="lazy"
        onLoad={() => setLoaded(true)} onError={next}
        style={{ width:"100%", height:"100%", objectFit:"cover", display:"block",
          visibility: loaded ? "visible" : "hidden" }} />
    </div>
  );
}