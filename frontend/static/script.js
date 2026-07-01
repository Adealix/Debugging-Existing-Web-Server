// ─── CHART INSTANCES ─────────────────────────────────────────────────────────
let chart, dbPhTempChart, dbTurbLuxChart, dbHealthDistChart;


// ─── CURRENT MODE STATE ───────────────────────────────────────────────────────
let currentMode = 'AUTO';


// ─── ACTUATOR BUTTON STATE ────────────────────────────────────────────────────
const ACTUATOR_KEYS = ['pump','air_pump','peltier','rgb'];


function updateActuatorButtons(key, state) {
const onBtn  = document.getElementById(`btn-${key}-ON`);
const offBtn = document.getElementById(`btn-${key}-OFF`);
if (!onBtn || !offBtn) return;
const isOn = String(state).toUpperCase() === 'ON';
onBtn.classList.toggle('is-active', isOn);
onBtn.classList.toggle('is-inactive', !isOn);
offBtn.classList.toggle('is-active', !isOn);
offBtn.classList.toggle('is-inactive', isOn);
}
function refreshAllActuatorButtons(data) {


// Do not let incoming MQTT data overwrite manual selections
if (currentMode === 'MANUAL')
    return;


ACTUATOR_KEYS.forEach(k => {
    if (data[k])
        updateActuatorButtons(k, data[k]);
});
}


// ─── PAGE ROUTING ─────────────────────────────────────────────────────────────
let activePage = 'Dashboard';
const pageNames = ['Dashboard','Maranan','Calungsod','Canta','Del Rosario','Garcia','Famini'];


const sensorMap = [
{ key:'ph',          label:'pH Sensor',          icon:'⚗',  unit:'',     pin:'GPIO34', type:'sensor' },
{ key:'turbidity',   label:'Turbidity Sensor',   icon:'🌫️', unit:' NTU', pin:'GPIO35', type:'sensor' },
{ key:'temp',        label:'Temperature Sensor', icon:'🌡️', unit:'°C',   pin:'GPIO32', type:'sensor' },
{ key:'light_lux',   label:'Light Sensor',       icon:'☀️', unit:' lux', pin:'GPIO33', type:'sensor' },
{ key:'distance_cm', label:'Distance Sensor',    icon:'📡', unit:' cm',  pin:'GPIO36', type:'sensor' },
];
const actuatorMap = [
{ key:'pump',        label:'Filter Pump', icon:'🚿', unit:'', pin:'GPIO25', type:'actuator' },
{ key:'air_pump',    label:'Air Pump',    icon:'💨', unit:'', pin:'GPIO26', type:'actuator' },
{ key:'peltier',     label:'Peltier',     icon:'❄️', unit:'', pin:'GPIO14', type:'actuator' },
{ key:'rgb',         label:'RGB Strip',   icon:'🌈', unit:'', pin:'GPIO13', type:'actuator' },
];


