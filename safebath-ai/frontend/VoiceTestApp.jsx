import React, { useEffect, useRef, useState } from "react";
import { resolveApiBase } from "./apiBase.mjs";

const API_BASE = resolveApiBase();
const API_HEALTH = `${API_BASE}/api/v1/health`;
const API_TURN = `${API_BASE}/api/v1/voice-test/turn`;
const SESSION_DELETE = `${API_BASE}/api/v1/voice-test/`;
const EMERGENCY_WORDS = /\b(help|emergency|sos|drowning|can't breathe|cannot breathe|not breathing|bleeding|fell|fall|unconscious|chest pain)\b/i;

function newSessionId() {
  if (globalThis.crypto?.randomUUID) return `voice-${globalThis.crypto.randomUUID()}`;
  return `voice-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function errorForStatus(status) {
  const messages = {
    401: "The backend rejected the token. Check it and try again.",
    403: "The backend denied this request. Check the voice-test configuration and token permissions.",
    404: "The voice-test endpoint was not found. Check the backend version and API URL.",
    422: "The backend could not accept this transcript. Try speaking again.",
    429: "The conversation provider is rate limited. Wait a moment and try again.",
    500: "The backend encountered an error while handling the request.",
    502: "The conversation provider failed or timed out. Local emergency procedures remain available.",
    503: "The voice-test feature is not enabled or is missing provider configuration.",
  };
  return messages[status] || `The backend returned HTTP ${status}.`;
}

function StatusPill({ state }) {
  const colors = {
    Idle: ["#18364a", "#a8c5d8"], Listening: ["#174c3d", "#9ae6c8"],
    Processing: ["#493b17", "#f4d78a"], Speaking: ["#32275a", "#c8b8ff"],
    Error: ["#512629", "#ffc0c0"],
  }[state] || ["#243244", "#d2dbe7"];
  return <span role="status" aria-live="polite" style={{ ...styles.pill, background: colors[0], color: colors[1] }}>{state}</span>;
}

export default function VoiceTestApp() {
  const [token, setToken] = useState("");
  const [backend, setBackend] = useState({ label: "Not checked", tone: "muted" });
  const [state, setState] = useState("Idle");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(newSessionId);
  const [speakResponse, setSpeakResponse] = useState(true);
  const [interim, setInterim] = useState("");
  const [listeningSupported, setListeningSupported] = useState(true);
  const [synthesisSupported, setSynthesisSupported] = useState(true);
  const recognitionRef = useRef(null);
  const activeRef = useRef(true);
  const intentionalStopRef = useRef(false);
  const submittedRef = useRef(false);
  const controllerRef = useRef(null);

  useEffect(() => {
    const supported = Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
    setListeningSupported(supported);
    setSynthesisSupported(Boolean(window.speechSynthesis && window.SpeechSynthesisUtterance));
    return () => {
      activeRef.current = false;
      intentionalStopRef.current = true;
      recognitionRef.current?.abort?.();
      controllerRef.current?.abort?.();
      window.speechSynthesis?.cancel?.();
    };
  }, []);

  function addMessage(role, text, extra = {}) {
    setMessages((items) => [...items, {
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      role, text, ...extra,
    }]);
  }

  async function testBackend() {
    setError("");
    setBackend({ label: "Checking…", tone: "muted" });
    const aborter = new AbortController();
    const timeout = setTimeout(() => aborter.abort(), 7000);
    try {
      const response = await fetch(API_HEALTH, { signal: aborter.signal, headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(errorForStatus(response.status));
      setBackend({ label: "Reachable · token is checked when you send", tone: "good" });
      setNotice("");
    } catch (err) {
      const text = err.name === "AbortError"
        ? "Backend check timed out."
        : err instanceof TypeError
          ? `Could not reach ${API_BASE}. Check the URL, CORS, network, and whether the backend is running.`
          : err.message;
      setBackend({ label: "Unavailable", tone: "bad" });
      setError(text);
    } finally {
      clearTimeout(timeout);
    }
  }

  async function sendTranscript(rawText) {
    const text = rawText.trim();
    if (!text || submittedRef.current) return;
    submittedRef.current = true;
    setInterim("");
    setError("");
    addMessage("user", text, { emergency: EMERGENCY_WORDS.test(text) });
    if (EMERGENCY_WORDS.test(text)) {
      setNotice("If anyone may be in immediate danger, use the available physical SOS/emergency procedure and seek immediate assistance now. Do not wait for this assistant or Gemini.");
    }
    if (!token.trim()) {
      setState("Error");
      setError("Enter the voice-test bearer token before sending a transcript.");
      return;
    }

    setState("Processing");
    const aborter = new AbortController();
    controllerRef.current = aborter;
    try {
      const response = await fetch(API_TURN, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          Accept: "application/json",
        },
        body: JSON.stringify({ session_id: sessionId, text }),
        signal: aborter.signal,
      });
      if (!response.ok) throw new Error(errorForStatus(response.status));
      const data = await response.json();
      if (typeof data.reply_text !== "string" || !data.reply_text.trim()) {
        throw new Error("The backend response did not contain reply text.");
      }
      if (!activeRef.current || aborter.signal.aborted) return;
      addMessage("assistant", data.reply_text.trim(), { alertSent: data.alert_sent === true });
      setBackend({ label: "Reachable", tone: "good" });
      if (speakResponse && window.speechSynthesis && window.SpeechSynthesisUtterance) {
        window.speechSynthesis.cancel();
        const utterance = new window.SpeechSynthesisUtterance(data.reply_text.trim());
        utterance.lang = "en-IN";
        utterance.onstart = () => activeRef.current && setState("Speaking");
        utterance.onend = () => activeRef.current && setState("Idle");
        utterance.onerror = () => {
          if (activeRef.current) {
            setState("Error");
            setError("The browser could not speak this response.");
          }
        };
        window.speechSynthesis.speak(utterance);
      } else {
        setState("Idle");
      }
    } catch (err) {
      if (!activeRef.current || err.name === "AbortError") return;
      setState("Error");
      if (err instanceof TypeError) {
        setError(`Could not reach ${API_BASE}. Check the network, CORS settings, and backend status.`);
      } else {
        setError(err.message || "Conversation request failed.");
      }
    } finally {
      if (controllerRef.current === aborter) controllerRef.current = null;
      if (activeRef.current && !window.speechSynthesis?.speaking && state !== "Speaking") {
        // The next successful turn or explicit action sets the visible state.
      }
    }
  }

  function startListening() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      setListeningSupported(false);
      setState("Error");
      setError("Speech recognition is not supported in this browser. Try a current version of Chrome or Edge.");
      return;
    }
    if (state === "Processing" || state === "Listening") return;
    setError("");
    setNotice("");
    setInterim("");
    submittedRef.current = false;
    intentionalStopRef.current = false;
    window.speechSynthesis?.cancel?.();
    const recognition = new Recognition();
    recognition.lang = "en-IN";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognitionRef.current = recognition;

    recognition.onstart = () => activeRef.current && setState("Listening");
    recognition.onresult = (event) => {
      if (recognitionRef.current !== recognition) return;
      let finalText = "";
      let interimText = "";
      for (let i = 0; i < event.results.length; i++) {
        const item = event.results[i];
        if (item.isFinal) finalText += `${item[0].transcript} `;
        else interimText += `${item[0].transcript} `;
      }
      recognition.finalTranscript = finalText.trim();
      setInterim(interimText.trim());
    };
    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition) return;
      recognition.failed = true;
      const denied = event.error === "not-allowed" || event.error === "service-not-allowed";
      setState("Error");
      setError(denied
        ? "Microphone or speech-recognition permission was denied. Allow microphone access in the browser and try again."
        : event.error === "no-speech"
          ? "No speech was detected. Check microphone access and try again."
          : `Speech recognition error: ${event.error || "unknown error"}.`);
    };
    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      setInterim("");
      if (intentionalStopRef.current && !recognition.finalTranscript) {
        setState("Idle");
        return;
      }
      const text = (recognition.finalTranscript || "").trim();
      if (!text) {
        if (!recognition.failed) setState("Idle");
        return;
      }
      void sendTranscript(text);
    };
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setState("Error");
      setError("Could not start speech recognition. Check microphone permission and try again.");
    }
  }

  function stopListening() {
    if (!recognitionRef.current) return;
    intentionalStopRef.current = true;
    try { recognitionRef.current.stop(); }
    catch {
      recognitionRef.current = null;
      setState("Idle");
    }
  }

  function stopSpeaking() {
    window.speechSynthesis?.cancel?.();
    if (state === "Speaking") setState("Idle");
  }

  async function clearConversation() {
    intentionalStopRef.current = true;
    const oldId = sessionId;
    const hadToken = Boolean(token.trim());
    recognitionRef.current?.abort?.();
    recognitionRef.current = null;
    controllerRef.current?.abort?.();
    controllerRef.current = null;
    window.speechSynthesis?.cancel?.();
    setState("Idle");
    setInterim("");
    setMessages([]);
    setError("");
    setNotice("");
    setSessionId(newSessionId());

    if (hadToken) {
      try {
        const response = await fetch(`${SESSION_DELETE}${encodeURIComponent(oldId)}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
        });
        if (!response.ok) setError(`Conversation cleared locally, but the backend session could not be deleted: ${errorForStatus(response.status)}`);
      } catch {
        setError("Conversation cleared locally, but the backend session could not be reached for deletion.");
      }
    }
  }

  const busy = state === "Processing";
  const isListening = state === "Listening";
  const isSpeaking = state === "Speaking";

  return (
    <main style={styles.page}>
      <div style={styles.shell}>
        <header style={styles.header}>
          <div>
            <p style={styles.eyebrow}>SAFEBATH AI · PRIVATE PROTOTYPE</p>
            <h1 style={styles.title}>SafeBath AI Voice Assistant</h1>
            <p style={styles.subtitle}>A browser-based conversation test. It is separate from the safety sensor and emergency-alert system.</p>
          </div>
          <StatusPill state={state} />
        </header>

        <section aria-label="Backend connection" style={styles.card}>
          <div style={styles.sectionTop}>
            <div><h2 style={styles.h2}>Backend connection</h2><p style={styles.small}>API: <code style={styles.code}>{API_BASE}</code></p></div>
            <span aria-live="polite" style={{ ...styles.connection, color: backend.tone === "good" ? "#92e3bd" : backend.tone === "bad" ? "#ffb0b0" : "#bdcad9" }}>{backend.label}</span>
          </div>
          <label htmlFor="voice-token" style={styles.label}>Voice-test bearer token</label>
          <div style={styles.connectRow}>
            <input id="voice-token" autoComplete="off" type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="Enter token for this page session" style={styles.input} />
            <button type="button" onClick={testBackend} style={styles.secondaryButton}>Test backend</button>
          </div>
          <p style={styles.small}>The token stays in this page’s memory. Backend reachability does not validate the token; the first conversation request does.</p>
        </section>

        <section aria-label="Conversation controls" style={styles.card}>
          <div style={styles.sectionTop}>
            <div><h2 style={styles.h2}>Conversation</h2><p style={styles.small}>Session <code style={styles.code}>{sessionId.slice(0, 22)}…</code></p></div>
            <label style={styles.toggle}><input type="checkbox" checked={speakResponse} onChange={(event) => setSpeakResponse(event.target.checked)} /> Speak Response</label>
          </div>
          <div style={styles.controls}>
            <button type="button" onClick={startListening} disabled={!listeningSupported || isListening || busy} style={styles.primaryButton}>Start Listening</button>
            <button type="button" onClick={stopListening} disabled={!isListening} style={styles.secondaryButton}>Stop Listening</button>
            <button type="button" onClick={stopSpeaking} disabled={!isSpeaking} style={styles.secondaryButton}>Stop Speaking</button>
            <button type="button" onClick={clearConversation} style={styles.clearButton}>Clear Conversation</button>
          </div>
          {!listeningSupported && <p role="alert" style={styles.warning}>Speech recognition is not supported by this browser.</p>}
          {!synthesisSupported && <p style={styles.warning}>Speech synthesis is unavailable in this browser. You can still read replies on screen.</p>}
          <p style={styles.small}>Browser speech recognition may use an online speech service. Recognition is not guaranteed to work offline.</p>
          <div aria-live="polite" aria-relevant="additions text" style={styles.transcript}>
            {messages.length === 0 && !interim && <p style={styles.empty}>Your conversation will appear here. Press Start Listening and speak when the browser is ready.</p>}
            {messages.map((message) => (
              <article key={message.id} style={{ ...styles.message, alignSelf: message.role === "user" ? "flex-end" : "flex-start", background: message.role === "user" ? "#173a43" : "#182438" }}>
                <strong style={styles.role}>{message.role === "user" ? "You" : "Assistant"}</strong>
                <p style={styles.messageText}>{message.text}</p>
                {message.emergency && <p style={styles.emergency}>Use the physical SOS/emergency procedure and seek immediate assistance. Do not wait for Gemini.</p>}
                {message.alertSent && <p style={styles.small}>The backend explicitly reported alert_sent=true.</p>}
                {message.role === "assistant" && <span style={styles.advisory}>AI response · advisory only · browser audio</span>}
              </article>
            ))}
            {interim && <p style={styles.interim}>Listening… {interim}</p>}
          </div>
          {notice && <p role="alert" style={styles.emergencyBanner}>{notice}</p>}
          {error && <p role="alert" style={styles.error}>{error}</p>}
        </section>

        <aside role="note" style={styles.disclaimer}>
          <strong>Prototype disclaimer:</strong> This assistant is advisory and does not replace emergency services, the existing SafeBath detection system, or the available physical SOS procedure. AI text never controls a valve, door, or other hardware. Browser speech is played on this device and is not sent to the ESP32 or DFPlayer Mini.
        </aside>
      </div>
    </main>
  );
}

