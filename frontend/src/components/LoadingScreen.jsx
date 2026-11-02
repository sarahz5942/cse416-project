import { useState, useEffect } from 'react';

const TIPS = [
  "Waking up the Seawolves…",
  "Asking 25Live which rooms are free…",
  "Fun fact: Stony Brook's mascot is Wolfie the Seawolf 🐺",
  "Making a lap around the Academic Mall…",
  "Lining up your clubs and requests…",
  "Almost there. Go Seawolves!",
];

const CSS = `
.sw-wolf{font-size:58px;line-height:1;animation:sw-bob 1.6s ease-in-out infinite}
@keyframes sw-bob{0%,100%{transform:translateY(0) rotate(-5deg)}50%{transform:translateY(-14px) rotate(5deg)}}
.sw-waves{display:block;overflow:hidden;margin:2px auto 0}
.sw-waves path{animation:sw-wave 2.4s linear infinite}
@keyframes sw-wave{from{transform:translateX(0)}to{transform:translateX(-110px)}}
.sw-paws{display:flex;gap:10px;justify-content:center;margin:16px 0 14px;font-size:22px}
.sw-paws span{opacity:.15;animation:sw-step 1.5s ease-in-out infinite}
@keyframes sw-step{0%,100%{opacity:.15;transform:translateY(0)}50%{opacity:1;transform:translateY(-5px)}}
.sw-tip{animation:sw-fade .5s ease}
@keyframes sw-fade{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion: reduce){.sw-wolf,.sw-waves path,.sw-paws span,.sw-tip{animation:none}.sw-paws span{opacity:.7}}
`;

// Seawolf-themed loading state. Use `inline` for a smaller version inside a page.
export default function LoadingScreen({ label = "Loading CampusReserve…", inline = false }) {
  const [i, setI] = useState(0);
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const t = setInterval(() => setI(n => (n + 1) % TIPS.length), 5000);
    const s = setTimeout(() => setSlow(true), 8000);
    return () => { clearInterval(t); clearTimeout(s); };
  }, []);

  return (
    <div role="status" aria-live="polite" style={{ textAlign: 'center', padding: inline ? '36px 20px' : '90px 20px' }}>
      <style>{CSS}</style>
      <div className="sw-wolf" aria-hidden="true">🐺</div>
      <svg className="sw-waves" width="220" height="30" viewBox="0 0 220 30" aria-hidden="true">
        <path d="M0 15 Q27.5 0 55 15 T110 15 T165 15 T220 15 T275 15 T330 15"
          fill="none" stroke="var(--red, #990000)" strokeWidth="4" strokeLinecap="round" />
      </svg>
      <div className="sw-paws" aria-hidden="true">
        {[0, 1, 2, 3, 4].map(n => <span key={n} style={{ animationDelay: `${n * 0.3}s` }}>🐾</span>)}
      </div>
      <div style={{ fontSize: inline ? 17 : 22, fontWeight: 800, color: 'var(--ink)', marginBottom: 6 }}>{label}</div>
      <div key={i} className="sw-tip" style={{ fontSize: 15, color: 'var(--ink-soft)' }}>{TIPS[i]}</div>
      {slow && <div style={{ fontSize: 13.5, color: 'var(--ink-faint)', marginTop: 12 }}>
        25Live can be slow sometimes. Thanks for hanging in there!</div>}
    </div>
  );
}