function fmtValue(v, suffix='') { return (v===null||v===undefined||v==='') ? '--' : `${Number(v).toFixed(2)}${suffix}`; }
function hashString(s) { let h=0; for(let i=0;i<s.length;i++){h=(h<<5)-h+s.charCodeAt(i);h|=0;} return h; }
function stableShuffle(list, seed) {
return list.slice().sort((a,b)=>{
 const ah=hashString(`${seed}-${a.key}`), bh=hashString(`${seed}-${b.key}`);
 return ah!==bh ? ah-bh : a.key.localeCompare(b.key);
});
}
function getPageItems(page) {
if (page === 'Famini') {
  const ph = sensorMap.find(s => s.key === 'ph');
  const distance = sensorMap.find(s => s.key === 'distance_cm');
  const temp = sensorMap.find(s => s.key === 'temp');
  const pump = actuatorMap.find(a => a.key === 'pump');
  const airPump = actuatorMap.find(a => a.key === 'air_pump');
  const rgb = actuatorMap.find(a => a.key === 'rgb');
  return [ph, pump, distance, airPump, temp, rgb];
}
const s=stableShuffle(sensorMap,   `${page}-sensor`).slice(0,3);
const a=stableShuffle(actuatorMap, `${page}-actuator`).slice(0,3);
return [s[0],a[0],s[1],a[1],s[2],a[2]];
}
function activatePage(page) {
activePage = pageNames.includes(page) ? page : 'Dashboard';
const isDash = activePage === 'Dashboard';
document.getElementById('pageSection').style.display     = isDash ? 'none'  : 'block';
document.getElementById('sensors').style.display         = isDash ? 'grid'  : 'none';
document.getElementById('dashboardLayout').style.display = isDash ? 'grid'  : 'none';
document.getElementById('db-history').style.display      = isDash ? 'block' : 'none';
document.getElementById('logs').style.display            = isDash ? 'block' : 'none';
document.getElementById('greeting').innerText = isDash ? greetText() : `${activePage} View`;
document.querySelector('.hero .sub').innerText = isDash
 ? 'Live monitoring of sensors, actuators, and feed system.'
 : 'Three dashboard-style components for the selected page.';
window.location.hash = encodeURIComponent(activePage);
document.querySelectorAll('.tabs a, .mobile-nav a').forEach(l=>{
 l.classList.toggle('active', decodeURIComponent(l.getAttribute('href').replace('#','')) === activePage);
});
if (!isDash) renderPageCards(activePage);
}
function renderPageCards(page) {
const items = getPageItems(page);
const g = document.getElementById('pageGrid');
g.innerHTML = '';
items.forEach((item,i)=>{
 const c = document.createElement('div'); c.className='page-card';
 if(item.type === "actuator"){


 c.innerHTML = `
   <div>
     <div class="stat-top">
       <div class="stat-icon">${item.icon}</div>
       <div class="seg">${item.label}</div>
     </div>


     <div class="value" id="pageCardValue${i}">--</div>


     <div class="label">${item.label}</div>


     <button
 id="btn-${item.key}-ON"
 onclick="setManualRelay('${item.key}','ON')">
 ON
</button>


<button
 id="btn-${item.key}-OFF"
 onclick="setManualRelay('${item.key}','OFF')">
 OFF
</button>
   </div>


   <div class="pin">${item.pin}</div>
 `;
}


else{


 c.innerHTML = `
   <div>
     <div class="stat-top">
       <div class="stat-icon">${item.icon}</div>
       <div class="seg">${item.label}</div>
     </div>


     <div class="value" id="pageCardValue${i}">--</div>


     <div class="label">${item.label}</div>
   </div>


   <div class="pin">${item.pin}</div>
 `;
}
 g.appendChild(c);
});
document.getElementById('pageSectionTitle').innerText = activePage;
document.getElementById('pageSectionFlag').innerText  = 'Custom page';
document.getElementById('pageSectionSub').innerText   = `Showing ${items.length} components for ${activePage}.`;
}
function applyPageDataToPageSection(data) {
if (activePage==='Dashboard') return;
getPageItems(activePage).forEach((item,i)=>{
 const el=document.getElementById(`pageCardValue${i}`);
 if (!el) return;
 el.innerText=(item.key in data)
   ? (item.type==='sensor' ? fmtValue(data[item.key],item.unit) : (data[item.key]||'--'))
   : '--';
});
}


// ─── MODE UI ──────────────────────────────────────────────────────────────────
function applyModeUI(mode) {
currentMode = mode;

// Do NOT disable buttons — let .manual-zone.locked (pointer-events:none)
// prevent clicks while keeping actuator ON/OFF state clearly visible.

const isAuto   = mode === 'AUTO';
const autoBtn  = document.getElementById('btnModeAuto');
const manBtn   = document.getElementById('btnModeManual');
const zone     = document.getElementById('manualZone');
const badge    = document.getElementById('modeBadge');

autoBtn.classList.toggle('active', isAuto);
manBtn.classList.toggle('active',  !isAuto);
zone.classList.toggle('locked',    isAuto);

badge.innerText = mode;
badge.className = isAuto ? 'badge badge-purple' : 'badge badge-amber';
}


