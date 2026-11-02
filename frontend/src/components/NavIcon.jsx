// Simple line icons for the phone/tablet tab bar. 24x24, drawn with the current text color.
const PATHS = {
  requests: <><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4.5h6M9 10h6M9 14h6M9 18h3" /></>,
  find: <><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></>,
  forum: <><path d="M4 5h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-4 3.5V15H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" transform="translate(1 0)" /><path d="M19 9h1a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-1v2.5L16 19h-3" /></>,
  messages: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3.5 6.5L12 13l8.5-6.5" /></>,
  clubs: <><circle cx="9" cy="8.5" r="3.2" /><path d="M3 19.5c.6-3.3 3-5 6-5s5.4 1.7 6 5" /><circle cx="17" cy="9.5" r="2.5" /><path d="M16.5 14.6c2.4.2 4 1.7 4.5 4.4" /></>,
  approvals: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8.5 12.5l2.4 2.4 4.8-5.3" /></>,
  verifications: <><path d="M12 3l7 3v5.5c0 4.3-3 7.6-7 9.5-4-1.9-7-5.2-7-9.5V6z" /><path d="M9 12l2.2 2.2L15.5 10" /></>,
  rooms: <><path d="M5 21V4.5A1.5 1.5 0 0 1 6.5 3h11A1.5 1.5 0 0 1 19 4.5V21" /><path d="M3 21h18" /><circle cx="15" cy="12.5" r=".9" fill="currentColor" stroke="none" /></>,
  export: <><path d="M12 4v10M8 10.5l4 4 4-4" /><path d="M4.5 19.5h15" /></>,
  analytics: <><path d="M4 20h16" /><path d="M7 16v-5M12 16V6M17 16v-8" /></>,
  profile: <><circle cx="12" cy="8.5" r="3.8" /><path d="M4.5 20.5c.9-3.8 3.9-6 7.5-6s6.6 2.2 7.5 6" /></>,
  more: <><circle cx="5.5" cy="12" r="1.6" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" /><circle cx="18.5" cy="12" r="1.6" fill="currentColor" stroke="none" /></>,
  logout: <><path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" /><path d="M10 8l-4 4 4 4M6 12h10" /></>,
  access: <><circle cx="12" cy="4.8" r="1.8" fill="currentColor" stroke="none" /><path d="M5 8.5c2.3.7 4.6 1 7 1s4.7-.3 7-1M12 9.5v4.5M12 14l-3 6.5M12 14l3 6.5" /></>,
  map: <><path d="M9 4L3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5z" /><path d="M9 4v13.5M15 6.5V20" /></>,
};

export default function NavIcon({ name, size = 24 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name] || PATHS.more}
    </svg>
  );
}
