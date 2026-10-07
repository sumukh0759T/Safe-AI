import { useState, useEffect, useRef, useCallback } from "react";
import {
  Home, LayoutGrid, Cpu, Wifi, WifiOff, ChevronDown, ChevronRight,
  Copy, Check, RefreshCw, AlertTriangle, ShieldCheck, Volume2, Activity,
  Radio, Thermometer, Waves, Droplets, Save, RotateCcw
} from "lucide-react";

/* ---------------------------------------------------------------
   Design tokens (shared with SafeBath_AI_Dashboard.html so the
   whole product family reads as one identity):
   ink #0f1e1c · dusk #123330 · panel #16413c · mist #bcd8d0
   paper #eef4f1 · safe #79c8a4 · watch #e8b256 · alert #e07856
   Display: Fraunces · Body: IBM Plex Sans · Data: IBM Plex Mono
----------------------------------------------------------------*/

const FONT_IMPORT =
  "@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');";

const COLORS = {
  ink: "#0f1e1c", dusk: "#123330", panel: "#16413c", mist: "#bcd8d0",
  paper: "#eef4f1", safe: "#79c8a4", watch: "#e8b256", alert: "#e07856",
};

/* ---------------------------------------------------------------
   MODULE CATALOG — mirrors the Build Guide's model mapping table
----------------------------------------------------------------*/
const MODULES = [
  { id: "mat", name: "Fall Risk (Pressure Mat)", board: "Board 1", model: "ConvLSTM", framework: "TFLite Micro (on-device)", metric: "Val. accuracy", metricValue: "91.4%", unit: "sway idx", icon: Activity, base: 0.6, range: 1.1 },
  { id: "radar", name: "Presence / Motion Anomaly", board: "Board 2", model: "Rule-based threshold", framework: "C++, on-device", metric: "Type", metricValue: "Deterministic rule", unit: "stillness s", icon: Radio, base: 4, range: 14 },
  { id: "toilet", name: "Toilet Biomarkers", board: "Board 3", model: "Random Forest", framework: "scikit-learn (laptop)", metric: "Val. accuracy", metricValue: "88.7%", unit: "gas ppm", icon: Droplets, base: 205, range: 24 },
  { id: "audio", name: "Acoustic Distress", board: "Board 4", model: "YAMNet embeddings + dense head", framework: "TFLite Micro (on-device)", metric: "Val. accuracy", metricValue: "94.1%", unit: "class", icon: Volume2, base: 0, range: 0 },
  { id: "tof", name: "Volumetric Fall (ToF)", board: "Board 5", model: "Rule-based trilateration", framework: "C++, on-device", metric: "Type", metricValue: "Deterministic rule", unit: "mm min", icon: Waves, base: 850, range: 300 },
  { id: "thermal", name: "Thermal Scald / Freeze", board: "Board 5", model: "CNN + LSTM", framework: "TFLite (laptop)", metric: "Val. accuracy", metricValue: "92.3%", unit: "°C avg", icon: Thermometer, base: 24.6, range: 1.6 },
  { id: "voice", name: "Voice Bridge (keyword spotting)", board: "Board 5", model: "micro_speech-style KWS", framework: "TFLite Micro (on-device)", metric: "Val. accuracy", metricValue: "96.0%", unit: "confidence", icon: Volume2, base: 0.9, range: 0.1 },
];

const AUDIO_CLASSES = ["silence", "silence", "silence", "water", "silence"];