// ─── FORMAT HELPERS ───────────────────────────────────────────────────────────
function onOffChip(val) {
if (!val||val==='--') return `<span class="chip chip-gray">--</span>`;
return String(val).toUpperCase()==='ON'
 ? `<span class="chip chip-green">● ON</span>`
 : `<span class="chip chip-red">● OFF</span>`;
}
function modeChip(val) {
if (!val||val==='--') return `<span class="chip chip-gray">--</span>`;
return String(val).toUpperCase()==='AUTO'
 ? `<span class="chip chip-purple">AUTO</span>`
 : `<span class="chip chip-amber">MANUAL</span>`;
}
function healthChip(val) {
if (val===null||val===undefined) return '--';
const n=Number(val);
const cls=n>=70?'health-good':n>=50?'health-fair':'health-poor';
return `<span class="${cls}">${n}%</span>`;
}
function fmtNum(v,d=2,s='') { return (v===null||v===undefined)?'--':`${Number(v).toFixed(d)}${s}`; }
function fmtTs(rec) {
if (rec.timestamp) { try { return new Date(rec.timestamp).toLocaleString(); } catch(e){} }
return rec.time||'--';
}


// ─── DB TAB ───────────────────────────────────────────────────────────────────
function switchDbTab(tab) {
['sensor','actuator','sms'].forEach(t=>{
 document.getElementById(`dbPanel-${t}`).classList.toggle('active',t===tab);
});
document.querySelectorAll('.tab-switcher button').forEach((btn,i)=>{
 btn.classList.toggle('active',['sensor','actuator','sms'][i]===tab);
});
}


// ─── DB CHARTS ────────────────────────────────────────────────────────────────
function buildDbCharts(sensorHistory) {
const records = [...sensorHistory].reverse();
const labels  = records.map((r,i)=>r.time||String(i+1));
const isMob   = window.innerWidth < 680;
const baseScale = { grid:{display:false}, ticks:{color:'#6b7280',font:{size:9},maxRotation:0,maxTicksLimit:isMob?4:6} };
const baseLegend = { position:'top', labels:{usePointStyle:true,boxWidth:7,color:'#374151',font:{size:10}} };


const ctx1=document.getElementById('dbPhTempChart').getContext('2d');
if (dbPhTempChart) dbPhTempChart.destroy();
dbPhTempChart=new Chart(ctx1,{type:'line',data:{labels,datasets:[
 {label:'pH',      data:records.map(r=>r.ph),   tension:0.3,borderWidth:2,borderColor:'#8b6df8',backgroundColor:'rgba(139,109,248,0.12)',fill:true,yAxisID:'y',  pointRadius:3},
 {label:'Temp °C', data:records.map(r=>r.temp), tension:0.3,borderWidth:2,borderColor:'#22c55e',backgroundColor:'rgba(34,197,94,0.10)', fill:true,yAxisID:'y1', pointRadius:3}
]},options:{maintainAspectRatio:false,responsive:true,interaction:{mode:'index',intersect:false},plugins:{legend:baseLegend},
 scales:{x:baseScale,y:{type:'linear',position:'left',min:0,max:14,ticks:{color:'#6b7280',font:{size:9}},grid:{color:'rgba(17,24,39,0.06)'}},y1:{type:'linear',position:'right',min:0,max:40,ticks:{color:'#6b7280',font:{size:9}},grid:{drawOnChartArea:false}}}}});


const ctx2=document.getElementById('dbTurbLuxChart').getContext('2d');
if (dbTurbLuxChart) dbTurbLuxChart.destroy();
dbTurbLuxChart=new Chart(ctx2,{type:'line',data:{labels,datasets:[
 {label:'Turbidity NTU',data:records.map(r=>r.turbidity), tension:0.3,borderWidth:2,borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,0.12)', fill:true,yAxisID:'y',  pointRadius:3},
 {label:'Light lux',    data:records.map(r=>r.light_lux), tension:0.3,borderWidth:2,borderColor:'#2dd4bf',backgroundColor:'rgba(45,212,191,0.10)',  fill:true,yAxisID:'y1', pointRadius:3}
]},options:{maintainAspectRatio:false,responsive:true,interaction:{mode:'index',intersect:false},plugins:{legend:baseLegend},
 scales:{x:baseScale,y:{type:'linear',position:'left',min:0,ticks:{color:'#6b7280',font:{size:9}},grid:{color:'rgba(17,24,39,0.06)'}},y1:{type:'linear',position:'right',min:0,ticks:{color:'#6b7280',font:{size:9}},grid:{drawOnChartArea:false}}}}});


const ctx3=document.getElementById('dbHealthDistChart').getContext('2d');
if (dbHealthDistChart) dbHealthDistChart.destroy();
dbHealthDistChart=new Chart(ctx3,{type:'line',data:{labels,datasets:[
 {label:'Health %',    data:records.map(r=>r.health),      tension:0.3,borderWidth:2,borderColor:'#6f52ff',backgroundColor:'rgba(111,82,255,0.12)',fill:true,yAxisID:'y',  pointRadius:3},
 {label:'Distance cm', data:records.map(r=>r.distance_cm), tension:0.3,borderWidth:2,borderColor:'#ef4444',backgroundColor:'rgba(239,68,68,0.10)', fill:true,yAxisID:'y1', pointRadius:3}
]},options:{maintainAspectRatio:false,responsive:true,interaction:{mode:'index',intersect:false},plugins:{legend:baseLegend},
 scales:{x:baseScale,y:{type:'linear',position:'left',min:0,max:100,ticks:{color:'#6b7280',font:{size:9}},grid:{color:'rgba(17,24,39,0.06)'}},y1:{type:'linear',position:'right',min:0,ticks:{color:'#6b7280',font:{size:9}},grid:{drawOnChartArea:false}}}}});
}


