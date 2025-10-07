// Agent HUD Realtime Client
// - WebRTC to model (mic -> model -> agent audio)
// - Agent captions: (A) WebRTC data channel (if provider sends), (B) fallback server STT (/v1/stt/agent)
// - User live line: Web Speech API (Chrome best)
// - Voice ring: Analyser -> circular HUD + EQ bars
// ----------------------------------------------------

const statusEl = document.getElementById("status");
const rtcInfoEl = document.getElementById("rtcInfo");
const captionsInfoEl = document.getElementById("captionsInfo");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");
const muteBtn = document.getElementById("muteBtn");
const unmuteBtn = document.getElementById("unmuteBtn");
const voiceSel = document.getElementById("voice");
const instructionsInput = document.getElementById("instructions");

const youLive = document.getElementById("youLive");
const agentLive = document.getElementById("agentLive");
const agentLed = document.getElementById("agentLed");
const agentSpeakingBadge = document.getElementById("agentSpeakingBadge");

const eqBars = document.querySelectorAll(".eq .bar");
const ringCanvas = document.getElementById("voiceRing");
const ringCtx = ringCanvas.getContext("2d");

// State
let pc = null;
let localStream = null;
let remoteAudio = null;
let connected = false;

// User STT
let rec = null;

// Audio analyser
let audioCtx = null, analyser = null, remoteSource = null, rafId = null;

// Agent STT fallback
let agentRec = null, sttAbort = null;
let captionsSource = "none";   // "datachannel" | "stt" | "unavailable" | "none"
let receivedDataChannelCaption = false;

// Helpers
function setStatus(msg){
  const existing = String(statusEl.textContent || "");
  statusEl.textContent = (existing ? existing + "\n" : "") + msg;
}
function setYouText(text){ youLive.innerHTML = `<span class="you">${text}</span>`; }
function setAgentText(text){
  agentLive.innerHTML = `<span class="agent">${text}</span>`;
  agentLed.classList.toggle("on", !!text && text.trim() !== "");
}
function setAgentSpeaking(on){
  agentSpeakingBadge.textContent = `AGENT: ${on ? "speaking…" : "idle"}`;
}
function setCaptionsBadge(){
  captionsInfoEl.textContent = `CAPTIONS: ${captionsSource}`;
}

// ===== Voice Ring HUD =====
function drawRing(level){
  const ctx = ringCtx;
  const w = ringCanvas.width, h = ringCanvas.height;
  const cx = w/2, cy = h/2;
  const rBase = 78;
  const rPulse = 18 * level;

  ctx.clearRect(0,0,w,h);

  // outer glow ring
  ctx.beginPath();
  ctx.arc(cx, cy, rBase + 24, 0, Math.PI*2);
  const g1 = ctx.createRadialGradient(cx,cy,rBase, cx,cy,rBase+24);
  g1.addColorStop(0, "rgba(88,177,255,0.05)");
  g1.addColorStop(1, "rgba(88,177,255,0.32)");
  ctx.strokeStyle = g1; ctx.lineWidth = 2; ctx.stroke();

  // main ring
  ctx.beginPath();
  ctx.arc(cx, cy, rBase + rPulse, 0, Math.PI*2);
  const g2 = ctx.createLinearGradient(0,0,w,0);
  g2.addColorStop(0, "rgba(123,255,222,0.9)");
  g2.addColorStop(1, "rgba(88,177,255,0.9)");
  ctx.strokeStyle = g2; ctx.lineWidth = 4; ctx.stroke();

  // ticks
  const ticks = 36;
  for (let i=0;i<ticks;i++){
    const a = (i / ticks) * Math.PI*2;
    const r1 = rBase + rPulse + 6;
    const r2 = r1 + (i % 3 === 0 ? 9 : 5);
    const x1 = cx + Math.cos(a)*r1;
    const y1 = cy + Math.sin(a)*r1;
    const x2 = cx + Math.cos(a)*r2;
    const y2 = cy + Math.sin(a)*r2;
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2);
    ctx.strokeStyle = "rgba(123,255,222,0.4)"; ctx.lineWidth = 1; ctx.stroke();
  }
}