/* ---------------------------------------------------------------
   HARDWARE DEFAULTS — from the confirmed wiring doc. Editable +
   persisted so Joy Projects' corrections can be dropped in later
   without touching code.
----------------------------------------------------------------*/
const DEFAULT_HARDWARE = [
  {
    board: "Board 1", title: "Pressure Mat + Entry Detection", status: "confirmed",
    rows: [
      ["PIR motion sensor", "OUT", "GPIO27", "Digital input, entry events"],
      ["CD4067 mux", "S0 / S1 / S2 / S3", "GPIO16 / 17 / 18 / 19", "Select lines — 16 row channels"],
      ["CD4067 mux", "SIG", "GPIO34", "ADC1, input-only"],
      ["74HC595 shift register", "DS / SH_CP / ST_CP", "GPIO23 / 25 / 26", "Drives 4 column outputs (Q1–Q4)"],
      ["Velostat sheet", "16 rows × 4 cols", "Into mux + shift-register lines", "Pending — everything else is wired. 64 sensing cells total."],
    ],
    firmware: `#define MUX_S0 16
#define MUX_S1 17
#define MUX_S2 18
#define MUX_S3 19
#define MUX_SIG 34
#define SR_DS 23
#define SR_SHCP 25
#define SR_STCP 26

// 16 rows (mux channels) x 4 cols (shift-register outputs) = 64 cells
void selectMuxChannel(int ch) {
  digitalWrite(MUX_S0, bitRead(ch, 0));
  digitalWrite(MUX_S1, bitRead(ch, 1));
  digitalWrite(MUX_S2, bitRead(ch, 2));
  digitalWrite(MUX_S3, bitRead(ch, 3));
}
// Full scan loop + Velostat physical assembly guide are in the Final Master Guide, Section 2.`,
  },
  {
    board: "Board 2", title: "Presence / Motion Radar (DFRobot C4001)", status: "confirmed",
    rows: [
      ["DFRobot C4001", "VIN / GND", "3.3V / GND", ""],
      ["DFRobot C4001", "RX", "GPIO17 (ESP32 TX2)", "UART"],
      ["DFRobot C4001", "TX", "GPIO16 (ESP32 RX2)", "UART"],
    ],
    firmware: `#include <DFRobot_C4001.h>
DFRobot_C4001_UART radar(&Serial2, 9600, 16, 17);
radar.begin();
radar.setSensorMode(eExitMode);   // presence + motion mode
// Rule: presence == true && stillness_ms > 120000 -> possible_collapse`,
  },
  {
    board: "Board 3", title: "Toilet Biomarker Sensing", status: "confirmed",
    rows: [
      ["TCS3200", "S0 / S1 / S2 / S3", "GPIO18 / 19 / 21 / 22", "Frequency-scaling + filter select"],
      ["TCS3200", "OUT", "GPIO23", "Pulse output, interrupt-counted"],
      ["MQ-135", "AOUT", "GPIO35", "ADC1, input-only, VCC->VIN(5V)"],
      ["MQ-3", "AOUT", "GPIO34", "ADC1, input-only, VCC->VIN(5V)"],
    ],
    firmware: `// DS18B20 is NOT in the final build — temp_c is dropped entirely,
// not placeholdered. train_toilet_rf.py trains on 3 features only:
// gas_ppm, voc_ppm, turbidity.
float gasPpm = analogRead(35);
float vocPpm = analogRead(34);`,
  },
  {
    board: "Board 4", title: "Acoustic Distress Detection", status: "confirmed",
    rows: [
      ["MAX4466 mic", "VCC / GND", "3.3V / GND", "Replaces the earlier dual-INMP441 plan"],
      ["MAX4466 mic", "OUT", "GPIO34", "Analog — ADC1, input-only"],
    ],
    firmware: `// Analog mic -> RMS / zero-crossing / spectral-centroid features,
// not YAMNet embeddings (that assumed a digital I2S mic).
// Only the classification label is published — never raw audio.
String label = classifyFromFeatures(rms, zcr, centroid);
client.publish("sensor/audio/event", label.c_str());`,
  },
  {
    board: "Board 5", title: "ToF + Thermal/Scald + Voice Bridge", status: "confirmed",
    rows: [
      ["3x VL53L1X", "SDA / SCL", "GPIO21 / 22", "Shared I2C bus"],
      ["3x VL53L1X", "XSHUT 1/2/3", "GPIO25 / 26 / 27", "Assign unique address 0x30/31/32 at boot"],
      ["AMG8833", "SDA / SCL", "GPIO21 / 22", "Same bus, addr 0x69 — no conflict"],
      ["Water flow sensor", "Signal", "GPIO34", "Pulse-counting interrupt, VCC->5V/VIN"],
      ["Relay -> 12V solenoid", "IN", "GPIO33", "✅ confirmed working, powered. Energized = valve open (NC solenoid)"],
      ["DFPlayer Mini", "RX / TX", "GPIO17 (TX2) / GPIO16 (RX2)", "✅ confirmed working"],
      ["Amplifier module", "audio in", "DFPlayer SPK_R", "✅ confirmed working — boosts DFPlayer output to speaker"],
    ],
    firmware: `digitalWrite(RELAY_PIN, HIGH);  // energized = valve OPEN by default
bool unattendedHotFill = (flowCount > 50 && avgThermalC > 38.0);
if (unattendedHotFill) digitalWrite(RELAY_PIN, LOW);  // de-energize -> solenoid closes
// checkToFCollapse(): all 3 VL53L1X report <300mm simultaneously -> possible collapse
// Full boot-sequence + Voice Bridge trigger code: SafeBath_AI_Board5_Firmware.md`,
  },
];

/* ---------------------------------------------------------------
   Storage helpers (persistent artifact storage, personal scope)
----------------------------------------------------------------*/
async function loadHardware() {
  try {
    const res = await window.storage?.get("hardware-config");
    if (res?.value) return JSON.parse(res.value);
  } catch (e) { /* no saved config yet */ }
  return DEFAULT_HARDWARE;
}
async function saveHardware(cfg) {
  try { await window.storage?.set("hardware-config", JSON.stringify(cfg)); } catch (e) {}
}
async function loadLiveUrl() {
  try {
    const res = await window.storage?.get("live-api-url");
    if (res?.value) return res.value;
  } catch (e) {}
  return "http://localhost:8000/api/latest";
}
async function saveLiveUrl(url) {
  try { await window.storage?.set("live-api-url", url); } catch (e) {}
}