// ─── TABLE RENDERS ────────────────────────────────────────────────────────────
function renderSensorHistory(records) {
const tbody=document.getElementById('sensorHistoryBody');
if (!records||!records.length){tbody.innerHTML='<tr><td colspan="7" style="color:#6b7280;padding:20px 16px;">No sensor records in MongoDB yet.</td></tr>';return;}
tbody.innerHTML=records.map(r=>`<tr>
 <td style="white-space:nowrap;font-size:11px;">${fmtTs(r)}</td>
 <td>${fmtNum(r.ph)}</td><td>${fmtNum(r.temp)}</td><td>${fmtNum(r.turbidity)}</td>
 <td>${fmtNum(r.light_lux)}</td><td>${fmtNum(r.distance_cm)}</td><td>${healthChip(r.health)}</td></tr>`).join('');
document.getElementById('sensorHistoryFooter').innerText=`${records.length} record${records.length!==1?'s':''} · FIFO cap: 10 · sensor_history`;
}
function renderActuatorHistory(records) {
const tbody=document.getElementById('actuatorHistoryBody');
if (!records||!records.length){tbody.innerHTML='<tr><td colspan="9" style="color:#6b7280;padding:20px 16px;">No actuator records in MongoDB yet.</td></tr>';return;}
tbody.innerHTML=records.map(r=>`<tr>
 <td style="white-space:nowrap;font-size:11px;">${fmtTs(r)}</td>
 <td>${modeChip(r.mode)}</td><td>${onOffChip(r.pump)}</td><td>${onOffChip(r.air_pump)}</td>
 <td>${onOffChip(r.filter_pump)}</td><td>${onOffChip(r.peltier)}</td><td>${onOffChip(r.rgb)}</td>
 <td>${r.rgb_color?`<span class="chip chip-blue">${r.rgb_color}</span>`:'--'}</td>
 <td>${(r.rgb_brightness!==null&&r.rgb_brightness!==undefined)?r.rgb_brightness+'%':'--'}</td></tr>`).join('');
}
function renderSmsHistory(records) {
const tbody=document.getElementById('smsHistoryBody');
if (!records||!records.length){tbody.innerHTML='<tr><td colspan="3" style="color:#6b7280;padding:20px 16px;">No SMS/GSM events in MongoDB yet.</td></tr>';return;}
tbody.innerHTML=records.map(r=>`<tr>
 <td style="white-space:nowrap;font-size:11px;">${fmtTs(r)}</td>
 <td><span class="chip chip-blue">${r.status||'--'}</span></td>
 <td style="font-family:ui-monospace,monospace;font-size:11px;color:#4b5563;max-width:320px;overflow-wrap:break-word;">${r.detail||'--'}</td></tr>`).join('');
}