// ===== EQ + Ring from analyser =====
function animateFromAnalyser(){
  if (!analyser) return;
  const arr = new Uint8Array(analyser.frequencyBinCount);
  const loop = () => {
    analyser.getByteFrequencyData(arr);
    // EQ bars
    const take = Math.min(eqBars.length, 16);
    let energy = 0;
    for (let i=0;i<take;i++){
      const v = arr[i] / 255;
      const h = Math.max(6, Math.round(6 + v * 36)); // 6..42px
      eqBars[i].style.height = `${h}px`;
      energy += v;
    }
    const avg = energy / take;
    setAgentSpeaking(avg > 0.12);
    drawRing(avg); // pulse ring with overall energy

    rafId = requestAnimationFrame(loop);
  };
  if (rafId) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(loop);
}

// ===== User STT (single current line) =====
function startUserSTT(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { setYouText("(speech recognition not supported)"); return; }
  rec = new SR();
  rec.lang = "en-US";
  rec.interimResults = true;
  rec.continuous = true;

  rec.onresult = (e)=>{
    let text = "";
    for (let i=e.resultIndex; i<e.results.length; i++){
      const res = e.results[i];
      text = res[0].transcript.trim();
      setYouText(text || " ");
    }
  };
  rec.onerror = ()=>{};
  rec.onend = ()=>{ if (connected) rec.start(); };
  rec.start();
}
function stopUserSTT(){ try{ rec && rec.stop(); }catch{} rec=null; }

// ===== Data Channel Captions =====
function setupDataChannel(pc){
  pc.ondatachannel = (e)=>{
    const ch = e.channel;
    ch.onmessage = (m)=>{
      let text = "";
      try{
        const msg = JSON.parse(m.data);
        text = msg.text || msg.caption || msg.message || "";
      }catch{
        if (typeof m.data === "string") text = m.data;
      }
      if (text){
        receivedDataChannelCaption = true;
        captionsSource = "datachannel"; setCaptionsBadge();
        setAgentText(text);
      }
    };
  };
  // ensure we have an outgoing channel (some stacks want offerer to create one)
  pc.createDataChannel("client");
}

// ===== Server STT Fallback =====
function startAgentSTT(remoteStream, { lang="en", sliceMs=800 } = {}){
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported("audio/webm")){
    if (captionsSource === "none"){ captionsSource = "unavailable"; setCaptionsBadge(); }
    return;
  }
  try{
    agentRec = new MediaRecorder(remoteStream, { mimeType:"audio/webm" });
  }catch(e){
    if (captionsSource === "none"){ captionsSource = "unavailable"; setCaptionsBadge(); }
    return;
  }
  sttAbort = new AbortController();
  agentRec.addEventListener("dataavailable", async (ev)=>{
    if (!ev.data || ev.data.size === 0) return;
    if (receivedDataChannelCaption) return; // data-channel already active; save cost

    const form = new FormData();
    form.append("file", ev.data, `agent-${Date.now()}.webm`);
    form.append("lang", lang);

    try{
      const res = await fetch("/v1/stt/agent", { method:"POST", body:form, signal: sttAbort.signal });
      if (!res.ok) return;
      const json = await res.json();
      if (json && typeof json.text === "string"){
        if (captionsSource === "none"){ captionsSource = "stt"; setCaptionsBadge(); }
        if (!receivedDataChannelCaption){
          setAgentText(json.text || " ");
        }
      }
    }catch(_){ /* ignore transient errors*/ }
  });
  agentRec.start(sliceMs);
  if (captionsSource === "none"){ captionsSource = "stt"; setCaptionsBadge(); }
}
function stopAgentSTT(){
  try{ agentRec && agentRec.stop(); }catch{}
  agentRec = null;
  try{ sttAbort && sttAbort.abort(); }catch{}
  sttAbort = null;
}

