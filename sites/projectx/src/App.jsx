import { useState, useEffect, useRef } from "react";

// ── Design tokens ─────────────────────────────────────────────
const C = {
  bg:      '#040C1C',
  panel:   '#060E1E',
  panel2:  '#070F22',
  border:  '#1A3254',
  blue:    '#4F9CF9',
  indigo:  '#8C8FFF',
  green:   '#0ED4A0',
  amber:   '#F5A623',
  red:     '#FF4D5A',
  cyan:    '#00E5FF',
  text:    '#A8C0DC',
  dim:     '#1E3050',
  bright:  '#E8F2FF',
  muted:   '#3A5570',
};

const MONO = "'SF Mono','Fira Code','Consolas','JetBrains Mono',monospace";

// ── Orbital layer config ──────────────────────────────────────
const LAYERS = [
  { id: 'SHIELD', r: 108, score: 100, color: C.green  },
  { id: 'QRF',    r: 80,  score: 87,  color: C.amber  },
  { id: 'TRACE',  r: 52,  score: 98,  color: C.blue   },
  { id: 'GUARD',  r: 26,  score: 92,  color: C.cyan   },
];

// SVG arc descriptor
function arc(cx, cy, r, startDeg, endDeg) {
  if (endDeg >= 360) endDeg = 359.9;
  const rad = d => (d - 90) * (Math.PI / 180);
  const x1 = cx + r * Math.cos(rad(startDeg));
  const y1 = cy + r * Math.sin(rad(startDeg));
  const x2 = cx + r * Math.cos(rad(endDeg));
  const y2 = cy + r * Math.sin(rad(endDeg));
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

// ── Trace log generator ───────────────────────────────────────
let uid = 9000;
const TOOLS   = ['axiom_guard', 'axiom_trace', 'axiom_qrf', 'axiom_shield'];
const DOMAINS = ['legal', 'medical', 'code', 'general', 'finance'];
const toolColor = t => ({ 'axiom_guard': C.cyan, 'axiom_trace': C.blue, 'axiom_qrf': C.indigo, 'axiom_shield': C.green }[t] || C.text);

function makeTrace() {
  uid++;
  const tool   = TOOLS[Math.floor(Math.random() * TOOLS.length)];
  const dist   = (Math.random() * 0.09).toFixed(3);
  const conf   = (82 + Math.random() * 17).toFixed(1);
  const level  = parseFloat(dist) > 0.065 ? 'WARN' : 'OK';
  const domain = DOMAINS[Math.floor(Math.random() * DOMAINS.length)];
  const now    = new Date();
  const ts     = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}.${String(now.getMilliseconds()).padStart(3,'0')}`;
  const hmac   = `${Math.random().toString(36).slice(2,6)}…${Math.random().toString(36).slice(2,6)}`;
  const msgs   = {
    'axiom_guard':  `REQ_${uid} | dist:${dist} | conf:${conf}% | ${level === 'OK' ? 'ALIGNED' : 'FLAGGED'}`,
    'axiom_trace':  `TRACE_${uid} | domain:${domain} | depth:${Math.floor(Math.random()*5)+2} | ${level === 'OK' ? 'PASS' : 'REVIEW'}`,
    'axiom_qrf':    `QRF_${uid} | branch:${Math.floor(Math.random()*5)+1} | p=${(0.55+Math.random()*0.44).toFixed(2)} | ${level === 'OK' ? 'PASS' : 'FLAG'}`,
    'axiom_shield': `SHIELD_${uid} | layer:${Math.floor(Math.random()*4)+1} | integ:${(96.5+Math.random()*3.4).toFixed(1)}% | ${level === 'OK' ? 'SECURE' : 'MONITOR'}`,
  };
  return { ts, tool, msg: msgs[tool], hmac, level };
}

const INIT_TRACES = [
  { ts:'14:31:52.001', tool:'axiom_guard',  msg:'REQ_8820 | dist:0.031 | conf:94.2% | ALIGNED',         hmac:'a3f9…c821', level:'OK'   },
  { ts:'14:31:54.341', tool:'axiom_qrf',    msg:'QRF_8821 | branch:3 | p=0.91 | PASS',                  hmac:'b77e…d410', level:'OK'   },
  { ts:'14:31:56.712', tool:'axiom_shield', msg:'SHIELD_8821 | layer:2 | integ:99.7% | SECURE',          hmac:'f22a…9081', level:'OK'   },
  { ts:'14:31:58.993', tool:'axiom_trace',  msg:'TRACE_8822 | domain:legal | depth:4 | PASS',            hmac:'3c1b…7a45', level:'OK'   },
  { ts:'14:32:01.144', tool:'axiom_guard',  msg:'REQ_8823 | dist:0.071 | conf:86.1% | FLAGGED',         hmac:'e99d…3b12', level:'WARN' },
  { ts:'14:32:03.556', tool:'axiom_qrf',    msg:'QRF_8823 | branch:2 | p=0.62 | FLAG',                  hmac:'cc4a…f019', level:'WARN' },
  { ts:'14:32:05.877', tool:'axiom_shield', msg:'SHIELD_8823 | layer:1 | integ:97.8% | SECURE',          hmac:'91bc…2e44', level:'OK'   },
];

// ── QRF tree data ─────────────────────────────────────────────
const QRF_NODES = [
  { id:0, p:1.00, x:50, y:8  },
  { id:1, p:0.91, x:20, y:35 },
  { id:2, p:0.62, x:50, y:35 },
  { id:3, p:0.78, x:80, y:35 },
  { id:4, p:0.95, x:10, y:63 },
  { id:5, p:0.38, x:32, y:63 },
  { id:6, p:0.62, x:50, y:63 },
  { id:7, p:0.78, x:68, y:63 },
  { id:8, p:0.82, x:88, y:63 },
];
const QRF_EDGES = [[0,1],[0,2],[0,3],[1,4],[1,5],[2,6],[3,7],[3,8]];
const nodeColor = p => p >= 0.8 ? C.green : p >= 0.55 ? C.amber : C.red;

// ── Components ────────────────────────────────────────────────

function TopBar({ cdist }) {
  const ok = cdist < 0.05;
  return (
    <div style={{
      display:'flex', alignItems:'center', justifyContent:'space-between',
      padding:'0 18px', height:46,
      background: C.panel2, borderBottom:`1px solid ${C.border}`,
      fontFamily:MONO, flexShrink:0,
    }}>
      {/* Brand */}
      <div style={{ display:'flex', alignItems:'center', gap:10 }}>
        {/* Flame icon */}
        <svg width="14" height="18" viewBox="0 0 14 18" fill="none">
          <path d="M7 0 C7 0 11 5 11 9 C11 12 9.5 14 7 14 C4.5 14 3 12 3 9 C3 7 4 5.5 5 4 C5 6 6 7 7 7 C7 7 8 5 7 0Z" fill={C.blue} opacity="0.9"/>
          <path d="M7 8 C7 8 9 10 9 12 C9 13.5 8 15 7 15 C6 15 5 13.5 5 12 C5 10 6 9 7 8Z" fill={C.cyan} opacity="0.85"/>
        </svg>
        <span style={{ color:C.blue,   fontSize:12, fontWeight:700, letterSpacing:'0.12em' }}>AXIOM</span>
        <span style={{ color:C.muted,  fontSize:10 }}>//</span>
        <span style={{ color:C.bright, fontSize:10, letterSpacing:'0.18em' }}>ORIVAEL</span>
        <div style={{
          background:'#0A1830', border:`1px solid ${C.indigo}55`,
          color:C.indigo, fontSize:8, padding:'2px 8px',
          borderRadius:2, letterSpacing:'0.12em',
        }}>HELLO OPERATOR</div>
        <div style={{
          background:'#0A1830', border:`1px solid ${C.muted}44`,
          color:C.muted, fontSize:8, padding:'2px 8px',
          borderRadius:2, letterSpacing:'0.1em',
        }}>ENTERPRISE</div>
      </div>

      {/* System status dots */}
      <div style={{ display:'flex', gap:18, alignItems:'center' }}>
        {[['GUARD',C.cyan],['TRACE',C.blue],['QRF',C.amber],['SHIELD',C.green]].map(([s,col]) => (
          <div key={s} style={{ display:'flex', alignItems:'center', gap:5 }}>
            <div style={{
              width:5, height:5, borderRadius:'50%',
              background:col, boxShadow:`0 0 6px ${col}`,
            }}/>
            <span style={{ fontSize:8, color:C.muted, letterSpacing:'0.1em' }}>{s}</span>
          </div>
        ))}
      </div>

      {/* Status */}
      <div style={{ display:'flex', alignItems:'center', gap:10 }}>
        <span style={{ fontSize:8, color:C.muted }}>CONST_DIST</span>
        <span style={{ fontSize:11, color: ok?C.green:C.amber, fontWeight:700, fontVariantNumeric:'tabular-nums' }}>
          {cdist.toFixed(5)}
        </span>
        <div style={{
          background: ok?'#081A10':'#1C1200',
          border:`1px solid ${ok?C.green:C.amber}`,
          color: ok?C.green:C.amber,
          fontSize:8, padding:'3px 10px', borderRadius:2,
          letterSpacing:'0.12em', fontWeight:700,
        }}>{ok ? '● ALIGNED' : '● FLAGGING'}</div>
      </div>
    </div>
  );
}

function TraceLog({ traces }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [traces]);

  return (
    <div style={{
      display:'flex', flexDirection:'column', overflow:'hidden',
      background:C.panel, border:`1px solid ${C.border}`, borderRadius:3,
      fontFamily:MONO,
    }}>
      <div style={{
        padding:'7px 11px', borderBottom:`1px solid ${C.border}`,
        display:'flex', alignItems:'center', gap:7, flexShrink:0,
      }}>
        <span style={{ color:C.blue, fontSize:8, letterSpacing:'0.15em' }}>AXIOM_TRACE</span>
        <span style={{
          fontSize:7, color:C.green, background:'#081A10',
          border:`1px solid ${C.green}44`, padding:'1px 5px', borderRadius:2,
        }}>LIVE</span>
        <div style={{
          marginLeft:'auto', width:5, height:5, borderRadius:'50%',
          background:C.green, boxShadow:`0 0 7px ${C.green}`,
        }}/>
      </div>

      <div ref={ref} style={{ flex:1, overflowY:'auto', padding:'6px 0' }}>
        {traces.map((tr, i) => (
          <div key={i} style={{
            padding:'4px 11px',
            borderLeft:`2px solid ${tr.level==='WARN' ? C.amber : 'transparent'}`,
            background: tr.level==='WARN' ? '#0F0900' : 'transparent',
            opacity: i < traces.length - 10 ? 0.45 : 1,
            transition:'opacity 0.3s',
          }}>
            <div style={{ display:'flex', alignItems:'center', gap:5, marginBottom:1 }}>
              <span style={{ color:C.muted, fontSize:7.5 }}>{tr.ts}</span>
              <span style={{ color:toolColor(tr.tool), fontSize:7.5, fontWeight:600 }}>{tr.tool}</span>
              <span style={{
                marginLeft:'auto', fontSize:7,
                color: tr.level==='WARN' ? C.amber : C.muted,
              }}>{tr.level}</span>
            </div>
            <div style={{ fontSize:7.5, color:C.text, lineHeight:1.4 }}>{tr.msg}</div>
            <div style={{ fontSize:6.5, color:C.dim, marginTop:1 }}>hmac:{tr.hmac}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function OrbitalGraph({ sweep, cdist }) {
  const CX = 130, CY = 130;
  const sweepRad = (sweep - 90) * Math.PI / 180;

  // Sweep trail: a wedge going back 60 degrees
  const trailPoints = [];
  for (let i = 0; i <= 20; i++) {
    const a = ((sweep - 60 + i * 3) - 90) * Math.PI / 180;
    const r = 118;
    trailPoints.push(`${(CX + r * Math.cos(a)).toFixed(1)},${(CY + r * Math.sin(a)).toFixed(1)}`);
  }
  const trailPath = `M${CX},${CY} L${trailPoints[0]} ${trailPoints.map((p,i) => i>0?`L${p}`:'').join(' ')} Z`;

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:8, overflow:'hidden' }}>
      {/* Header */}
      <div style={{
        padding:'7px 12px', background:C.panel, border:`1px solid ${C.border}`,
        borderRadius:3, display:'flex', alignItems:'center', gap:8, fontFamily:MONO, flexShrink:0,
      }}>
        <span style={{ fontSize:8, color:C.indigo, letterSpacing:'0.15em' }}>COMPLIANCE ORBITAL</span>
        <span style={{ fontSize:7, color:C.muted }}>REAL-TIME CONSTITUTIONAL MONITORING</span>
        <span style={{ marginLeft:'auto', fontSize:7, color:C.muted }}>
          SWEEP {sweep.toFixed(0).padStart(3,'0')}°
        </span>
      </div>

      {/* SVG orbital */}
      <div style={{
        background:C.panel, border:`1px solid ${C.border}`, borderRadius:3,
        display:'flex', alignItems:'center', justifyContent:'center',
        flex:1, padding:12, overflow:'hidden',
      }}>
        <svg viewBox="0 0 260 260" style={{ width:'100%', maxWidth:300, maxHeight:300 }}>
          <defs>
            <radialGradient id="ambientGlow" cx="50%" cy="50%">
              <stop offset="0%"   stopColor="#1A3A6B" stopOpacity="0.3"/>
              <stop offset="100%" stopColor="#040C1C" stopOpacity="0"/>
            </radialGradient>
            <radialGradient id="centerGlow" cx="50%" cy="50%">
              <stop offset="0%"   stopColor={C.blue} stopOpacity="0.25"/>
              <stop offset="100%" stopColor="#040C1C" stopOpacity="0"/>
            </radialGradient>
          </defs>

          {/* Ambient glow */}
          <circle cx={CX} cy={CY} r={120} fill="url(#ambientGlow)"/>

          {/* Grid spokes */}
          {[0,30,60,90,120,150,180,210,240,270,300,330].map(deg => {
            const r = (deg - 90) * Math.PI / 180;
            return (
              <line key={deg}
                x1={CX} y1={CY}
                x2={CX + 118 * Math.cos(r)}
                y2={CY + 118 * Math.sin(r)}
                stroke={C.dim} strokeWidth={0.4} opacity={0.5}
              />
            );
          })}

          {/* Track rings */}
          {LAYERS.map(l => (
            <circle key={l.id} cx={CX} cy={CY} r={l.r}
              fill="none" stroke={C.dim} strokeWidth={0.5} opacity={0.7}/>
          ))}

          {/* Sweep trail */}
          <path d={trailPath} fill={C.blue} opacity={0.04}/>

          {/* Compliance arcs */}
          {LAYERS.map(l => (
            <path key={l.id}
              d={arc(CX, CY, l.r, 0, l.score / 100 * 360)}
              fill="none" stroke={l.color} strokeWidth={2.5} strokeLinecap="round"
              style={{ filter:`drop-shadow(0 0 5px ${l.color}88)` }}
            />
          ))}

          {/* Sweep glow dots on each ring */}
          {LAYERS.map(l => {
            const x = CX + l.r * Math.cos(sweepRad);
            const y = CY + l.r * Math.sin(sweepRad);
            return (
              <circle key={l.id} cx={x} cy={y} r={3.5}
                fill={l.color} opacity={0.95}
                style={{ filter:`drop-shadow(0 0 6px ${l.color})` }}
              />
            );
          })}

          {/* Sweep line */}
          <line
            x1={CX} y1={CY}
            x2={CX + 118 * Math.cos(sweepRad)}
            y2={CY + 118 * Math.sin(sweepRad)}
            stroke={C.blue} strokeWidth={1.2} opacity={0.75}
          />

          {/* Layer ID labels */}
          {LAYERS.map(l => (
            <text key={l.id}
              x={CX + l.r + 6} y={CY - 5}
              fill={l.color} fontSize={5.5} fontFamily={MONO} opacity={0.75}
            >{l.id}</text>
          ))}

          {/* Score labels */}
          {LAYERS.map(l => (
            <text key={l.id}
              x={CX - l.r - 6} y={CY - 5}
              textAnchor="end"
              fill={l.color} fontSize={5.5} fontFamily={MONO} opacity={0.65}
            >{l.score}%</text>
          ))}

          {/* Center core */}
          <circle cx={CX} cy={CY} r={20} fill="url(#centerGlow)"/>
          <circle cx={CX} cy={CY} r={15}
            fill={C.panel} stroke={C.blue} strokeWidth={1.5}
            style={{ filter:`drop-shadow(0 0 10px ${C.blue}55)` }}
          />
          <text x={CX} y={CY-3} textAnchor="middle"
            fill={C.bright} fontSize={6} fontFamily={MONO} fontWeight="bold" letterSpacing="1">AXIOM</text>
          <text x={CX} y={CY+6} textAnchor="middle"
            fill={C.blue} fontSize={4.5} fontFamily={MONO}>ONLINE</text>
        </svg>
      </div>

      {/* Layer metric cards */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:6, flexShrink:0 }}>
        {LAYERS.map(l => (
          <div key={l.id} style={{
            background:C.panel, border:`1px solid ${C.border}`,
            borderTop:`2px solid ${l.color}`,
            borderRadius:3, padding:'8px 10px', textAlign:'center',
            fontFamily:MONO,
          }}>
            <div style={{ fontSize:6.5, color:C.muted, letterSpacing:'0.12em', marginBottom:4 }}>{l.id}</div>
            <div style={{ fontSize:16, color:l.color, fontWeight:700, lineHeight:1 }}>{l.score}</div>
            <div style={{ fontSize:5.5, color:C.muted, marginTop:2 }}>%</div>
            <div style={{
              fontSize:6, marginTop:5,
              color: l.id==='QRF' ? C.amber : C.green,
              background: l.id==='QRF' ? '#1A0E00' : '#061410',
              padding:'2px 4px', borderRadius:2,
            }}>
              {l.id === 'QRF' ? 'CALIBRATING' : 'NOMINAL'}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function QRFTree() {
  return (
    <div style={{
      background:C.panel, border:`1px solid ${C.border}`, borderRadius:3,
      padding:'9px 11px', fontFamily:MONO,
    }}>
      <div style={{
        fontSize:8, color:C.indigo, letterSpacing:'0.15em', marginBottom:8,
        borderBottom:`1px solid ${C.border}`, paddingBottom:6,
        display:'flex', alignItems:'center', gap:8,
      }}>
        QRF BRANCH VISUALIZER
        <span style={{ marginLeft:'auto', fontSize:7, color:C.muted }}>p-weighted</span>
      </div>

      <svg viewBox="0 0 100 72" style={{ width:'100%' }}>
        {QRF_EDGES.map(([a, b]) => {
          const na = QRF_NODES[a], nb = QRF_NODES[b];
          return (
            <line key={`${a}-${b}`}
              x1={na.x} y1={na.y} x2={nb.x} y2={nb.y}
              stroke={C.dim} strokeWidth={0.6}
            />
          );
        })}
        {QRF_NODES.map(n => (
          <g key={n.id}>
            <circle cx={n.x} cy={n.y} r={4}
              fill={C.panel2} stroke={nodeColor(n.p)} strokeWidth={1.2}
              style={{ filter:`drop-shadow(0 0 3px ${nodeColor(n.p)}88)` }}
            />
            <text x={n.x} y={n.y+9}
              textAnchor="middle" fill={nodeColor(n.p)}
              fontSize={3.8} fontFamily={MONO}
            >{n.p.toFixed(2)}</text>
          </g>
        ))}
      </svg>

      <div style={{ display:'flex', gap:10, marginTop:5 }}>
        {[[C.green,'≥0.80'],[C.amber,'≥0.55'],[C.red,'<0.55']].map(([c,l]) => (
          <div key={l} style={{ display:'flex', alignItems:'center', gap:4 }}>
            <div style={{ width:5, height:5, borderRadius:'50%', background:c }}/>
            <span style={{ fontSize:6.5, color:C.muted }}>{l}</span>
          </div>
        ))}
        <span style={{ marginLeft:'auto', fontSize:6.5, color:C.muted }}>8 branches active</span>
      </div>
    </div>
  );
}

function ShieldBars() {
  const layers = [
    { id:'L1 // INPUT FILTER',    val:99.9, color:C.green },
    { id:'L2 // CONST VALIDATOR', val:97.3, color:C.green },
    { id:'L3 // OUTPUT SCREEN',   val:94.1, color:C.amber },
    { id:'L4 // HMAC VERIFY',     val:100,  color:C.green },
  ];
  return (
    <div style={{
      background:C.panel, border:`1px solid ${C.border}`, borderRadius:3,
      padding:'9px 11px', fontFamily:MONO, flex:1,
    }}>
      <div style={{
        fontSize:8, color:C.green, letterSpacing:'0.15em', marginBottom:10,
        borderBottom:`1px solid ${C.border}`, paddingBottom:6,
      }}>SHIELD INTEGRITY</div>
      {layers.map(l => (
        <div key={l.id} style={{ marginBottom:9 }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:3 }}>
            <span style={{ fontSize:7, color:C.muted }}>{l.id}</span>
            <span style={{ fontSize:7, color:l.color, fontVariantNumeric:'tabular-nums' }}>{l.val}%</span>
          </div>
          <div style={{ height:3, background:C.dim, borderRadius:2, overflow:'hidden' }}>
            <div style={{
              width:`${l.val}%`, height:'100%', background:l.color, borderRadius:2,
              boxShadow:`0 0 6px ${l.color}`,
            }}/>
          </div>
        </div>
      ))}
    </div>
  );
}

function BottomBar({ cdist }) {
  const ok = cdist < 0.05;
  const pct = Math.min(cdist / 0.1 * 100, 100);
  return (
    <div style={{
      height:38, display:'flex', alignItems:'center',
      padding:'0 18px', gap:16,
      background:C.panel2, borderTop:`1px solid ${C.border}`,
      fontFamily:MONO, flexShrink:0,
    }}>
      <span style={{ fontSize:7.5, color:C.muted, letterSpacing:'0.1em' }}>CONSTITUTIONAL DISTANCE</span>
      <span style={{
        fontSize:12, color:ok?C.green:C.amber, fontWeight:700,
        fontVariantNumeric:'tabular-nums',
      }}>{cdist.toFixed(5)}</span>
      <div style={{ width:180, height:3, background:C.dim, borderRadius:2 }}>
        <div style={{
          width:`${pct}%`, height:'100%',
          background:`linear-gradient(90deg, ${C.green}, ${ok?C.green:C.amber})`,
          borderRadius:2, boxShadow:`0 0 6px ${ok?C.green:C.amber}`,
          transition:'width 0.6s ease',
        }}/>
      </div>
      <span style={{ fontSize:7.5, color:C.muted }}>THRESHOLD 0.050</span>
      <span style={{ fontSize:7.5, color:ok?C.green:C.amber, letterSpacing:'0.08em' }}>
        {ok ? '✓ WITHIN BOUNDS' : '⚠ APPROACHING LIMIT'}
      </span>
      <span style={{ marginLeft:'auto', fontSize:7.5, color:C.muted }}>SESSION UPTIME</span>
      <span style={{ fontSize:7.5, color:C.text }}>00:31:52</span>
      <span style={{ fontSize:7.5, color:C.muted }}>|</span>
      <span style={{ fontSize:7.5, color:C.muted }}>v2.4.1-rc</span>
      <span style={{ fontSize:7.5, color:C.muted }}>|</span>
      <span style={{ fontSize:7.5, color:C.indigo }}>ORIVAEL © 2026</span>
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────
export default function AxiomDashboard() {
  const [sweep,  setSweep]  = useState(0);
  const [traces, setTraces] = useState(INIT_TRACES);
  const [cdist,  setCdist]  = useState(0.03127);

  // Radar sweep
  useEffect(() => {
    const id = setInterval(() => setSweep(s => (s + 1.5) % 360), 30);
    return () => clearInterval(id);
  }, []);

  // Live trace feed + cdist jitter
  useEffect(() => {
    const id = setInterval(() => {
      setTraces(prev => [...prev.slice(-22), makeTrace()]);
      setCdist(d => Math.max(0.001, Math.min(0.088, d + (Math.random() - 0.48) * 0.0018)));
    }, 2100);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{
      background:C.bg, height:'100vh', display:'flex', flexDirection:'column',
      color:C.text, overflow:'hidden',
      backgroundImage:'radial-gradient(ellipse at 50% 0%, #0D2040 0%, transparent 60%)',
    }}>
      <TopBar cdist={cdist}/>

      <div style={{
        flex:1, display:'grid',
        gridTemplateColumns:'258px 1fr 238px',
        gap:8, padding:8, minHeight:0, overflow:'hidden',
      }}>
        {/* Left: trace log */}
        <TraceLog traces={traces}/>

        {/* Center: orbital + metrics */}
        <OrbitalGraph sweep={sweep} cdist={cdist}/>

        {/* Right: QRF tree + shield */}
        <div style={{ display:'flex', flexDirection:'column', gap:8, overflow:'hidden' }}>
          <QRFTree/>
          <ShieldBars/>
        </div>
      </div>

      <BottomBar cdist={cdist}/>
    </div>
  );
}