// ─── LIVE SENSOR TREND CHART ──────────────────────────────────────────────────
function buildChart(labels, phSeries, tempSeries, turbSeries) {
const ctx=document.getElementById('sensorChart').getContext('2d');
if (chart) chart.destroy();
const isMob=window.innerWidth<680;
chart=new Chart(ctx,{type:'line',data:{labels,datasets:[
 {label:'pH',          data:phSeries,   tension:0.3,borderWidth:2.5,borderColor:'#8b6df8',backgroundColor:'rgba(139,109,248,0.14)',fill:true,yAxisID:'y',  pointRadius:isMob?2:3},
 {label:'Temperature', data:tempSeries, tension:0.3,borderWidth:2.5,borderColor:'#22c55e',backgroundColor:'rgba(34,197,94,0.12)',  fill:true,yAxisID:'y1', pointRadius:isMob?2:3},
 {label:'Turbidity',   data:turbSeries, tension:0.3,borderWidth:2.5,borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,0.12)', fill:true,yAxisID:'y2', pointRadius:isMob?2:3}
]},options:{maintainAspectRatio:false,responsive:true,interaction:{mode:'index',intersect:false},
 plugins:{legend:{position:'top',labels:{usePointStyle:true,boxWidth:8,color:'#374151',font:{size:isMob?11:12}}}},
 scales:{
   x:{grid:{display:false},ticks:{color:'#6b7280',font:{size:isMob?9:11},maxRotation:0,maxTicksLimit:isMob?4:8}},
   y:{type:'linear',position:'left',min:0,max:14,ticks:{color:'#6b7280',font:{size:isMob?9:11}},grid:{color:'rgba(17,24,39,0.06)'}},
   y1:{type:'linear',position:'right',min:0,max:40,ticks:{color:'#6b7280',font:{size:isMob?9:11}},grid:{drawOnChartArea:false}},
   y2:{type:'linear',position:'right',min:0,max:200,ticks:{display:false},grid:{drawOnChartArea:false}}
 }}});
}


// ─── CONTROLS ─────────────────────────────────────────────────────────────────
function sendControl(payload) {
return fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
 .then(r=>r.json())
 .then(d=>{ if(d.state) applyState(d.state); })
 .catch(console.warn);
}


function setMode(m) {
applyModeUI(m);          // instant UI feedback
sendControl({mode:m});
}
function setManualRelay(f, v) {
 // force MANUAL mode on first user interaction
 if (currentMode !== 'MANUAL') {
   setMode('MANUAL');
 }

 console.log("SENDING:", f, v);
 
 updateActuatorButtons(f, v); // immediate UI feedback
 sendControl({ [f]: v, mode: 'MANUAL' });
}


function setRgbColour(c) {
document.querySelectorAll('#colourGrid .btn-colour').forEach(b=>{
 b.classList.toggle('active', b.dataset.colour===c);
});
sendControl({rgb_color:c});
}
function setRgbBrightness(l) { sendControl({rgb_brightness:Number(l)}); }
function controlStepper(d,s) { sendControl({stepper_rotate:true,stepper_direction:d,stepper_rotations:s}); }
function sendGsmAlert() { sendControl({gsm_alert:'TEST_ALERT'}); }