// ===== Connect Flow =====
async function connect(){
  if (connected) return;
  statusEl.textContent = "Starting…";
  rtcInfoEl.textContent = "RTC: starting";
  captionsSource = "none"; receivedDataChannelCaption = false; setCaptionsBadge();
  setAgentText("— awaiting response —"); setYouText("— listening —");

  // 1) Token
  let data;
  try{
    const resp = await fetch("/v1/voice/session", {
      method: "POST",
      headers: { "Content-Type":"application/json" },
      body: JSON.stringify({ voice: voiceSel.value, instructions: instructionsInput.value })
    });
    if (!resp.ok) throw new Error(await resp.text());
    data = await resp.json();
  }catch(err){
    setStatus("Failed to get session token: " + err.message);
    rtcInfoEl.textContent = "RTC: error";
    return;
  }
  const clientSecret = data.client_secret?.value || data.client_secret || data.token;
  const rtcUrl = data.rtc_url || data.url || data.web_rtc_url || data.webrtc_url;
  if (!clientSecret || !rtcUrl){
    setStatus("Missing client token or RTC URL.\n" + JSON.stringify(data, null, 2));
    rtcInfoEl.textContent = "RTC: missing config";
    return;
  }

  // 2) WebRTC
  pc = new RTCPeerConnection();
  setupDataChannel(pc);

  remoteAudio = new Audio();
  remoteAudio.autoplay = true;

  pc.ontrack = (e)=>{
    remoteAudio.srcObject = e.streams[0];

    // Analyser => EQ + Ring
    try{
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      remoteSource = audioCtx.createMediaStreamSource(e.streams[0]);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      remoteSource.connect(analyser);
      animateFromAnalyser();
    }catch(err){ console.warn("AudioContext:", err); }

    // Fallback STT
    startAgentSTT(e.streams[0], { lang:"en", sliceMs:800 });
  };

  // Mic
  try{
    localStream = await navigator.mediaDevices.getUserMedia({ audio:true });
  }catch(err){
    setStatus("Mic permission error: " + err.message);
    rtcInfoEl.textContent = "RTC: mic error";
    return;
  }
  localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

  // 3) Offer
  const offer = await pc.createOffer({ offerToReceiveAudio:true, offerToReceiveVideo:false });
  await pc.setLocalDescription(offer);

  // 4) SDP exchange
  try{
    const sdpResp = await fetch(rtcUrl, {
      method: "POST",
      headers: { "Content-Type":"application/sdp", "Authorization": `Bearer ${clientSecret}` },
      body: offer.sdp
    });
    if (!sdpResp.ok) throw new Error(await sdpResp.text());
    const answerSdp = await sdpResp.text();
    await pc.setRemoteDescription({ type:"answer", sdp: answerSdp });
  }catch(err){
    setStatus("RTC SDP exchange failed: " + err.message);
    rtcInfoEl.textContent = "RTC: sdp error";
    return;
  }

  // UI
  connected = true;
  connectBtn.disabled = true;
  disconnectBtn.disabled = false;
  muteBtn.disabled = false;
  unmuteBtn.disabled = true;
  rtcInfoEl.textContent = "RTC: connected";
  setStatus("Connected. Speak into your mic.");

  // Start user live line
  startUserSTT();

  // If data-channel captions kick in later, badge will flip
  setTimeout(()=>{ if (receivedDataChannelCaption) { captionsSource = "datachannel"; setCaptionsBadge(); }}, 2000);
}

// ===== Disconnect / Controls =====
async function disconnect(){
  if (!connected) return;

  try{ pc && pc.close(); }catch{}
  pc = null;

  if (localStream) localStream.getTracks().forEach(t => t.stop());
  localStream = null;

  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
  try{ audioCtx && audioCtx.close(); }catch{}
  audioCtx = null; analyser = null; remoteSource = null;
  setAgentSpeaking(false);

  stopUserSTT();
  stopAgentSTT();

  connected = false;
  connectBtn.disabled = false;
  disconnectBtn.disabled = true;
  muteBtn.disabled = true;
  unmuteBtn.disabled = true;
  rtcInfoEl.textContent = "RTC: disconnected";
  setStatus("Disconnected.");

  setYouText("— awaiting input —");
  setAgentText("— awaiting response —");
  captionsSource = "none"; setCaptionsBadge();
}

function mute(){
  if (!localStream) return;
  localStream.getAudioTracks().forEach(t => t.enabled = false);
  muteBtn.disabled = true; unmuteBtn.disabled = false;
  setStatus("Mic muted.");
}
function unmute(){
  if (!localStream) return;
  localStream.getAudioTracks().forEach(t => t.enabled = true);
  muteBtn.disabled = false; unmuteBtn.disabled = true;
  setStatus("Mic unmuted.");
}

// Wire UI
connectBtn.addEventListener("click", connect);
disconnectBtn.addEventListener("click", disconnect);
muteBtn.addEventListener("click", mute);
unmuteBtn.addEventListener("click", unmute);

// Init ring at idle
drawRing(0.05);