const styles = {
  page: { minHeight: "100vh", padding: "32px 16px 56px", background: "radial-gradient(ellipse at 12% 0%, #173043 0, transparent 38%), #0b1524" },
  shell: { width: "min(900px, 100%)", margin: "0 auto", display: "grid", gap: 18 },
  header: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 18, padding: "14px 2px" },
  eyebrow: { margin: "0 0 8px", color: "#82d9c6", letterSpacing: ".14em", fontSize: 11, fontWeight: 800 },
  title: { margin: 0, fontSize: "clamp(26px, 5vw, 38px)", letterSpacing: "-.035em" },
  subtitle: { margin: "10px 0 0", maxWidth: 680, color: "#a8b5c6", lineHeight: 1.55 },
  pill: { display: "inline-flex", flexShrink: 0, padding: "7px 12px", borderRadius: 999, fontSize: 13, fontWeight: 750 },
  card: { border: "1px solid #2c3b4e", borderRadius: 18, padding: "22px clamp(16px, 4vw, 28px)", background: "rgba(17, 29, 46, .94)", boxShadow: "0 14px 40px #02081744" },
  sectionTop: { display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, marginBottom: 18 },
  h2: { margin: 0, fontSize: 18 },
  small: { color: "#a5b2c2", fontSize: 13, lineHeight: 1.5, margin: "6px 0 0", overflowWrap: "anywhere" },
  code: { color: "#c7d5e6", fontSize: 12, overflowWrap: "anywhere" },
  connection: { fontSize: 13, fontWeight: 700 },
  label: { display: "block", marginBottom: 8, fontSize: 13, fontWeight: 700 },
  connectRow: { display: "flex", gap: 10 },
  input: { width: "100%", minWidth: 0, borderRadius: 10, border: "1px solid #41516a", padding: "11px 12px", background: "#0b1524", color: "#eef4fb" },
  controls: { display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 8 },
  primaryButton: { border: 0, borderRadius: 10, padding: "11px 15px", background: "#71d4bf", color: "#06221e", fontWeight: 800, cursor: "pointer" },
  secondaryButton: { border: "1px solid #53647a", borderRadius: 10, padding: "10px 14px", background: "#1c2b3f", color: "#e7edf5", fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" },
  clearButton: { border: "1px solid #774c52", borderRadius: 10, padding: "10px 14px", background: "#32242c", color: "#ffd9d7", fontWeight: 700, cursor: "pointer" },
  toggle: { display: "flex", gap: 8, alignItems: "center", color: "#d8e3ef", fontSize: 14, cursor: "pointer" },
  transcript: { display: "flex", flexDirection: "column", gap: 12, minHeight: 220, maxHeight: 460, overflowY: "auto", padding: 14, border: "1px solid #26364a", borderRadius: 14, background: "#0b1524" },
  empty: { margin: "auto", maxWidth: 430, textAlign: "center", color: "#8998aa", lineHeight: 1.6 },
  message: { width: "min(85%, 650px)", border: "1px solid #34445a", borderRadius: 14, padding: "13px 15px" },
  role: { color: "#9ee5d5", fontSize: 12, letterSpacing: ".04em" },
  messageText: { margin: "6px 0", lineHeight: 1.55, whiteSpace: "pre-wrap", overflowWrap: "anywhere" },
  advisory: { color: "#9daec1", fontSize: 11 },
  interim: { margin: 0, color: "#acc3d6", fontStyle: "italic" },
  error: { margin: "14px 0 0", padding: 12, color: "#ffd3d3", background: "#4b2328", border: "1px solid #7b3d43", borderRadius: 10, lineHeight: 1.5 },
  warning: { color: "#f4d78a", margin: "12px 0", fontSize: 13 },
  emergency: { margin: "10px 0 0", color: "#ffe1a3", fontWeight: 700, lineHeight: 1.45 },
  emergencyBanner: { margin: "14px 0 0", padding: 13, color: "#fff0cc", background: "#594119", border: "1px solid #90713d", borderRadius: 10, lineHeight: 1.5, fontWeight: 650 },
  disclaimer: { padding: "15px 17px", borderRadius: 13, border: "1px solid #66522f", background: "#292516", color: "#e6d9b8", lineHeight: 1.55, fontSize: 13 },
};