// ─── APPLY STATE FROM SERVER ──────────────────────────────────────────────────
function applyState(data) {
document.getElementById('phValue').innerText        = fmtValue(data.ph,'');
document.getElementById('tempValue').innerText      = fmtValue(data.temp,'°C');
document.getElementById('luxValue').innerText       = fmtValue(data.light_lux,' lux');
document.getElementById('turbidityValue').innerText = fmtValue(data.turbidity,' NTU');
document.getElementById('distanceValue').innerText  = fmtValue(data.distance_cm,' cm');
document.getElementById('cameraValue').innerText    = data.camera_connected ? `${data.detection?.count||0} detections` : 'Offline';
document.getElementById('rgbValue').innerText       = data.rgb||'--';
document.getElementById('rgbColor').innerText       = data.rgb_color||'--';
document.getElementById('gsmStatus').innerText      = data.gsm_status||'--';
document.getElementById('connStatus').innerText     = `Connection: ${data.connection_source||'Offline'}`;
document.getElementById('serialStatus').innerText   = data.serial_connected ? 'Connected' : 'Disconnected';
document.getElementById('mqttStatus').innerText     = data.mqtt_connected   ? 'Connected' : 'Disconnected';
document.getElementById('rawLine').innerText        = data.serial_raw||'No ESP32 data yet';


// Camera badge
const camBadge=document.getElementById('cameraBadge');
camBadge.innerText=data.camera_connected?'LIVE':'OFFLINE';
camBadge.className=data.camera_connected?'badge badge-green':'badge badge-red';


document.getElementById('cameraText').innerText = data.camera_connected
 ? (data.detection?.last_error?`Stream active. Detection error: ${data.detection.last_error}`:`Stream active. Crayfish detected: ${data.detection?.count||0}`)
 : (data.camera_error?`Camera unavailable: ${data.camera_error}`:'Camera stream not ready yet.');


const health = data.health||0;
document.getElementById('healthPct').innerText   = `${health}%`;
document.getElementById('healthLabel').innerText = `${health}%`;


const updated = data.updated_at ? new Date(data.updated_at*1000) : null;
document.getElementById('updatedAt').innerText = updated
 ? updated.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '--';


// Sync brightness slider if it differs from server value
if (data.rgb_brightness!==undefined) {
 const slider=document.getElementById('rgbSlider');
 if (document.activeElement!==slider) {
   slider.value=data.rgb_brightness;
   document.getElementById('brightnessVal').innerText=`${data.rgb_brightness}%`;
 }
}


// Sync active colour swatch
if (data.rgb_color) {
 document.querySelectorAll('#colourGrid .btn-colour').forEach(b=>{
   b.classList.toggle('active', b.dataset.colour===data.rgb_color);
 });
}

if (currentMode !== 'MANUAL') {
  refreshAllActuatorButtons(data);
}


// Mode UI — only update if server disagrees with our local state
const serverMode = (data.mode || 'AUTO').toUpperCase();
if (serverMode !== currentMode && currentMode !== 'MANUAL') {
   applyModeUI(serverMode);
}


}




// ─── MAIN REFRESH ─────────────────────────────────────────────────────────────
async function refreshData() {
try {
 const res  = await fetch('/api/data');
 const data = await res.json();
 applyState(data);


 // DB history
 const hist          = data.history          || [];
 const sensorHist    = data.sensor_history    || [];
 const actuatorHist  = data.actuator_history  || [];
 const smsHist       = data.sms_history       || [];


 // Live trend chart + DB charts
 const labels = hist.map((r,i)=> r.time || String(i+1));
 buildChart(labels, hist.map(r=>r.ph), hist.map(r=>r.temp), hist.map(r=>r.turbidity));
 buildDbCharts(sensorHist);


 renderSensorHistory(sensorHist.slice(-10));
 renderActuatorHistory(actuatorHist.slice(-10));
 renderSmsHistory(smsHist.slice(-10));


 applyPageDataToPageSection(data);


 // Live events
 const tbody=document.getElementById('eventsBody');
 tbody.innerHTML='';
 if (!data.events?.length) {
   tbody.innerHTML='<tr><td colspan="4" style="color:#6b7280;">No activity yet.</td></tr>';
 } else {
   data.events.slice(0,10).forEach(ev=>{
     const tr=document.createElement('tr');
     tr.innerHTML=`<td>${ev.time||'--'}</td><td><span class="kind ${ev.kind||'serial'}">${(ev.kind||'serial').toUpperCase()}</span></td><td>${ev.title||'--'}</td><td>${ev.detail||'--'}</td>`;
     tbody.appendChild(tr);
   });
 }
} catch(e) { console.warn('Refresh error:',e); }
}


// ─── CLOCK ────────────────────────────────────────────────────────────────────
function tickClock() { document.getElementById('clock').innerText=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}); }


function greetText() {
const h=new Date().getHours();
return h<12?'Good morning, Operator!':h<18?'Good afternoon, Operator!':'Good evening, Operator!';
}
function greetByTime() { if(activePage==='Dashboard') document.getElementById('greeting').innerText=greetText(); }


// ─── INIT ─────────────────────────────────────────────────────────────────────
const startPage = decodeURIComponent(window.location.hash.replace('#',''));
activatePage(pageNames.includes(startPage) ? startPage : 'Dashboard');
greetByTime();
tickClock();
refreshData();
setInterval(refreshData, 2000);  // Poll every 2 seconds (was 1s — reduces server load 50%)
setInterval(tickClock,   1000);