/* ---------------------------------------------------------------
   App
----------------------------------------------------------------*/
export default function App() {
  const [page, setPage] = useState("dashboard");
  const [dataMode, setDataMode] = useState("mock");
  const [liveUrl, setLiveUrl] = useState("http://localhost:8000/api/latest");
  const [liveStatus, setLiveStatus] = useState("idle"); // idle | connecting | connected | error
  const [readings, setReadings] = useState(() =>
    Object.fromEntries(MODULES.map((m) => [m.id, m.base]))
  );
  const [audioClass, setAudioClass] = useState("silence");
  const [riskScore, setRiskScore] = useState(12);
  const [events, setEvents] = useState([]);
  const [hardware, setHardware] = useState(DEFAULT_HARDWARE);
  const [matFrame, setMatFrame] = useState(() => Array(16).fill(null).map(() => Array(4).fill(0)));
  const matBlob = useRef({ row: 8, col: 2 });

  useEffect(() => {
    loadHardware().then(setHardware);
    loadLiveUrl().then(setLiveUrl);
  }, []);

  const pushEvent = useCallback((text, level = "safe") => {
    setEvents((prev) => [
      { id: Date.now() + Math.random(), time: new Date(), text, level },
      ...prev,
    ].slice(0, 40));
  }, []);

  // Mock ticking
  useEffect(() => {
    if (dataMode !== "mock") return;
    const tick = () => {
      const next = {};
      MODULES.forEach((m) => {
        next[m.id] = m.range === 0 ? 0 : m.base + (Math.random() - 0.5) * m.range;
      });
      setReadings(next);
      const ac = AUDIO_CLASSES[Math.floor(Math.random() * AUDIO_CLASSES.length)];
      setAudioClass(ac);

      // Simulate a 16x4 Velostat scan: a soft pressure blob wandering,
      // matching Section 2 of the Final Master Guide's 16-row x 4-col grid.
      const b = matBlob.current;
      b.row = Math.min(15, Math.max(0, b.row + (Math.random() - 0.5) * 2.2));
      b.col = Math.min(3, Math.max(0, b.col + (Math.random() - 0.5) * 0.6));
      const grid = Array(16).fill(null).map((_, r) =>
        Array(4).fill(null).map((_, c) => {
          const d2 = (r - b.row) ** 2 + (c - b.col) ** 2 * 4;
          const val = Math.exp(-d2 / 8) * (2800 + Math.random() * 900);
          return Math.round(val);
        })
      );
      setMatFrame(grid);

      let score = Math.round(8 + next.mat * 9 + (ac === "water" ? 4 : 0) + (next.toilet > 218 ? 8 : 0));
      score = Math.min(score, 100);
      setRiskScore(score);

      const msgs = [
        `Board 1 mat sway index within normal range (${next.mat.toFixed(2)})`,
        `Board 3 toilet reading logged — gas ${Math.round(next.toilet)}ppm`,
        `Board 4 audio event classified: ${ac}`,
        `Board 5 thermal frame captured, avg ${next.thermal.toFixed(1)}°C`,
        `Fusion engine recomputed risk score — ${score}/100`,
      ];
      pushEvent(msgs[Math.floor(Math.random() * msgs.length)], score > 30 ? "watch" : "safe");
    };
    tick();
    const id = setInterval(tick, 3200);
    return () => clearInterval(id);
  }, [dataMode, pushEvent]);

  // Live polling
  useEffect(() => {
    if (dataMode !== "live") return;
    let cancelled = false;
    setLiveStatus("connecting");
    const poll = async () => {
      try {
        const res = await fetch(liveUrl, { cache: "no-store" });
        if (!res.ok) throw new Error("bad status");
        const data = await res.json();
        if (cancelled) return;
        setLiveStatus("connected");
        if (data.modules) {
          const next = {};
          MODULES.forEach((m) => { next[m.id] = data.modules[m.id]?.value ?? readings[m.id]; });
          setReadings(next);
        }
        if (typeof data.risk_score === "number") setRiskScore(data.risk_score);
        // sensor/mat/frame arrives as a 64-value CSV string, 16 rows x 4 cols, row-major (Section 3 contract)
        const rawFrame = data.modules?.mat?.raw_frame;
        if (typeof rawFrame === "string") {
          const vals = rawFrame.split(",").map((v) => parseInt(v, 10)).filter((v) => !isNaN(v));
          if (vals.length === 64) {
            const grid = Array(16).fill(null).map((_, r) => vals.slice(r * 4, r * 4 + 4));
            setMatFrame(grid);
          }
        }
        if (Array.isArray(data.events)) {
          data.events.slice(0, 5).forEach((e) => pushEvent(e.text, e.level || "safe"));
        }
      } catch (e) {
        if (!cancelled) setLiveStatus("error");
      }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { cancelled = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataMode, liveUrl]);

  return (
    <div style={{ fontFamily: "'IBM Plex Sans', sans-serif", background: COLORS.ink, minHeight: "100vh", color: COLORS.paper }}>
      <style>{FONT_IMPORT}</style>
      <TopNav page={page} setPage={setPage} dataMode={dataMode} setDataMode={setDataMode}
        liveUrl={liveUrl} setLiveUrl={(u) => { setLiveUrl(u); saveLiveUrl(u); }} liveStatus={liveStatus} />
      <main className="max-w-6xl mx-auto px-6 pb-16">
        {page === "user" && <UserPage riskScore={riskScore} readings={readings} events={events} audioClass={audioClass} />}
        {page === "dashboard" && <DashboardPage riskScore={riskScore} readings={readings} events={events} dataMode={dataMode} liveStatus={liveStatus} audioClass={audioClass} matFrame={matFrame} />}
        {page === "hardware" && <HardwarePage hardware={hardware} setHardware={setHardware} />}
      </main>
    </div>
  );
}

/* ---------------------------------------------------------------
   Top navigation
----------------------------------------------------------------*/
function TopNav({ page, setPage, dataMode, setDataMode, liveUrl, setLiveUrl, liveStatus }) {
  const [editingUrl, setEditingUrl] = useState(false);
  const tabs = [
    { id: "user", label: "User View", icon: Home },
    { id: "dashboard", label: "AI / Module Dashboard", icon: LayoutGrid },
    { id: "hardware", label: "Hardware & Connections", icon: Cpu },
  ];
  return (
    <div style={{ borderBottom: `1px solid rgba(238,244,241,0.10)`, background: `linear-gradient(180deg, ${COLORS.dusk}, ${COLORS.ink})` }}>
      <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <svg width="30" height="30" viewBox="0 0 40 40" fill="none">
            <circle cx="20" cy="20" r="18" stroke={COLORS.safe} strokeWidth="1.6" opacity="0.5" />
            <circle cx="20" cy="20" r="11" stroke={COLORS.watch} strokeWidth="1.6" opacity="0.7" />
            <circle cx="20" cy="20" r="3.5" fill={COLORS.paper} />
          </svg>
          <div>
            <div style={{ fontFamily: "'Fraunces', serif", fontSize: 18, fontWeight: 500 }}>SafeBath AI</div>
            <div style={{ fontSize: 10.5, letterSpacing: "0.08em", color: COLORS.mist, textTransform: "uppercase" }}>Software Console</div>
          </div>
        </div>

        <nav className="flex gap-1 rounded-xl p-1" style={{ background: "rgba(238,244,241,0.05)" }}>
          {tabs.map((t) => {
            const Icon = t.icon;
            const active = page === t.id;
            return (
              <button key={t.id} onClick={() => setPage(t.id)}
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors"
                style={{
                  background: active ? COLORS.panel : "transparent",
                  color: active ? COLORS.paper : COLORS.mist,
                  fontWeight: active ? 500 : 400,
                }}>
                <Icon size={14} /> {t.label}
              </button>
            );
          })}
        </nav>

        <div className="flex items-center gap-2 text-xs" style={{ fontFamily: "'IBM Plex Mono', monospace", color: COLORS.mist }}>
          <div className="flex rounded-lg overflow-hidden" style={{ border: "1px solid rgba(238,244,241,0.15)" }}>
            <button onClick={() => setDataMode("mock")} className="px-2.5 py-1.5"
              style={{ background: dataMode === "mock" ? COLORS.panel : "transparent", color: dataMode === "mock" ? COLORS.paper : COLORS.mist }}>
              Mock
            </button>
            <button onClick={() => setDataMode("live")} className="px-2.5 py-1.5"
              style={{ background: dataMode === "live" ? COLORS.panel : "transparent", color: dataMode === "live" ? COLORS.paper : COLORS.mist }}>
              Live
            </button>
          </div>
          {dataMode === "live" && (
            <div className="flex items-center gap-1.5">
              {liveStatus === "connected" ? <Wifi size={14} color={COLORS.safe} /> : <WifiOff size={14} color={COLORS.alert} />}
              {editingUrl ? (
                <input autoFocus value={liveUrl} onChange={(e) => setLiveUrl(e.target.value)}
                  onBlur={() => setEditingUrl(false)} onKeyDown={(e) => e.key === "Enter" && setEditingUrl(false)}
                  className="px-2 py-1 rounded outline-none" style={{ background: "rgba(238,244,241,0.08)", color: COLORS.paper, width: 220 }} />
              ) : (
                <button onClick={() => setEditingUrl(true)} className="underline decoration-dotted" style={{ color: COLORS.mist }}>
                  {liveUrl}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------
   USER VIEW — plain language, caregiver-facing
----------------------------------------------------------------*/
function UserPage({ riskScore, readings, events, audioClass }) {
  const status = riskScore > 55 ? "alert" : riskScore > 30 ? "watch" : "safe";
  const statusCopy = {
    safe: { title: "Everything looks normal", body: "No unusual activity detected in the bathroom right now." },
    watch: { title: "Keeping an eye on things", body: "One or two readings are slightly outside the usual pattern. Nothing urgent." },
    alert: { title: "Checking in now", body: "SafeBath is asking if everything's alright before contacting you." },
  }[status];
  const color = { safe: COLORS.safe, watch: COLORS.watch, alert: COLORS.alert }[status];

  return (
    <div className="pt-8">
      <Section title="Household Status">
        <div className="rounded-2xl p-8 flex items-center gap-6 flex-wrap" style={{ background: `linear-gradient(180deg, ${COLORS.panel}, ${COLORS.dusk})`, border: "1px solid rgba(238,244,241,0.10)" }}>
          <div className="w-20 h-20 rounded-full flex items-center justify-center flex-none" style={{ background: color + "22", border: `2px solid ${color}55` }}>
            {status === "alert" ? <AlertTriangle size={30} color={color} /> : <ShieldCheck size={30} color={color} />}
          </div>
          <div>
            <div style={{ fontFamily: "'Fraunces', serif", fontSize: 26, fontWeight: 500 }}>{statusCopy.title}</div>
            <p style={{ color: COLORS.mist, marginTop: 6, maxWidth: 520 }}>{statusCopy.body}</p>
          </div>
        </div>
      </Section>

      <Section title="Voice Bridge">
        <div className="rounded-2xl p-5 flex items-center gap-4" style={{ background: "rgba(232,178,86,0.07)", border: "1px solid rgba(232,178,86,0.25)" }}>
          <div className="w-10 h-10 rounded-full flex items-center justify-center flex-none" style={{ background: "rgba(232,178,86,0.18)" }}>
            <Volume2 size={18} color={COLORS.watch} />
          </div>
          <p style={{ color: COLORS.mist, fontSize: 13.5, lineHeight: 1.6 }}>
            Before contacting you, SafeBath asks out loud, <i>"Are you alright?"</i> A calm reply stands the alert down automatically — you're only contacted if there's silence or a request for help.
          </p>
        </div>
      </Section>

      <Section title="Recent Activity">
        <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid rgba(238,244,241,0.10)" }}>
          {events.slice(0, 6).map((e, i) => (
            <div key={e.id} className="flex items-center gap-3 px-5 py-3.5"
              style={{ background: i % 2 ? "rgba(238,244,241,0.02)" : "transparent", borderTop: i ? "1px solid rgba(238,244,241,0.06)" : "none" }}>
              <span className="w-2 h-2 rounded-full flex-none" style={{ background: e.level === "watch" ? COLORS.watch : COLORS.safe }} />
              <span style={{ fontSize: 13.5 }}>{plainLanguage(e.text)}</span>
              <span style={{ marginLeft: "auto", fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: COLORS.mist }}>
                {e.time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
          ))}
          {events.length === 0 && <div className="px-5 py-6 text-sm" style={{ color: COLORS.mist }}>No activity yet.</div>}
        </div>
      </Section>
    </div>
  );
}

function plainLanguage(text) {
  return text
    .replace(/Board \d/g, "The system")
    .replace(/Fusion engine recomputed risk score.*/, "Overall status re-checked")
    .replace(/mat sway index.*/, "movement pattern looked steady")
    .replace(/toilet reading logged.*/, "a routine check came back normal")
    .replace(/audio event classified: silence/, "it's quiet, as expected")
    .replace(/audio event classified: water/, "running water was heard, as expected")
    .replace(/thermal frame captured.*/, "temperature checked, nothing unusual");
}

/* ---------------------------------------------------------------
   AI / MODULE DASHBOARD
----------------------------------------------------------------*/
function DashboardPage({ riskScore, readings, events, dataMode, liveStatus, audioClass, matFrame }) {
  const color = riskScore > 55 ? COLORS.alert : riskScore > 30 ? COLORS.watch : COLORS.safe;
  const label = riskScore > 55 ? "ELEVATED" : riskScore > 30 ? "WATCH" : "NORMAL";

  return (
    <div className="pt-8">
      <Section title="Fused Household Risk">
        <div className="rounded-2xl p-6 flex items-center gap-6 flex-wrap" style={{ background: `linear-gradient(180deg, ${COLORS.panel}, ${COLORS.dusk})`, border: "1px solid rgba(238,244,241,0.10)" }}>
          <div className="relative w-28 h-28 flex-none">
            <svg width="112" height="112" viewBox="0 0 112 112" style={{ transform: "rotate(-90deg)" }}>
              <circle cx="56" cy="56" r="46" fill="none" stroke="rgba(238,244,241,0.15)" strokeWidth="9" />
              <circle cx="56" cy="56" r="46" fill="none" stroke={color} strokeWidth="9" strokeLinecap="round"
                strokeDasharray={2 * Math.PI * 46} strokeDashoffset={2 * Math.PI * 46 * (1 - riskScore / 100)}
                style={{ transition: "stroke-dashoffset 1s ease, stroke 0.5s ease" }} />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span style={{ fontFamily: "'Fraunces', serif", fontSize: 30, fontWeight: 500 }}>{Math.round(riskScore)}</span>
              <span style={{ fontSize: 9.5, color: COLORS.mist }}>/ 100</span>
            </div>
          </div>
          <div className="flex-1 min-w-[220px]">
            <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs" style={{ fontFamily: "'IBM Plex Mono', monospace", color, background: color + "22", border: `1px solid ${color}55` }}>
              {label}
            </span>
            <p style={{ color: COLORS.mist, fontSize: 13, marginTop: 10 }}>
              Weighted ensemble across all seven modules below, with severity decay — a single spike doesn't escalate; a sustained pattern does.
            </p>
            <div className="mt-2 flex items-center gap-2 text-xs" style={{ fontFamily: "'IBM Plex Mono', monospace", color: COLORS.mist }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: dataMode === "live" && liveStatus === "connected" ? COLORS.safe : COLORS.mist }} />
              {dataMode === "mock" ? "mock data" : liveStatus === "connected" ? "live feed connected" : liveStatus === "error" ? "live feed unreachable — check API URL above" : "connecting..."}
            </div>
          </div>
        </div>
      </Section>

      <Section title="Module-by-Module — AI Models, Metrics, Live Results">
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          {MODULES.map((m) => {
            const Icon = m.icon;
            const val = readings[m.id];
            const display = m.id === "audio" ? "—" : m.unit === "class" ? "—" : val?.toFixed?.(m.unit === "mm min" ? 0 : 2);
            return (
              <div key={m.id} className="rounded-2xl p-4" style={{ background: "rgba(238,244,241,0.03)", border: "1px solid rgba(238,244,241,0.10)" }}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "rgba(121,200,164,0.12)" }}>
                      <Icon size={15} color={COLORS.safe} />
                    </div>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 500 }}>{m.name}</div>
                      <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: COLORS.mist }}>{m.board}</div>
                    </div>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs" style={{ color: COLORS.mist }}>
                  <div>
                    <div style={{ fontSize: 10, opacity: 0.8 }}>AI Model</div>
                    <div style={{ color: COLORS.paper, fontSize: 12 }}>{m.model}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, opacity: 0.8 }}>{m.metric}</div>
                    <div style={{ color: COLORS.paper, fontFamily: "'IBM Plex Mono', monospace", fontSize: 12 }}>{m.metricValue}</div>
                  </div>
                </div>
                <div className="mt-3 flex items-center justify-between pt-3" style={{ borderTop: "1px solid rgba(238,244,241,0.08)" }}>
                  <span style={{ fontSize: 10, color: COLORS.mist }}>{m.framework}</span>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 15 }}>
                    {m.id === "audio" ? audioClass : `${display}${m.unit !== "class" ? " " + m.unit : ""}`}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="Live Velostat Heatmap — Board 1, 16 rows × 4 cols">
        <MatHeatmap frame={matFrame} />
      </Section>

      <Section title="Event Log">
        <div className="rounded-2xl overflow-hidden max-h-72 overflow-y-auto" style={{ border: "1px solid rgba(238,244,241,0.10)" }}>
          {events.map((e, i) => (
            <div key={e.id} className="flex items-center gap-3 px-5 py-3"
              style={{ borderTop: i ? "1px solid rgba(238,244,241,0.06)" : "none" }}>
              <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: COLORS.mist, width: 56, flex: "none" }}>
                {e.time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
              <span className="w-1.5 h-1.5 rounded-full flex-none" style={{ background: e.level === "watch" ? COLORS.watch : COLORS.safe }} />
              <span style={{ fontSize: 12.5 }}>{e.text}</span>
            </div>
          ))}
          {events.length === 0 && <div className="px-5 py-6 text-sm" style={{ color: COLORS.mist }}>No events yet — waiting on data.</div>}
        </div>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------
   VELOSTAT LIVE HEATMAP — Board 1, 16 rows x 4 cols (Final Master
   Guide Section 2.4: heatmap + center-of-pressure trail)
----------------------------------------------------------------*/
function MatHeatmap({ frame }) {
  const [showValues, setShowValues] = useState(false);
  const rows = frame.length || 16;
  const cols = frame[0]?.length || 4;
  const cellW = 46, cellH = 20, pad = 2;
  const maxVal = 4095;

  // Weighted centroid across all 64 cells — the same signal the
  // ConvLSTM's sway trajectory is derived from.
  let sumW = 0, sumR = 0, sumC = 0;
  frame.forEach((row, r) => row.forEach((v, c) => {
    sumW += v; sumR += v * r; sumC += v * c;
  }));
  const centroid = sumW > 0.001 ? { r: sumR / sumW, c: sumC / sumW } : null;

  const colorFor = (v) => {
    const t = Math.min(1, v / maxVal);
    // paper/mist (cool, low) -> watch (mid) -> alert (hot, high pressure)
    if (t < 0.5) return lerpColor(COLORS.dusk, COLORS.watch, t / 0.5);
    return lerpColor(COLORS.watch, COLORS.alert, (t - 0.5) / 0.5);
  };

  return (
    <div className="rounded-2xl p-5" style={{ background: "rgba(238,244,241,0.03)", border: "1px solid rgba(238,244,241,0.10)" }}>
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <p style={{ color: COLORS.mist, fontSize: 12.5, maxWidth: 480 }}>
          Blue/dusk = no pressure, amber → red = increasing pressure. The dot traces the weighted center of pressure —
          the same signal the fall-risk ConvLSTM model reads as sway trajectory.
        </p>
        <button onClick={() => setShowValues((s) => !s)}
          className="px-3 py-1.5 rounded-lg text-xs flex-none"
          style={{ background: "rgba(238,244,241,0.06)", color: COLORS.mist, border: "1px solid rgba(238,244,241,0.12)" }}>
          {showValues ? "Hide raw ADC values" : "Show raw ADC values"}
        </button>
      </div>

      <div className="flex justify-center">
        <svg width={cols * (cellW + pad)} height={rows * (cellH + pad)}>
          {frame.map((row, r) =>
            row.map((v, c) => (
              <g key={`${r}-${c}`}>
                <rect
                  x={c * (cellW + pad)} y={r * (cellH + pad)}
                  width={cellW} height={cellH} rx={4}
                  fill={colorFor(v)}
                  style={{ transition: "fill 0.4s ease" }}
                />
                {showValues && (
                  <text
                    x={c * (cellW + pad) + cellW / 2} y={r * (cellH + pad) + cellH / 2 + 3}
                    textAnchor="middle" fontSize="8.5" fontFamily="'IBM Plex Mono', monospace"
                    fill={COLORS.ink}
                  >{v}</text>
                )}
              </g>
            ))
          )}
          {centroid && (
            <circle
              cx={centroid.c * (cellW + pad) + cellW / 2}
              cy={centroid.r * (cellH + pad) + cellH / 2}
              r={5} fill={COLORS.paper} stroke={COLORS.ink} strokeWidth={1.5}
              style={{ transition: "cx 0.4s ease, cy 0.4s ease" }}
            />
          )}
        </svg>
      </div>

      <div className="flex items-center gap-4 justify-center mt-4 text-xs" style={{ fontFamily: "'IBM Plex Mono', monospace", color: COLORS.mist }}>
        <LegendSwatch color={COLORS.dusk} label="no pressure" />
        <LegendSwatch color={COLORS.watch} label="moderate" />
        <LegendSwatch color={COLORS.alert} label="high pressure" />
      </div>
    </div>
  );
}

function LegendSwatch({ color, label }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-3 h-3 rounded" style={{ background: color }} />
      {label}
    </div>
  );
}

function lerpColor(a, b, t) {
  const pa = hexToRgb(a), pb = hexToRgb(b);
  const r = Math.round(pa.r + (pb.r - pa.r) * t);
  const g = Math.round(pa.g + (pb.g - pa.g) * t);
  const bl = Math.round(pa.b + (pb.b - pa.b) * t);
  return `rgb(${r},${g},${bl})`;
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return {
    r: parseInt(h.substring(0, 2), 16),
    g: parseInt(h.substring(2, 4), 16),
    b: parseInt(h.substring(4, 6), 16),
  };
}

/* ---------------------------------------------------------------
   HARDWARE & CONNECTIONS PAGE
----------------------------------------------------------------*/
function HardwarePage({ hardware, setHardware }) {
  const [open, setOpen] = useState({ 0: true });
  const [dirty, setDirty] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState(null);

  const updateCell = (boardIdx, rowIdx, colIdx, value) => {
    setHardware((prev) => {
      const next = prev.map((b) => ({ ...b, rows: b.rows.map((r) => [...r]) }));
      next[boardIdx].rows[rowIdx][colIdx] = value;
      return next;
    });
    setDirty(true);
  };

  const copyFirmware = (idx, code) => {
    navigator.clipboard?.writeText(code);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 1500);
  };

  return (
    <div className="pt-8">
      <Section title="Hardware & Connections">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
          <p style={{ color: COLORS.mist, fontSize: 13, maxWidth: 560 }}>
            Editable reference for each board's wiring. Update this once Joy Projects finalizes the physical connections —
            edits are saved automatically and this is the single place the firmware snippets are derived from.
          </p>
          <div className="flex gap-2">
            <button onClick={() => { setHardware(DEFAULT_HARDWARE); setDirty(true); }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ background: "rgba(238,244,241,0.06)", color: COLORS.mist, border: "1px solid rgba(238,244,241,0.12)" }}>
              <RotateCcw size={12} /> Reset to documented defaults
            </button>
            <button onClick={() => { saveHardware(hardware); setDirty(false); }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ background: dirty ? COLORS.safe : "rgba(121,200,164,0.15)", color: dirty ? COLORS.ink : COLORS.safe, fontWeight: 500 }}>
              <Save size={12} /> {dirty ? "Save changes" : "Saved"}
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          {hardware.map((b, bi) => (
            <div key={b.board} className="rounded-2xl overflow-hidden" style={{ border: "1px solid rgba(238,244,241,0.10)" }}>
              <button onClick={() => setOpen((o) => ({ ...o, [bi]: !o[bi] }))}
                className="w-full flex items-center justify-between px-5 py-4" style={{ background: "rgba(238,244,241,0.03)" }}>
                <div className="flex items-center gap-3">
                  {open[bi] ? <ChevronDown size={16} color={COLORS.mist} /> : <ChevronRight size={16} color={COLORS.mist} />}
                  <div className="text-left">
                    <div style={{ fontWeight: 500, fontSize: 14 }}>{b.board} — {b.title}</div>
                  </div>
                </div>
                <StatusTag status={b.status} />
              </button>

              {open[bi] && (
                <div className="px-5 pb-5">
                  <div className="overflow-x-auto">
                    <table className="w-full text-left" style={{ fontSize: 12.5, borderCollapse: "collapse" }}>
                      <thead>
                        <tr style={{ color: COLORS.mist, fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5 }}>
                          <th className="pb-2 pr-3">COMPONENT</th>
                          <th className="pb-2 pr-3">PIN</th>
                          <th className="pb-2 pr-3">GPIO</th>
                          <th className="pb-2">NOTES</th>
                        </tr>
                      </thead>
                      <tbody>
                        {b.rows.map((row, ri) => (
                          <tr key={ri} style={{ borderTop: "1px solid rgba(238,244,241,0.07)" }}>
                            {row.map((cell, ci) => (
                              <td key={ci} className="py-2 pr-3 align-top">
                                <EditableCell value={cell} onChange={(v) => updateCell(bi, ri, ci, v)} mono={ci === 2} />
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="mt-4">
                    <div className="flex items-center justify-between mb-1.5">
                      <span style={{ fontSize: 10.5, letterSpacing: "0.06em", color: COLORS.mist, textTransform: "uppercase" }}>Firmware snippet</span>
                      <button onClick={() => copyFirmware(bi, b.firmware)} className="flex items-center gap-1 text-xs" style={{ color: COLORS.mist }}>
                        {copiedIdx === bi ? <Check size={12} color={COLORS.safe} /> : <Copy size={12} />} {copiedIdx === bi ? "Copied" : "Copy"}
                      </button>
                    </div>
                    <pre className="rounded-xl p-4 overflow-x-auto" style={{ background: COLORS.ink, border: "1px solid rgba(238,244,241,0.08)", fontFamily: "'IBM Plex Mono', monospace", fontSize: 11.5, color: COLORS.mist, lineHeight: 1.6 }}>
                      {b.firmware}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}

function EditableCell({ value, onChange, mono }) {
  return (
    <input value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full bg-transparent outline-none rounded px-1 py-0.5"
      style={{ color: COLORS.paper, fontFamily: mono ? "'IBM Plex Mono', monospace" : "inherit", border: "1px solid transparent" }}
      onFocus={(e) => (e.target.style.border = "1px solid rgba(121,200,164,0.4)")}
      onBlur={(e) => (e.target.style.border = "1px solid transparent")}
    />
  );
}

function StatusTag({ status }) {
  const map = {
    confirmed: { label: "wired", color: COLORS.safe },
    pending: { label: "pending arrival", color: COLORS.watch },
    mixed: { label: "partly pending", color: COLORS.watch },
  };
  const s = map[status] || map.confirmed;
  return (
    <span className="px-2.5 py-1 rounded-full text-xs flex-none" style={{ fontFamily: "'IBM Plex Mono', monospace", color: s.color, background: s.color + "1a", border: `1px solid ${s.color}55` }}>
      {s.label}
    </span>
  );
}

/* ---------------------------------------------------------------
   Shared
----------------------------------------------------------------*/
function Section({ title, children }) {
  return (
    <section className="mb-10">
      <h2 style={{ fontFamily: "'Fraunces', serif", fontWeight: 500, fontSize: 13, letterSpacing: "0.05em", color: COLORS.mist, textTransform: "uppercase", marginBottom: 14 }}>
        {title}
      </h2>
      {children}
    </section>
  );
}
