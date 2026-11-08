// Source JSX for the ERP SPA. Precompiled to app.js (see build-ui.sh).
const {useState, useEffect, useCallback} = React;
const R = window.Recharts || null;

// ---- api helper ----------------------------------------------------------
const TOKEN_KEY = "erp_token";
function getToken(){ try{ return localStorage.getItem(TOKEN_KEY) }catch(e){ return null } }
function setToken(t){ try{ t?localStorage.setItem(TOKEN_KEY,t):localStorage.removeItem(TOKEN_KEY) }catch(e){} }

async function api(path, {method, body, form, blob}={}){
  if(!method) method = (form || body!==undefined) ? "POST" : "GET";
  const headers={}; const tok=getToken(); if(tok) headers["Authorization"]="Bearer "+tok;
  let payload;
  if(form){ payload=new URLSearchParams(form); headers["Content-Type"]="application/x-www-form-urlencoded"; }
  else if(body!==undefined){ payload=JSON.stringify(body); headers["Content-Type"]="application/json"; }
  const res=await fetch(path,{method,headers,body:payload});
  if(res.status===401){ setToken(null); location.reload(); throw new Error("Unauthorized"); }
  if(blob){ if(!res.ok) throw new Error("Download failed"); return await res.blob(); }
  const ct=res.headers.get("content-type")||"";
  const data= ct.includes("json") ? await res.json() : await res.text();
  if(!res.ok) throw new Error((data && data.detail) || ("Error "+res.status));
  return data;
}
const inr = n => Number(n||0).toLocaleString("en-IN",{style:"currency",currency:"INR",maximumFractionDigits:0});
const inr2 = n => Number(n||0).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});

// ---- small components -----------------------------------------------------
function Kpi({label, value, cls}){ return (
  <div className="card"><div className="label">{label}</div>
    <div className={"value "+(cls||"")}>{value}</div></div> ); }

// Richer KPI card: coloured icon chip + value + optional % delta.
function Stat({icon, label, value, delta, tone}){
  const d = (delta===undefined||delta===null) ? null : Number(delta);
  return (<div className={"stat "+(tone||"")}>
    <div className="stat-icon">{icon}</div>
    <div className="stat-body">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {d!==null && <div className={"stat-delta "+(d>=0?"up":"down")}>
        {d>=0?"▲":"▼"} {Math.abs(d).toFixed(1)}% vs last month</div>}
    </div>
  </div>);
}

function Panel({title, children}){ return (
  <div className="panel">{title && <h3>{title}</h3>}{children}</div> ); }

// ---- login ----------------------------------------------------------------
function Login({onLogin}){
  const [u,setU]=useState("admin"); const [p,setP]=useState("admin123"); const [err,setErr]=useState("");
  const submit=async e=>{ e.preventDefault(); setErr("");
    try{ const r=await api("/api/auth/login",{form:{username:u,password:p}}); setToken(r.access_token); onLogin(); }
    catch(ex){ setErr("Invalid username or password"); } };
  return (<div className="login"><form className="box" onSubmit={submit}>
    <h2>Textile ERP</h2><p>Integrated Knitting &amp; Dyeing ERP</p>
    <div className="field"><label>Username</label><input value={u} onChange={e=>setU(e.target.value)} /></div>
    <div className="field"><label>Password</label><input type="password" value={p} onChange={e=>setP(e.target.value)} /></div>
    <button className="btn" style={{width:"100%"}}>Sign in</button>
    {err && <div className="err">{err}</div>}
    <p className="muted" style={{marginTop:16,fontSize:12}}>Demo: admin / admin123</p>
  </form></div>);
}

// ---- data hook ------------------------------------------------------------
function useData(path){
  const [d,setD]=useState(null); const [err,setErr]=useState("");
  useEffect(()=>{ let on=true; api(path).then(r=>on&&setD(r)).catch(e=>on&&setErr(e.message));
    return ()=>{on=false}; },[path]);
  return [d,err];
}
// loads a lookup list once; returns [] until ready
function useList(path){
  const [d,setD]=useState([]);
  useEffect(()=>{ let on=true; api(path).then(r=>on&&setD(r||[])).catch(()=>{}); return ()=>{on=false}; },[path]);
  return d;
}
function Opts({items, value, onChange, labelKey="label", valueKey="id", placeholder="Select…"}){
  return (<select value={value} onChange={onChange}>
    <option value="">{placeholder}</option>
    {items.map(o=><option key={o[valueKey]} value={o[valueKey]}>{o[labelKey]||o.name||o.code}</option>)}
  </select>);
}
function Result({r}){ return r ? <div className="panel" style={{borderColor:"#bbf7d0",background:"#f0fdf4"}}>
  <b>Posted.</b> {Object.entries(r).map(([k,v])=><span key={k} style={{marginRight:16}}>
    <span className="muted">{k}:</span> {String(v)}</span>)}</div> : null; }
const genNo = pfx => pfx+"-"+Date.now().toString().slice(-8);

const DONUT=["#2563eb","#16a34a","#f59e0b","#7c3aed","#0d9488","#e11d48","#64748b"];

function OwnerDash(){
  const [d,err]=useData("/api/dashboard/owner");
  const trend=useList("/api/dashboard/production-trend");
  const chem=useData("/api/dashboard/chemical")[0];
  const util=useList("/api/hardware/knitting-summary");
  const batches=useList("/api/dyeing/batches");
  const acts=useList("/api/vouchers");
  if(err) return <Panel>{err}</Panel>; if(!d) return <Panel>Loading…</Panel>;
  const dl=d.deltas||{};
  const mix=((chem&&chem.top_consumption)||[]).map((m,i)=>({name:m.material, value:Number(m.cost), c:DONUT[i%DONUT.length]}));
  const mixTotal=mix.reduce((s,m)=>s+m.value,0);
  const alerts=(chem&&chem.stock_alerts)||[];
  const dayShort=s=>s?s.slice(5):"";
  return (<div>
    <div className="stats">
      <Stat tone="" icon="₹" label="Revenue (YTD)" value={inr(d.revenue)} delta={dl.revenue} />
      <Stat tone="green" icon="📈" label="Gross Profit" value={inr(d.gross_profit)} delta={dl.gross_profit} />
      <Stat tone="violet" icon="％" label="Net Profit" value={inr(d.net_profit)} delta={dl.net_profit} />
      <Stat tone="teal" icon="🏦" label="Cash & Bank" value={inr(d.cash_and_bank)} />
      <Stat tone="amber" icon="📥" label="Receivables" value={inr(d.receivables)} />
      <Stat tone="rose" icon="📤" label="Payables" value={inr(d.payables)} />
    </div>
    <div className="grid2">
      <Panel title="Production vs dyeing output (last 14 days)">
        {R ? <div className="chartbox"><R.ResponsiveContainer>
          <R.BarChart data={(trend||[]).map(p=>({d:dayShort(p.date), Knitting:Number(p.knitting_kg), Dyeing:Number(p.dyeing_kg)}))}
            margin={{top:10,right:16,left:0,bottom:0}}>
            <R.CartesianGrid strokeDasharray="3 3" stroke="#eef2f7"/>
            <R.XAxis dataKey="d" fontSize={11}/><R.YAxis fontSize={11}/>
            <R.Tooltip formatter={v=>inr2(v)+" kg"}/><R.Legend/>
            <R.Bar dataKey="Knitting" stackId="a" fill="#2563eb" radius={[0,0,0,0]}/>
            <R.Bar dataKey="Dyeing" stackId="a" fill="#16a34a" radius={[4,4,0,0]}/>
          </R.BarChart></R.ResponsiveContainer></div> : <div className="muted">Chart unavailable</div>}
      </Panel>
      <Panel title="Chemical consumption mix">
        {mix.length===0?<div className="muted">No consumption yet.</div>:<div>
          {R && <div style={{height:170}}><R.ResponsiveContainer>
            <R.PieChart><R.Pie data={mix} dataKey="value" nameKey="name" innerRadius={48} outerRadius={70} paddingAngle={2}>
              {mix.map((m,i)=><R.Cell key={i} fill={m.c}/>)}</R.Pie><R.Tooltip formatter={v=>inr(v)}/></R.PieChart>
          </R.ResponsiveContainer></div>}
          <div className="legend">{mix.map((m,i)=><div className="lr" key={i}>
            <span className="dot" style={{background:m.c}}></span><span style={{flex:1}}>{m.name}</span>
            <span className="muted">{mixTotal?((m.value/mixTotal*100).toFixed(0)):0}%</span></div>)}</div>
        </div>}
      </Panel>
    </div>
    <div className="grid-half">
      <Panel title="Active dyeing batches">
        {(batches||[]).length===0?<div className="muted">No batches yet.</div>:
        <table><thead><tr><th>Batch</th><th className="num">Fabric (kg)</th><th className="num">Rolls</th><th>Status</th></tr></thead>
        <tbody>{batches.slice(0,6).map(b=><tr key={b.id}><td>{b.batch_code}</td><td className="num">{inr2(b.fabric_kg)}</td>
          <td className="num">{b.rolls}</td><td>{stBadge(b.status)}</td></tr>)}</tbody></table>}
      </Panel>
      <Panel title="Machine utilization">
        {(util||[]).length===0?<div className="muted">No machine telemetry yet — simulate on Devices &amp; Labels.</div>:
        util.map((m,i)=>{ const pc=Math.min(100,Number(m.efficiency_pct||0)); return <div className="util-row" key={i}>
          <span className="nm">{m.machine}</span><span className="util-bar"><span className="util-fill"
            style={{width:pc+"%", background:pc>=80?"#16a34a":pc>=50?"#2563eb":"#f59e0b"}}></span></span>
          <span className="pc">{pc.toFixed(0)}%</span></div>; })}
      </Panel>
    </div>
    <div className="grid-half">
      <Panel title="Inventory alerts">
        {alerts.length===0?<div className="muted">All stock above reorder level.</div>:
        <table><thead><tr><th>Item</th><th className="num">On hand</th><th className="num">Reorder</th><th>Status</th></tr></thead>
        <tbody>{alerts.map((a,i)=><tr key={i}><td>{a.name}</td><td className="num">{inr2(a.on_hand)}</td>
          <td className="num">{inr2(a.reorder_level)}</td><td><span className="badge bad">Low stock</span></td></tr>)}</tbody></table>}
      </Panel>
      <Panel title="Recent activity">
        {(acts||[]).length===0?<div className="muted">No postings yet.</div>:
        acts.slice(0,8).map((a,i)=><div className="act" key={i}>
          <span className="tt">{a.voucher_date?a.voucher_date.slice(5):""}</span>
          <span style={{flex:1}}><b>{a.number}</b> — {a.narration||"—"}</span>
          <span className="num" style={{width:90}}>{inr2(a.total_debit)}</span>
          <span className="badge info">{a.status}</span></div>)}
      </Panel>
    </div>
  </div>);
}
function SalesTable({rows}){ return (<table><thead><tr><th>Month</th><th className="num">Sales</th></tr></thead>
  <tbody>{(rows||[]).map((r,i)=><tr key={i}><td>{r.month}</td><td className="num">{inr2(r.amount)}</td></tr>)}</tbody></table>); }

function FactoryDash(){
  const [d,err]=useData("/api/dashboard/factory");
  const trend=useList("/api/dashboard/production-trend");
  const util=useList("/api/hardware/knitting-summary");
  const batches=useList("/api/dyeing/batches");
  if(err) return <Panel>{err}</Panel>; if(!d) return <Panel>Loading…</Panel>;
  const dayShort=s=>s?s.slice(5):"";
  return (<div>
    <div className="stats">
      <Stat tone="" icon="🧶" label="Knitting output (kg)" value={inr2(d.knitting_output_kg)} />
      <Stat tone="violet" icon="🎨" label="Dyeing output (kg)" value={inr2(d.dyeing_output_kg)} />
      <Stat tone="amber" icon="⚙️" label="Active batches" value={d.active_batches} />
      <Stat tone="teal" icon="🧪" label="Chemical consumption" value={inr(d.chemical_consumption_cost)} />
    </div>
    <Panel title="Production vs dyeing output (last 14 days)">
      {R ? <div className="chartbox"><R.ResponsiveContainer>
        <R.BarChart data={(trend||[]).map(p=>({d:dayShort(p.date), Knitting:Number(p.knitting_kg), Dyeing:Number(p.dyeing_kg)}))}
          margin={{top:10,right:16,left:0,bottom:0}}>
          <R.CartesianGrid strokeDasharray="3 3" stroke="#eef2f7"/>
          <R.XAxis dataKey="d" fontSize={11}/><R.YAxis fontSize={11}/>
          <R.Tooltip formatter={v=>inr2(v)+" kg"}/><R.Legend/>
          <R.Bar dataKey="Knitting" stackId="a" fill="#2563eb"/><R.Bar dataKey="Dyeing" stackId="a" fill="#16a34a" radius={[4,4,0,0]}/>
        </R.BarChart></R.ResponsiveContainer></div> : <div className="muted">Chart unavailable</div>}
    </Panel>
    <div className="grid-half">
      <Panel title="Machine utilization">
        {(util||[]).length===0?<div className="muted">No machine telemetry yet — simulate on Devices &amp; Labels.</div>:
        util.map((m,i)=>{ const pc=Math.min(100,Number(m.efficiency_pct||0)); return <div className="util-row" key={i}>
          <span className="nm">{m.machine}</span><span className="util-bar"><span className="util-fill"
            style={{width:pc+"%", background:pc>=80?"#16a34a":pc>=50?"#2563eb":"#f59e0b"}}></span></span>
          <span className="pc">{pc.toFixed(0)}%</span></div>; })}
      </Panel>
      <Panel title="Active dyeing batches">
        {(batches||[]).length===0?<div className="muted">No batches yet.</div>:
        <table><thead><tr><th>Batch</th><th className="num">Fabric (kg)</th><th className="num">Rolls</th><th>Status</th></tr></thead>
        <tbody>{batches.slice(0,8).map(b=><tr key={b.id}><td>{b.batch_code}</td><td className="num">{inr2(b.fabric_kg)}</td>
          <td className="num">{b.rolls}</td><td>{stBadge(b.status)}</td></tr>)}</tbody></table>}
      </Panel>
    </div>
  </div>);
}

function AccountsDash(){
  const [d,err]=useData("/api/dashboard/accounts");
  const acts=useList("/api/vouchers");
  if(err) return <Panel>{err}</Panel>; if(!d) return <Panel>Loading…</Panel>;
  return (<div>
    <div className="stats">
      <Stat tone="green" icon="📥" label="Today's receipts" value={inr(d.today_receipts)} />
      <Stat tone="rose" icon="📤" label="Today's payments" value={inr(d.today_payments)} />
      <Stat tone="amber" icon="👤" label="Debtors outstanding" value={inr(d.debtors_total)} />
      <Stat tone="violet" icon="🏢" label="Creditors outstanding" value={inr(d.creditors_total)} />
      <Stat tone="teal" icon="🧾" label="GST net payable" value={inr(d.gst_net_payable)} />
      <Stat tone="" icon={d.trial_balance_balanced?"✅":"⚠️"} label="Trial balance"
        value={<span className={"badge "+(d.trial_balance_balanced?"ok":"bad")}>{d.trial_balance_balanced?"Balanced":"Out of balance"}</span>} />
    </div>
    <div className="grid-half">
      <Panel title="Bank balances">
        {(d.bank_balances||[]).length===0?<div className="muted">No bank accounts yet.</div>:
        <table><thead><tr><th>Account</th><th className="num">Balance</th></tr></thead>
        <tbody>{(d.bank_balances||[]).map((b,i)=><tr key={i}><td>{b.name}</td>
          <td className="num">{inr2(b.balance)}</td></tr>)}</tbody></table>}
      </Panel>
      <Panel title="Recent vouchers">
        {(acts||[]).length===0?<div className="muted">No postings yet.</div>:
        acts.slice(0,8).map((a,i)=><div className="act" key={i}>
          <span className="tt">{a.voucher_date?a.voucher_date.slice(5):""}</span>
          <span style={{flex:1}}><b>{a.number}</b> — {a.narration||"—"}</span>
          <span className="num" style={{width:90}}>{inr2(a.total_debit)}</span>
          <span className="badge info">{a.status}</span></div>)}
      </Panel>
    </div>
  </div>);
}

function ChemicalDash(){
  const [d,err]=useData("/api/dashboard/chemical");
  if(err) return <Panel>{err}</Panel>; if(!d) return <Panel>Loading…</Panel>;
  const bars=(d.top_consumption||[]).map(r=>({material:r.material, cost:Number(r.cost)}));
  return (<div>
    <div className="stats">
      <Stat tone="violet" icon="🎨" label="Dye cost" value={inr(d.dye_cost)} />
      <Stat tone="teal" icon="🧪" label="Chemical cost" value={inr(d.chemical_cost)} />
      <Stat tone="" icon="Σ" label="Total consumption" value={inr(d.total_consumption_cost)} />
      <Stat tone={d.dispensing_errors?"rose":"green"} icon={d.dispensing_errors?"⚠️":"✅"} label="Dispensing errors" value={d.dispensing_errors} />
    </div>
    <Panel title="Top consumption by material">
      {R ? <div className="chartbox"><R.ResponsiveContainer>
        <R.BarChart data={bars} margin={{top:10,right:20,left:10,bottom:0}}>
          <R.CartesianGrid strokeDasharray="3 3" stroke="#eef2f7"/>
          <R.XAxis dataKey="material" fontSize={12}/><R.YAxis fontSize={12}/>
          <R.Tooltip formatter={v=>inr(v)}/><R.Bar dataKey="cost" fill="#2563eb" radius={[4,4,0,0]}/>
        </R.BarChart></R.ResponsiveContainer></div>
        : <table><tbody>{bars.map((b,i)=><tr key={i}><td>{b.material}</td>
          <td className="num">{inr2(b.cost)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Stock alerts">
      {(d.stock_alerts||[]).length===0 ? <div className="muted">No chemicals below reorder level.</div> :
      <table><thead><tr><th>Code</th><th>Name</th><th className="num">On hand</th><th className="num">Reorder</th></tr></thead>
      <tbody>{d.stock_alerts.map((a,i)=><tr key={i}><td>{a.code}</td><td>{a.name}</td>
        <td className="num">{inr2(a.on_hand)}</td><td className="num">{inr2(a.reorder_level)}</td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- masters: warehouses --------------------------------------------------
function Warehouses(){
  const [rows,setRows]=useState(null); const [err,setErr]=useState("");
  const [f,setF]=useState({code:"",name:"",kind:"STORE"});
  const load=useCallback(()=>api("/api/masters/warehouses").then(setRows).catch(e=>setErr(e.message)),[]);
  useEffect(()=>{load()},[load]);
  const add=async e=>{ e.preventDefault(); setErr("");
    try{ await api("/api/masters/warehouses",{method:"POST",body:f});
      setF({code:"",name:"",kind:"STORE"}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Add warehouse">
      <form className="row" onSubmit={add}>
        <div className="field"><label>Code</label>
          <input required value={f.code} onChange={e=>setF({...f,code:e.target.value})} placeholder="WH-XYZ"/></div>
        <div className="field"><label>Name</label>
          <input required value={f.name} onChange={e=>setF({...f,name:e.target.value})} placeholder="Store name"/></div>
        <div className="field"><label>Kind</label>
          <select value={f.kind} onChange={e=>setF({...f,kind:e.target.value})}>
            <option>STORE</option><option>GREIGE</option><option>FG</option><option>DISPENSING_TANK</option>
          </select></div>
        <button className="btn">Add warehouse</button>
      </form>
      {err && <div className="err">{err}</div>}
    </Panel>
    <Panel title="Warehouses">
      {!rows ? "Loading…" :
      <table><thead><tr><th>Code</th><th>Name</th><th>Kind</th><th>Status</th></tr></thead>
      <tbody>{rows.map(w=><tr key={w.id}><td>{w.code}</td><td>{w.name}</td><td>{w.kind}</td>
        <td><span className={"badge "+(w.is_active?"ok":"bad")}>{w.is_active?"Active":"Inactive"}</span></td></tr>)}
      </tbody></table>}
    </Panel>
  </div>);
}

// ---- reports (authenticated downloads) ------------------------------------
const REPORTS=[
  ["trial-balance","Trial Balance"],["profit-and-loss","Profit & Loss"],["balance-sheet","Balance Sheet"],
  ["cash-flow","Cash Flow"],["debtors-ageing","Debtors Ageing"],["creditors-ageing","Creditors Ageing"],
  ["gst-sales-register","GST Sales Register"],["gst-purchase-register","GST Purchase Register"],
  ["gstr3b","GSTR-3B"],["stock-reconciliation","Stock vs GL Reconciliation"],
];
function FinalAccounts(){
  const mfg=useData("/api/reports/manufacturing-account")[0];
  const trd=useData("/api/reports/trading-account")[0];
  const ff=useData("/api/reports/funds-flow")[0];
  const line=(l,v,b)=> <tr><td style={b?{fontWeight:700}:{}}>{l}</td><td className="num" style={b?{fontWeight:700}:{}}>{inr2(v)}</td></tr>;
  return (<div className="grid3">
    <Panel title="Manufacturing account">
      {!mfg?<div className="muted">…</div>:<table><tbody>
        {line("Opening WIP",mfg.opening_wip)}
        {line("Charged to production",mfg.charged_to_production)}
        {line("Less: closing WIP",mfg.closing_wip)}
        {line("Cost of goods manufactured",mfg.cost_of_goods_manufactured,true)}
      </tbody></table>}
    </Panel>
    <Panel title="Trading account">
      {!trd?<div className="muted">…</div>:<table><tbody>
        {line("Sales",trd.sales)}
        {line("Opening stock",trd.opening_stock)}
        {line("Cost of goods manufactured",trd.cost_of_goods_manufactured)}
        {line("Less: closing stock",trd.closing_stock)}
        {line("Cost of goods sold",trd.cost_of_goods_sold)}
        {line("Gross profit",trd.gross_profit,true)}
      </tbody></table>}
    </Panel>
    <Panel title="Funds flow statement">
      {!ff?<div className="muted">…</div>:<table><tbody>
        {line("Funds from operations",ff.funds_from_operations)}
        <tr><td colSpan="2" className="muted" style={{paddingTop:10}}>Sources</td></tr>
        {(ff.sources||[]).map((s,i)=><tr key={"s"+i}><td>· {s.item}</td><td className="num">{inr2(s.amount)}</td></tr>)}
        {line("Total sources",ff.total_sources,true)}
        <tr><td colSpan="2" className="muted" style={{paddingTop:10}}>Applications</td></tr>
        {(ff.applications||[]).map((a,i)=><tr key={"a"+i}><td>· {a.item}</td><td className="num">{inr2(a.amount)}</td></tr>)}
        {line("Total applications",ff.total_applications,true)}
        {line("Net change in working capital",ff.net_working_capital_change,true)}
      </tbody></table>}
    </Panel>
  </div>);
}

function Reports(){
  const [fmt,setFmt]=useState("xlsx"); const [busy,setBusy]=useState("");
  const dl=async(rep)=>{ setBusy(rep);
    try{ const blob=await api(`/api/reports/export/${rep}?format=${fmt}`,{blob:true});
      const url=URL.createObjectURL(blob); const a=document.createElement("a");
      a.href=url; a.download=`${rep}.${fmt}`; a.click(); URL.revokeObjectURL(url); }
    catch(e){ alert(e.message); } finally{ setBusy(""); } };
  return (<div>
    <FinalAccounts/>
    <Panel title="Download reports">
    <div className="row mb"><div className="field"><label>Format</label>
      <select value={fmt} onChange={e=>setFmt(e.target.value)}><option value="xlsx">Excel</option>
      <option value="pdf">PDF</option></select></div></div>
    <table><thead><tr><th>Report</th><th></th></tr></thead><tbody>
      {REPORTS.map(([id,label])=><tr key={id}><td>{label}</td>
        <td className="num"><button className="btn sec" disabled={busy===id}
          onClick={()=>dl(id)}>{busy===id?"…":"Download"}</button></td></tr>)}
    </tbody></table>
  </Panel>
  </div>);
}

// ---- quality --------------------------------------------------------------
const RESULT_CLS={ACCEPT:"ok",REWORK:"warn",REJECT:"bad"};
function Quality(){
  const [sum,setSum]=useState(null); const [insp,setInsp]=useState([]); const [comp,setComp]=useState([]);
  const [err,setErr]=useState("");
  const [f,setF]=useState({delta_e:"",shade_match:true,inspected_qty:"",rejection_qty:"",colour_fastness:""});
  const load=useCallback(()=>{
    api("/api/quality/summary").then(setSum).catch(e=>setErr(e.message));
    api("/api/quality/inspections").then(setInsp).catch(()=>{});
    api("/api/quality/complaints").then(setComp).catch(()=>{});
  },[]);
  useEffect(()=>{load()},[load]);
  const add=async e=>{ e.preventDefault(); setErr("");
    const body={inspection_date:new Date().toISOString().slice(0,10),
      delta_e:f.delta_e||null, shade_match:f.shade_match,
      inspected_qty:f.inspected_qty||null, rejection_qty:f.rejection_qty||null,
      colour_fastness:f.colour_fastness||null};
    try{ await api("/api/quality/inspections",{method:"POST",body});
      setF({delta_e:"",shade_match:true,inspected_qty:"",rejection_qty:"",colour_fastness:""}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <div className="cards">
      <Kpi label="Inspections" value={sum?sum.inspections:"…"} />
      <Kpi label="Accepted" value={sum?sum.accepted:"…"} cls="pos" />
      <Kpi label="Rework" value={sum?sum.rework:"…"} />
      <Kpi label="Rejected" value={sum?sum.rejected:"…"} cls={sum&&sum.rejected?"neg":""} />
      <Kpi label="Rejection rate" value={sum?Number(sum.rejection_rate_pct).toFixed(1)+"%":"…"} />
      <Kpi label="Open complaints" value={sum?sum.open_complaints:"…"} cls={sum&&sum.open_complaints?"neg":""} />
    </div>
    <Panel title="Record inspection">
      <form className="row" onSubmit={add}>
        <div className="field"><label>Delta E</label>
          <input value={f.delta_e} onChange={e=>setF({...f,delta_e:e.target.value})} placeholder="0.8"/></div>
        <div className="field"><label>Inspected qty</label>
          <input value={f.inspected_qty} onChange={e=>setF({...f,inspected_qty:e.target.value})} placeholder="100"/></div>
        <div className="field"><label>Rejection qty</label>
          <input value={f.rejection_qty} onChange={e=>setF({...f,rejection_qty:e.target.value})} placeholder="0"/></div>
        <div className="field"><label>Fastness</label>
          <input value={f.colour_fastness} onChange={e=>setF({...f,colour_fastness:e.target.value})} placeholder="4-5"/></div>
        <div className="field"><label>Shade match</label>
          <select value={f.shade_match?"yes":"no"} onChange={e=>setF({...f,shade_match:e.target.value==="yes"})}>
            <option value="yes">Yes</option><option value="no">No</option></select></div>
        <button className="btn">Record (auto-grades result)</button>
      </form>
      {err && <div className="err">{err}</div>}
    </Panel>
    <Panel title="Recent inspections">
      {insp.length===0 ? <div className="muted">No inspections yet.</div> :
      <table><thead><tr><th>No.</th><th>Date</th><th>Delta E</th><th>Fastness</th><th>Result</th></tr></thead>
      <tbody>{insp.slice(0,15).map(r=><tr key={r.id}><td>{r.number}</td><td>{r.inspection_date}</td>
        <td>{r.delta_e||"-"}</td><td>{r.colour_fastness||"-"}</td>
        <td><span className={"badge "+(RESULT_CLS[r.result]||"")}>{r.result}</span></td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Customer complaints">
      {comp.length===0 ? <div className="muted">No complaints.</div> :
      <table><thead><tr><th>No.</th><th>Date</th><th>Severity</th><th>Status</th><th>Description</th></tr></thead>
      <tbody>{comp.slice(0,15).map(r=><tr key={r.id}><td>{r.number}</td><td>{r.complaint_date}</td>
        <td>{r.severity}</td><td><span className={"badge "+(r.status==="OPEN"?"warn":"ok")}>{r.status}</span></td>
        <td>{r.description}</td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- masters: parties -----------------------------------------------------
function PartyPage({kind}){  // kind: "customers" | "suppliers"
  const isCust = kind==="customers";
  const [rows,setRows]=useState(null); const [err,setErr]=useState("");
  const blank={code:"",name:"",gstin:"",state_code:"",credit_limit:"0"};
  const [f,setF]=useState(blank);
  const load=useCallback(()=>api(`/api/masters/${kind}`).then(setRows).catch(e=>setErr(e.message)),[kind]);
  useEffect(()=>{load()},[load]);
  const add=async e=>{ e.preventDefault(); setErr("");
    const body={code:f.code,name:f.name,gstin:f.gstin,state_code:f.state_code};
    if(isCust) body.credit_limit=f.credit_limit||"0";
    try{ await api(`/api/masters/${kind}`,{method:"POST",body});
      setF(blank); load(); }
    catch(ex){ setErr(ex.message); } };
  const title = isCust?"customer":"supplier";
  return (<div>
    <Panel title={"Add "+title}>
      <form className="row" onSubmit={add}>
        <div className="field"><label>Code</label><input required value={f.code}
          onChange={e=>setF({...f,code:e.target.value})} placeholder={kind==="customers"?"CUST-01":"SUP-01"}/></div>
        <div className="field"><label>Name</label><input required value={f.name}
          onChange={e=>setF({...f,name:e.target.value})} placeholder="Legal name"/></div>
        <div className="field"><label>GSTIN</label><input value={f.gstin}
          onChange={e=>setF({...f,gstin:e.target.value})} placeholder="03ABCDE1234F1Z5"/></div>
        <div className="field"><label>State code</label><input value={f.state_code}
          onChange={e=>setF({...f,state_code:e.target.value})} placeholder="03" style={{width:70}}/></div>
        {isCust && <div className="field"><label>Credit limit (₹)</label><input value={f.credit_limit}
          onChange={e=>setF({...f,credit_limit:e.target.value})} placeholder="0 = unlimited" style={{width:120}}/></div>}
        <button className="btn">Add</button>
      </form>{err && <div className="err">{err}</div>}
    </Panel>
    <Panel title={title.charAt(0).toUpperCase()+title.slice(1)+"s"}>
      {!rows?"Loading…":<table><thead><tr><th>Code</th><th>Name</th><th>GSTIN</th><th>State</th>{isCust && <th className="num">Credit limit</th>}</tr></thead>
      <tbody>{rows.map(r=><tr key={r.id}><td>{r.code}</td><td>{r.name}</td><td>{r.gstin||"-"}</td>
        <td>{r.state_code||"-"}</td>{isCust && <td className="num">{Number(r.credit_limit)>0?inr2(r.credit_limit):"—"}</td>}</tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- pre-sales: enquiries, quotations, statements -------------------------
function SalesPipeline(){
  const customers=useList("/api/masters/customers"); const fabrics=useList("/api/masters/fabric-types");
  const taxes=useList("/api/masters/tax-codes");
  const [enqs,setEnqs]=useState([]); const [quotes,setQuotes]=useState([]);
  const [ef,setEf]=useState({customer_id:"",number:genNo("ENQ"),description:""});
  const [qf,setQf]=useState({customer_id:"",number:genNo("QT"),fabric_type_id:"",qty_kg:"100",rate:"300",tax_code_id:""});
  const [stmtCust,setStmtCust]=useState(""); const [stmt,setStmt]=useState(null);
  const [eRes,setERes]=useState(null); const [qRes,setQRes]=useState(null); const [err,setErr]=useState("");
  const load=useCallback(()=>{ api("/api/sales/enquiries").then(setEnqs).catch(()=>{});
    api("/api/sales/quotations").then(setQuotes).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const go=async fn=>{ setErr(""); try{ await fn(); load(); }catch(ex){ setErr(ex.message); } };
  const addEnq=e=>{ e.preventDefault(); setERes(null); go(async()=>{
    setERes(await api("/api/sales/enquiries",{method:"POST",body:{customer_id:ef.customer_id,number:ef.number,
      enquiry_date:today(),description:ef.description||null}})); setEf({...ef,number:genNo("ENQ"),description:""}); }); };
  const addQuote=e=>{ e.preventDefault(); setQRes(null); go(async()=>{
    setQRes(await api("/api/sales/quotations",{method:"POST",body:{customer_id:qf.customer_id,number:qf.number,
      quote_date:today(), lines:[{fabric_type_id:qf.fabric_type_id||null,qty_kg:qf.qty_kg,rate:qf.rate,tax_code_id:qf.tax_code_id||null}]}}));
    setQf({...qf,number:genNo("QT")}); }); };
  const convert=id=>go(async()=>{ await api(`/api/sales/quotations/${id}/convert`,{method:"POST",
    body:{number:genNo("SO"),order_date:today()}}); });
  const loadStmt=async id=>{ setStmtCust(id); setStmt(null); if(!id) return;
    try{ setStmt(await api(`/api/reports/customer-statement?customer_id=${id}`)); }catch(ex){ setErr(ex.message); } };
  return (<div>
    <div className="row" style={{gap:16,alignItems:"flex-start"}}>
      <div style={{flex:1}}><Panel title="New enquiry">
        <form onSubmit={addEnq}>
          <div className="field"><label>Customer</label><Opts items={customers} value={ef.customer_id} onChange={e=>setEf({...ef,customer_id:e.target.value})}/></div>
          <div className="field"><label>Number</label><input value={ef.number} onChange={e=>setEf({...ef,number:e.target.value})}/></div>
          <div className="field"><label>Requirement</label><input value={ef.description} onChange={e=>setEf({...ef,description:e.target.value})} placeholder="e.g. 500 kg navy single jersey"/></div>
          <div style={{marginTop:10}}><button className="btn">Log enquiry</button></div>
        </form><Result r={eRes}/>
      </Panel></div>
      <div style={{flex:2}}><Panel title="New quotation">
        <form onSubmit={addQuote}>
          <div className="row mb">
            <div className="field"><label>Customer</label><Opts items={customers} value={qf.customer_id} onChange={e=>setQf({...qf,customer_id:e.target.value})}/></div>
            <div className="field"><label>Number</label><input value={qf.number} onChange={e=>setQf({...qf,number:e.target.value})}/></div>
          </div>
          <div className="row">
            <div className="field"><label>Fabric</label><Opts items={fabrics} value={qf.fabric_type_id} onChange={e=>setQf({...qf,fabric_type_id:e.target.value})}/></div>
            <div className="field"><label>Qty (kg)</label><input value={qf.qty_kg} onChange={e=>setQf({...qf,qty_kg:e.target.value})}/></div>
            <div className="field"><label>Rate</label><input value={qf.rate} onChange={e=>setQf({...qf,rate:e.target.value})}/></div>
            <div className="field"><label>Tax</label><Opts items={taxes} value={qf.tax_code_id} onChange={e=>setQf({...qf,tax_code_id:e.target.value})} placeholder="—"/></div>
            <div className="field" style={{alignSelf:"end"}}><button className="btn">Create quote</button></div>
          </div>
        </form><Result r={qRes}/>
      </Panel></div>
    </div>
    <Panel title="Quotations">
      {quotes.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Number</th><th>Date</th><th className="num">Value</th><th>Status</th><th></th></tr></thead>
      <tbody>{quotes.map(q=><tr key={q.id}><td>{q.number}</td><td>{q.date}</td><td className="num">{inr2(q.value)}</td>
        <td>{stBadge(q.status)}</td><td className="num">{q.status!=="COMPLETED" && q.status!=="CANCELLED" &&
          <button className="btn" onClick={()=>convert(q.id)}>Convert to SO</button>}
          {q.status==="COMPLETED" && <span className="muted">→ SO created</span>}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Enquiries">
      {enqs.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Number</th><th>Date</th><th>Requirement</th><th>Status</th></tr></thead>
      <tbody>{enqs.map(x=><tr key={x.id}><td>{x.number}</td><td>{x.date}</td><td>{x.description||"—"}</td><td>{stBadge(x.status)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Customer statement">
      <div className="row mb"><div className="field" style={{flex:1}}><label>Customer</label>
        <Opts items={customers} value={stmtCust} onChange={e=>loadStmt(e.target.value)}/></div></div>
      {stmt && <table><thead><tr><th>Date</th><th>Voucher</th><th>Narration</th><th className="num">Debit</th>
        <th className="num">Credit</th><th className="num">Balance</th></tr></thead>
      <tbody>
        <tr><td colSpan="5"><b>Opening balance</b></td><td className="num"><b>{inr2(stmt.opening)}</b></td></tr>
        {stmt.rows.map((r,i)=><tr key={i}><td>{r.date}</td><td>{r.voucher}</td><td>{r.narration||"—"}</td>
          <td className="num">{Number(r.debit)?inr2(r.debit):""}</td><td className="num">{Number(r.credit)?inr2(r.credit):""}</td>
          <td className="num">{inr2(r.balance)}</td></tr>)}</tbody>
      <tfoot><tr><th colSpan="5">Closing balance</th><th className="num">{inr2(stmt.closing)}</th></tr></tfoot></table>}
    </Panel>
    {err && <Panel><div className="err">{err}</div></Panel>}
  </div>);
}

// ---- transactions: purchase (GRN) -----------------------------------------
function Purchase(){
  const suppliers=useList("/api/masters/suppliers"); const chemicals=useList("/api/masters/chemicals");
  const warehouses=useList("/api/masters/warehouses"); const taxes=useList("/api/masters/tax-codes");
  const [hdr,setHdr]=useState({supplier_id:"",number:genNo("GRN"),grn_date:new Date().toISOString().slice(0,10)});
  const [lines,setLines]=useState([{chemical_id:"",warehouse_id:"",qty:"",unit_cost:"",tax_code_id:""}]);
  const [res,setRes]=useState(null); const [err,setErr]=useState("");
  const setLine=(i,k,v)=>setLines(ls=>ls.map((l,j)=>j===i?{...l,[k]:v}:l));
  const submit=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    try{ const body={...hdr, lines:lines.map(l=>({chemical_id:l.chemical_id, warehouse_id:l.warehouse_id||null,
        qty:l.qty, unit_cost:l.unit_cost, tax_code_id:l.tax_code_id||null}))};
      const r=await api("/api/procurement/grn",{method:"POST",body}); setRes(r);
      setHdr({...hdr,number:genNo("GRN")});
      setLines([{chemical_id:"",warehouse_id:"",qty:"",unit_cost:"",tax_code_id:""}]); }
    catch(ex){ setErr(ex.message); } };
  return (<Panel title="Goods Receipt Note (chemical / dye purchase)">
    <form onSubmit={submit}>
      <div className="row mb">
        <div className="field"><label>Supplier</label>
          <Opts items={suppliers} value={hdr.supplier_id} onChange={e=>setHdr({...hdr,supplier_id:e.target.value})}/></div>
        <div className="field"><label>GRN number</label>
          <input value={hdr.number} onChange={e=>setHdr({...hdr,number:e.target.value})}/></div>
        <div className="field"><label>Date</label>
          <input type="date" value={hdr.grn_date} onChange={e=>setHdr({...hdr,grn_date:e.target.value})}/></div>
      </div>
      <table><thead><tr><th>Chemical</th><th>Warehouse</th><th className="num">Qty</th>
        <th className="num">Unit cost</th><th>Tax</th><th></th></tr></thead>
      <tbody>{lines.map((l,i)=><tr key={i}>
        <td><Opts items={chemicals} value={l.chemical_id} onChange={e=>setLine(i,"chemical_id",e.target.value)}/></td>
        <td><Opts items={warehouses} value={l.warehouse_id} onChange={e=>setLine(i,"warehouse_id",e.target.value)} placeholder="—"/></td>
        <td className="num"><input style={{width:90}} value={l.qty} onChange={e=>setLine(i,"qty",e.target.value)}/></td>
        <td className="num"><input style={{width:100}} value={l.unit_cost} onChange={e=>setLine(i,"unit_cost",e.target.value)}/></td>
        <td><Opts items={taxes} value={l.tax_code_id} onChange={e=>setLine(i,"tax_code_id",e.target.value)} placeholder="—"/></td>
        <td>{lines.length>1 && <button type="button" className="btn sec" onClick={()=>setLines(ls=>ls.filter((_,j)=>j!==i))}>✕</button>}</td>
      </tr>)}</tbody></table>
      <div className="row" style={{marginTop:12}}>
        <button type="button" className="btn sec" onClick={()=>setLines(ls=>[...ls,{chemical_id:"",warehouse_id:"",qty:"",unit_cost:"",tax_code_id:""}])}>+ Add line</button>
        <button className="btn">Post GRN</button>
      </div>
      {err && <div className="err">{err}</div>}
    </form>
    <Result r={res}/>
  </Panel>);
}

// ---- transactions: knitting production ------------------------------------
function Knitting(){
  const machines=useList("/api/masters/machines?machine_type=KNITTING"); const yarns=useList("/api/masters/yarns");
  const fabrics=useList("/api/masters/fabric-types"); const warehouses=useList("/api/masters/warehouses");
  const orders=useList("/api/masters/sales-orders"); const suppliers=useList("/api/masters/suppliers");
  const [lots,setLots]=useState([]);
  const loadLots=useCallback(()=>api("/api/masters/yarn-lots").then(setLots).catch(()=>{}),[]);
  useEffect(()=>{loadLots()},[loadLots]);
  const [h,setH]=useState({number:genNo("KP"),machine_id:"",yarn_id:"",yarn_lot_id:"",fabric_type_id:"",sales_order_id:"",
    production_date:new Date().toISOString().slice(0,10),produced_kg:"",yarn_consumed_kg:"",wastage_kg:"0"});
  const [rolls,setRolls]=useState([{roll_no:"",weight_kg:"",warehouse_id:""}]);
  const [res,setRes]=useState(null); const [err,setErr]=useState("");
  const [lf,setLf]=useState({yarn_id:"",internal_lot_no:"",qty:"",unit_cost:"",supplier_id:""});
  const [lres,setLres]=useState(null); const [lerr,setLerr]=useState("");
  const setRoll=(i,k,v)=>setRolls(rs=>rs.map((r,j)=>j===i?{...r,[k]:v}:r));
  const lotOpts=lots.filter(l=>!h.yarn_id || l.yarn_id===h.yarn_id).map(l=>({id:l.id,label:l.label}));
  const receiveLot=async e=>{ e.preventDefault(); setLerr(""); setLres(null);
    try{ const r=await api("/api/masters/yarn-lots",{method:"POST",body:{yarn_id:lf.yarn_id,
        internal_lot_no:lf.internal_lot_no, qty:lf.qty, unit_cost:lf.unit_cost, supplier_id:lf.supplier_id||null}});
      setLres(r); setLf({yarn_id:"",internal_lot_no:"",qty:"",unit_cost:"",supplier_id:""}); loadLots(); }
    catch(ex){ setLerr(ex.message); } };
  const submit=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    try{ const body={...h, sales_order_id:h.sales_order_id||null, fabric_type_id:h.fabric_type_id||null,
        yarn_lot_id:h.yarn_lot_id||null,
        rolls:rolls.map(r=>({roll_no:r.roll_no, weight_kg:r.weight_kg, warehouse_id:r.warehouse_id||null}))};
      const r=await api("/api/knitting/production",{method:"POST",body}); setRes(r);
      setH({...h,number:genNo("KP"),yarn_lot_id:""}); setRolls([{roll_no:"",weight_kg:"",warehouse_id:""}]); loadLots(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Receive yarn lot">
      <form className="row" onSubmit={receiveLot}>
        <div className="field"><label>Yarn</label>
          <Opts items={yarns} value={lf.yarn_id} onChange={e=>setLf({...lf,yarn_id:e.target.value})}/></div>
        <div className="field"><label>Lot no.</label><input required value={lf.internal_lot_no}
          onChange={e=>setLf({...lf,internal_lot_no:e.target.value})} placeholder="YLOT-001"/></div>
        <div className="field"><label>Supplier</label>
          <Opts items={suppliers} value={lf.supplier_id} onChange={e=>setLf({...lf,supplier_id:e.target.value})} placeholder="—"/></div>
        <div className="field"><label>Qty (kg)</label><input style={{width:90}} value={lf.qty}
          onChange={e=>setLf({...lf,qty:e.target.value})}/></div>
        <div className="field"><label>Unit cost</label><input style={{width:90}} value={lf.unit_cost}
          onChange={e=>setLf({...lf,unit_cost:e.target.value})}/></div>
        <button className="btn">Receive</button>
      </form>
      {lerr && <div className="err">{lerr}</div>}
      <Result r={lres}/>
      {lots.length>0 && <table style={{marginTop:12}}><thead><tr><th>Lot</th><th className="num">On hand (kg)</th><th className="num">Unit cost</th></tr></thead>
        <tbody>{lots.map(l=><tr key={l.id}><td>{l.internal_lot_no}</td><td className="num">{inr2(l.on_hand)}</td>
          <td className="num">{inr2(l.unit_cost)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Knitting production (issues yarn, creates greige rolls)">
      <form onSubmit={submit}>
        <div className="row mb">
          <div className="field"><label>Doc number</label><input value={h.number} onChange={e=>setH({...h,number:e.target.value})}/></div>
          <div className="field"><label>Machine</label>
            <Opts items={machines} value={h.machine_id} onChange={e=>setH({...h,machine_id:e.target.value})}/></div>
          <div className="field"><label>Yarn</label>
            <Opts items={yarns} value={h.yarn_id} onChange={e=>setH({...h,yarn_id:e.target.value,yarn_lot_id:""})}/></div>
          <div className="field"><label>Yarn lot</label>
            <Opts items={lotOpts} value={h.yarn_lot_id} onChange={e=>setH({...h,yarn_lot_id:e.target.value})} placeholder="FIFO (any lot)"/></div>
          <div className="field"><label>Fabric type</label>
            <Opts items={fabrics} value={h.fabric_type_id} onChange={e=>setH({...h,fabric_type_id:e.target.value})} placeholder="—"/></div>
          <div className="field"><label>Sales order</label>
            <Opts items={orders} value={h.sales_order_id} onChange={e=>setH({...h,sales_order_id:e.target.value})} placeholder="—"/></div>
        </div>
        <div className="row mb">
          <div className="field"><label>Date</label><input type="date" value={h.production_date} onChange={e=>setH({...h,production_date:e.target.value})}/></div>
          <div className="field"><label>Produced (kg)</label><input value={h.produced_kg} onChange={e=>setH({...h,produced_kg:e.target.value})} placeholder="250"/></div>
          <div className="field"><label>Yarn consumed (kg)</label><input value={h.yarn_consumed_kg} onChange={e=>setH({...h,yarn_consumed_kg:e.target.value})} placeholder="255"/></div>
          <div className="field"><label>Wastage (kg)</label><input value={h.wastage_kg} onChange={e=>setH({...h,wastage_kg:e.target.value})} placeholder="5"/></div>
        </div>
        <table><thead><tr><th>Roll no.</th><th className="num">Weight (kg)</th><th>Warehouse</th><th></th></tr></thead>
        <tbody>{rolls.map((r,i)=><tr key={i}>
          <td><input value={r.roll_no} onChange={e=>setRoll(i,"roll_no",e.target.value)} placeholder="(auto)"/></td>
          <td className="num"><input style={{width:100}} value={r.weight_kg} onChange={e=>setRoll(i,"weight_kg",e.target.value)}/></td>
          <td><Opts items={warehouses} value={r.warehouse_id} onChange={e=>setRoll(i,"warehouse_id",e.target.value)} placeholder="—"/></td>
          <td>{rolls.length>1 && <button type="button" className="btn sec" onClick={()=>setRolls(rs=>rs.filter((_,j)=>j!==i))}>✕</button>}</td>
        </tr>)}</tbody></table>
        <div className="row" style={{marginTop:12}}>
          <button type="button" className="btn sec" onClick={()=>setRolls(rs=>[...rs,{roll_no:"",weight_kg:"",warehouse_id:""}])}>+ Add roll</button>
          <button className="btn">Record production</button>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
    </Panel>
  </div>);
}
// ---- transactions: sales invoice ------------------------------------------
function Sales(){
  const customers=useList("/api/masters/customers"); const taxes=useList("/api/masters/tax-codes");
  const [hdr,setHdr]=useState({customer_id:"",number:genNo("INV"),invoice_date:new Date().toISOString().slice(0,10)});
  const [lines,setLines]=useState([{description:"",qty:"",rate:"",tax_code_id:""}]);
  const [res,setRes]=useState(null); const [err,setErr]=useState("");
  const setLine=(i,k,v)=>setLines(ls=>ls.map((l,j)=>j===i?{...l,[k]:v}:l));
  const submit=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    try{ const body={...hdr, lines:lines.map(l=>({description:l.description, qty:l.qty, rate:l.rate,
        tax_code_id:l.tax_code_id||null}))};
      const r=await api("/api/sales/invoices",{method:"POST",body}); setRes(r);
      setHdr({...hdr,number:genNo("INV")}); setLines([{description:"",qty:"",rate:"",tax_code_id:""}]); }
    catch(ex){ setErr(ex.message); } };
  return (<Panel title="Sales invoice (with GST)">
    <form onSubmit={submit}>
      <div className="row mb">
        <div className="field"><label>Customer</label>
          <Opts items={customers} value={hdr.customer_id} onChange={e=>setHdr({...hdr,customer_id:e.target.value})}/></div>
        <div className="field"><label>Invoice number</label>
          <input value={hdr.number} onChange={e=>setHdr({...hdr,number:e.target.value})}/></div>
        <div className="field"><label>Date</label>
          <input type="date" value={hdr.invoice_date} onChange={e=>setHdr({...hdr,invoice_date:e.target.value})}/></div>
      </div>
      <table><thead><tr><th>Description</th><th className="num">Qty</th><th className="num">Rate</th><th>Tax</th><th></th></tr></thead>
      <tbody>{lines.map((l,i)=><tr key={i}>
        <td><input style={{width:260}} value={l.description} onChange={e=>setLine(i,"description",e.target.value)} placeholder="Dyed fabric"/></td>
        <td className="num"><input style={{width:90}} value={l.qty} onChange={e=>setLine(i,"qty",e.target.value)}/></td>
        <td className="num"><input style={{width:90}} value={l.rate} onChange={e=>setLine(i,"rate",e.target.value)}/></td>
        <td><Opts items={taxes} value={l.tax_code_id} onChange={e=>setLine(i,"tax_code_id",e.target.value)} placeholder="—"/></td>
        <td>{lines.length>1 && <button type="button" className="btn sec" onClick={()=>setLines(ls=>ls.filter((_,j)=>j!==i))}>✕</button>}</td>
      </tr>)}</tbody></table>
      <div className="row" style={{marginTop:12}}>
        <button type="button" className="btn sec" onClick={()=>setLines(ls=>[...ls,{description:"",qty:"",rate:"",tax_code_id:""}])}>+ Add line</button>
        <button className="btn">Post invoice</button>
      </div>
      {err && <div className="err">{err}</div>}
    </form>
    <Result r={res}/>
  </Panel>);
}

// ---- transactions: batching (assign greige rolls to a dye batch/lot) -------
function Batching(){
  const shades=useList("/api/masters/shades"); const orders=useList("/api/masters/sales-orders");
  const [rolls,setRolls]=useState([]); const [batches,setBatches]=useState([]);
  const [sel,setSel]=useState({});
  const [f,setF]=useState({batch_code:genNo("DY"),shade_id:"",sales_order_id:""});
  const [res,setRes]=useState(null); const [err,setErr]=useState("");
  const load=useCallback(()=>{ api("/api/rolls?kind=GREIGE&unbatched=true").then(setRolls).catch(()=>{});
    api("/api/dyeing/batches").then(setBatches).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const chosen=rolls.filter(r=>sel[r.id]);
  const totalKg=chosen.reduce((s,r)=>s+Number(r.weight_kg||0),0);
  const toggle=id=>setSel(s=>({...s,[id]:!s[id]}));
  const create=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    if(chosen.length===0){ setErr("Select at least one greige roll"); return; }
    try{ const r=await api("/api/dyeing/batches",{method:"POST",body:{batch_code:f.batch_code,
        shade_id:f.shade_id||null, sales_order_id:f.sales_order_id||null, roll_ids:chosen.map(r=>r.id)}});
      setRes(r); setF({...f,batch_code:genNo("DY")}); setSel({}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Create dye batch from greige rolls">
      <form onSubmit={create}>
        <div className="row mb">
          <div className="field"><label>Batch code</label><input value={f.batch_code} onChange={e=>setF({...f,batch_code:e.target.value})}/></div>
          <div className="field"><label>Shade</label><Opts items={shades} value={f.shade_id} onChange={e=>setF({...f,shade_id:e.target.value})} placeholder="—"/></div>
          <div className="field"><label>Sales order</label><Opts items={orders} value={f.sales_order_id} onChange={e=>setF({...f,sales_order_id:e.target.value})} placeholder="—"/></div>
          <div className="field"><label>Selected</label><div style={{padding:"9px 0",fontWeight:700}}>{chosen.length} rolls · {inr2(totalKg)} kg</div></div>
        </div>
        <table><thead><tr><th style={{width:40}}></th><th>Roll no.</th><th className="num">Weight (kg)</th><th>Quality</th></tr></thead>
        <tbody>{rolls.length===0 ? <tr><td colSpan="4" className="muted">No unbatched greige rolls — record a knitting production first.</td></tr>
          : rolls.map(r=><tr key={r.id}><td><input type="checkbox" checked={!!sel[r.id]} onChange={()=>toggle(r.id)}/></td>
            <td>{r.roll_no}</td><td className="num">{inr2(r.weight_kg)}</td><td>{r.quality_status}</td></tr>)}</tbody></table>
        <div className="row" style={{marginTop:12}}><button className="btn">Create batch ({chosen.length} rolls)</button></div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
    </Panel>
    <Panel title="Recent batches">
      {batches.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Batch</th><th className="num">Fabric (kg)</th><th className="num">Rolls</th><th>Status</th></tr></thead>
      <tbody>{batches.map(b=><tr key={b.id}><td>{b.batch_code}</td><td className="num">{inr2(b.fabric_kg)}</td>
        <td className="num">{b.rolls}</td><td><span className="badge ok">{b.status}</span></td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- transactions: dyeing (batch -> bath -> dispense -> register) ----------
function Dyeing(){
  const shades=useList("/api/masters/shades"); const machines=useList("/api/masters/machines?machine_type=DYEING");
  const versions=useList("/api/masters/recipe-versions"); const batches=useList("/api/dyeing/batches");
  const [batch,setBatch]=useState(null); const [bath,setBath]=useState(null);
  const [order,setOrder]=useState(null); const [disp,setDisp]=useState(null); const [reg,setReg]=useState(null);
  const [err,setErr]=useState("");
  const [bf,setBf]=useState({batch_code:genNo("DY"),fabric_kg:"250",shade_id:""});
  const [hf,setHf]=useState({bath_code:"",machine_id:"",recipe_version_id:"",fabric_kg:"250"});
  const go=async(fn)=>{ setErr(""); try{ await fn(); }catch(ex){ setErr(ex.message); } };
  const createBatch=()=>go(async()=>{ const r=await api("/api/dyeing/batches",{method:"POST",body:bf});
    setBatch(r); setHf({...hf,bath_code:r.batch_code+"-B01",fabric_kg:bf.fabric_kg}); });
  const createBath=()=>go(async()=>{ const r=await api("/api/dyeing/baths",{method:"POST",
    body:{...hf,batch_id:batch.batch_id}}); setBath(r); setOrder(null); setDisp(null); setReg(null); });
  const preview=()=>go(async()=>setOrder(await api(`/api/dyeing/baths/${bath.bath_id}/dose-order`)));
  const dispense=()=>go(async()=>{ setDisp(await api(`/api/dyeing/baths/${bath.bath_id}/simulate-dispense`,{method:"POST"}));
    setReg(await api(`/api/dyeing/baths/${bath.bath_id}/consumption-register`)); });
  const pickBatch=id=>{ const b=batches.find(x=>x.id===id); if(!b){setBatch(null);return;}
    setBatch({batch_id:b.id,batch_code:b.batch_code}); setHf({...hf,bath_code:b.batch_code+"-B01",fabric_kg:b.fabric_kg}); };
  return (<div>
    <Panel title="1 · Create or pick a dye batch">
      <div className="row mb"><div className="field"><label>Use an existing (batched) batch</label>
        <Opts items={batches} value={batch?batch.batch_id:""} onChange={e=>pickBatch(e.target.value)} placeholder="— new batch below —"/></div></div>
      <div className="row">
        <div className="field"><label>Batch code</label><input value={bf.batch_code}
          onChange={e=>setBf({...bf,batch_code:e.target.value})}/></div>
        <div className="field"><label>Fabric (kg)</label><input value={bf.fabric_kg}
          onChange={e=>setBf({...bf,fabric_kg:e.target.value})}/></div>
        <div className="field"><label>Shade</label>
          <Opts items={shades} value={bf.shade_id} onChange={e=>setBf({...bf,shade_id:e.target.value})}/></div>
        <button className="btn" onClick={createBatch}>Create batch</button>
      </div>
      {batch && <div className="muted" style={{marginTop:10}}>Batch {batch.batch_code} created ✓</div>}
    </Panel>
    {batch && <Panel title="2 · Add bath (approved recipe)">
      <div className="row">
        <div className="field"><label>Bath code</label><input value={hf.bath_code}
          onChange={e=>setHf({...hf,bath_code:e.target.value})}/></div>
        <div className="field"><label>Machine</label>
          <Opts items={machines} value={hf.machine_id} onChange={e=>setHf({...hf,machine_id:e.target.value})}/></div>
        <div className="field"><label>Recipe version</label>
          <Opts items={versions} value={hf.recipe_version_id} onChange={e=>setHf({...hf,recipe_version_id:e.target.value})}/></div>
        <div className="field"><label>Fabric (kg)</label><input value={hf.fabric_kg}
          onChange={e=>setHf({...hf,fabric_kg:e.target.value})}/></div>
        <button className="btn" onClick={createBath}>Create bath</button>
      </div>
      {bath && <div className="row" style={{marginTop:12}}>
        <span className="muted" style={{alignSelf:"center"}}>Bath {bath.bath_code} ✓</span>
        <button className="btn sec" onClick={preview}>Preview dose order</button>
        <button className="btn" onClick={dispense}>Simulate dispensing</button></div>}
    </Panel>}
    {order && <Panel title="Scaled dose order (ERP → Color Service)">
      <table><thead><tr><th>Seq</th><th>Material</th><th className="num">Target (kg)</th><th>Dye?</th></tr></thead>
      <tbody>{order.materials.map((m,i)=><tr key={i}><td>{m.sequence}</td><td>{m.material_code}</td>
        <td className="num">{m.target_qty}</td><td>{m.is_dye?"Dye":"Aux"}</td></tr>)}</tbody></table>
    </Panel>}
    {disp && <div className="cards">
      <Kpi label="Dispensed lines" value={disp.processed} />
      <Kpi label="Skipped (replay)" value={disp.skipped} />
      <Kpi label="Consumption cost" value={inr(disp.total_cost)} />
    </div>}
    {reg && <Panel title="Bath consumption register (recipe vs actual)">
      <table><thead><tr><th>Material</th><th className="num">Recipe</th><th className="num">Actual</th>
        <th className="num">Variance</th><th className="num">Cost</th></tr></thead>
      <tbody>{reg.rows.map((r,i)=><tr key={i}><td>{r.material_code}</td><td className="num">{r.recipe_target_kg}</td>
        <td className="num">{r.actual_kg}</td><td className="num">{r.variance_kg}</td>
        <td className="num">{inr2(r.cost)}</td></tr>)}</tbody>
      <tfoot><tr><th>Total</th><th></th><th className="num">{reg.total_actual_kg}</th><th></th>
        <th className="num">{inr2(reg.total_cost)}</th></tr></tfoot></table>
    </Panel>}
    {err && <Panel><div className="err">{err}</div></Panel>}
  </div>);
}

// ---- costing --------------------------------------------------------------
function Costing(){
  const [cpk,errc]=useData("/api/costing/cost-per-kg");
  const custs=useList("/api/costing/customers"); const orders=useList("/api/costing/orders");
  const machines=useList("/api/costing/machines"); const accounts=useList("/api/accounts");
  const [cc,setCc]=useState(null); const [pools,setPools]=useState([]);
  const [pf,setPf]=useState({code:genNo("OH"),name:"Electricity",source_account_code:"5070",basis:"OUTPUT_KG"});
  const [af,setAf]=useState({pool_id:"",month:today().slice(0,7),amount:"10000"});
  const [pRes,setPRes]=useState(null); const [aRes,setARes]=useState(null); const [ohErr,setOhErr]=useState("");
  const load=useCallback(()=>{ api("/api/reports/cost-centres").then(setCc).catch(()=>{});
    api("/api/accounting/overhead/pools").then(setPools).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const expAccts=accounts.filter(a=>a.code&&(a.code[0]==="5"||a.code[0]==="6")&&a.allow_posting)
    .map(a=>({id:a.code,name:a.code+" — "+a.name}));
  const makePool=async e=>{ e.preventDefault(); setOhErr(""); setPRes(null);
    try{ const r=await api("/api/accounting/overhead/pools",{method:"POST",body:pf});
      setPRes(r); setPf({...pf,code:genNo("OH")}); load(); }catch(ex){ setOhErr(ex.message); } };
  const runAlloc=async e=>{ e.preventDefault(); setOhErr(""); setARes(null);
    if(!af.pool_id){ setOhErr("Pick a pool"); return; }
    try{ setARes(await api("/api/accounting/overhead/allocate",{method:"POST",body:{
        pool_id:af.pool_id, period_month:af.month+"-01", amount:af.amount||null}})); load(); }
    catch(ex){ setOhErr(ex.message); } };
  const pct = v => (Number(v||0)).toFixed(1)+"%";
  return (<div>
    <div className="cards">
      <Kpi label="Knitted cost / kg" value={cpk?inr(cpk.knitted_cost_per_kg):"…"} />
      <Kpi label="Dyeing cost / kg" value={cpk?inr(cpk.dyeing_cost_per_kg):"…"} />
      <Kpi label="Knitted output (kg)" value={cpk?inr2(cpk.knitted_kg):"…"} />
      <Kpi label="Dyed output (kg)" value={cpk?inr2(cpk.dyed_kg):"…"} />
    </div>
    <Panel title="Customer profitability">
      {custs.length===0 ? <div className="muted">No billed customers yet.</div> :
      <table><thead><tr><th>Customer</th><th className="num">Revenue</th><th className="num">Cost</th>
        <th className="num">Gross profit</th><th className="num">Margin</th></tr></thead>
      <tbody>{custs.map((c,i)=><tr key={i}><td>{c.customer}</td><td className="num">{inr2(c.revenue)}</td>
        <td className="num">{inr2(c.cost)}</td>
        <td className="num" style={{color:Number(c.gross_profit)>=0?"#16a34a":"#dc2626"}}>{inr2(c.gross_profit)}</td>
        <td className="num">{pct(c.margin_pct)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Order profitability">
      {orders.filter(o=>Number(o.revenue)||Number(o.total_cost)).length===0 ?
        <div className="muted">No orders with activity yet.</div> :
      <table><thead><tr><th>Order</th><th className="num">Revenue</th><th className="num">Yarn</th>
        <th className="num">Dyeing</th><th className="num">Total cost</th><th className="num">Gross profit</th>
        <th className="num">Margin</th></tr></thead>
      <tbody>{orders.filter(o=>Number(o.revenue)||Number(o.total_cost)).map((o,i)=><tr key={i}>
        <td>{o.order_number}</td><td className="num">{inr2(o.revenue)}</td><td className="num">{inr2(o.yarn_cost)}</td>
        <td className="num">{inr2(o.dyeing_cost)}</td><td className="num">{inr2(o.total_cost)}</td>
        <td className="num" style={{color:Number(o.gross_profit)>=0?"#16a34a":"#dc2626"}}>{inr2(o.gross_profit)}</td>
        <td className="num">{pct(o.margin_pct)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Machine-wise cost">
      {machines.length===0 ? <div className="muted">No machine cost yet.</div> :
      <table><thead><tr><th>Machine</th><th>Type</th><th className="num">Cost</th></tr></thead>
      <tbody>{machines.map((m,i)=><tr key={i}><td>{m.machine}</td><td>{m.machine_type}</td>
        <td className="num">{inr2(m.cost)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Overhead allocation (absorb indirect cost into work centres)">
      <div className="row" style={{gap:16,alignItems:"flex-start"}}>
        <div style={{flex:1}}>
          <form onSubmit={makePool}>
            <div className="field"><label>Pool code</label><input value={pf.code} onChange={e=>setPf({...pf,code:e.target.value})}/></div>
            <div className="field"><label>Name</label><input value={pf.name} onChange={e=>setPf({...pf,name:e.target.value})}/></div>
            <div className="field"><label>Source (expense) account</label><Opts items={expAccts} value={pf.source_account_code} onChange={e=>setPf({...pf,source_account_code:e.target.value})}/></div>
            <div className="field"><label>Basis</label><select value={pf.basis} onChange={e=>setPf({...pf,basis:e.target.value})}>
              <option value="OUTPUT_KG">Output kg</option><option value="MACHINE_HOURS">Machine hours</option><option value="EQUAL">Equal split</option></select></div>
            <div style={{marginTop:10}}><button className="btn">Create pool</button></div>
          </form>
          <Result r={pRes}/>
        </div>
        <div style={{flex:1}}>
          <form onSubmit={runAlloc}>
            <div className="field"><label>Pool</label><Opts items={pools.map(p=>({id:p.id,name:`${p.code} — ${p.name} (${p.basis})`}))} value={af.pool_id} onChange={e=>setAf({...af,pool_id:e.target.value})}/></div>
            <div className="field"><label>Period (month)</label><input type="month" value={af.month} onChange={e=>setAf({...af,month:e.target.value})}/></div>
            <div className="field"><label>Amount (blank = absorb account balance)</label><input value={af.amount} onChange={e=>setAf({...af,amount:e.target.value})}/></div>
            <div style={{marginTop:10}}><button className="btn">Run allocation</button></div>
          </form>
          <Result r={aRes}/>
        </div>
      </div>
      {ohErr && <div className="err">{ohErr}</div>}
    </Panel>
    <Panel title="Work-centre cost (from the ledger)">
      <div className="muted" style={{marginBottom:8,fontSize:12}}>Each machine is a work centre; production postings tag its cost centre, which rolls up to the department.</div>
      {(!cc || cc.centres.length===0) ? <div className="muted">No cost-centre postings yet.</div> :
      <div className="row" style={{gap:16,alignItems:"flex-start"}}>
        <div style={{flex:1}}>
          <table><thead><tr><th>Work centre</th><th>Type</th><th className="num">Net cost</th></tr></thead>
          <tbody>{cc.centres.map((c,i)=><tr key={i}><td>{c.code} — {c.name}</td><td>{c.type}</td>
            <td className="num">{inr2(c.net_cost)}</td></tr>)}</tbody></table>
        </div>
        <div style={{flex:1}}>
          <table><thead><tr><th>Department</th><th className="num">Net cost</th></tr></thead>
          <tbody>{cc.departments.map((d,i)=><tr key={i}><td>{d.code} — {d.name}</td>
            <td className="num">{inr2(d.net_cost)}</td></tr>)}</tbody>
          <tfoot><tr><th>Total</th><th className="num">{inr2(cc.total_net_cost)}</th></tr></tfoot></table>
        </div>
      </div>}
    </Panel>
  </div>);
}

// ---- devices & labels -----------------------------------------------------
function Devices(){
  const [summary,setSummary]=useState([]); const [readings,setReadings]=useState([]);
  const [rolls,setRolls]=useState([]); const [machines,setMachines]=useState([]); const [scales,setScales]=useState([]);
  const [msg,setMsg]=useState("");
  const load=useCallback(()=>{
    api("/api/hardware/knitting-summary").then(setSummary).catch(()=>{});
    api("/api/hardware/scale-readings").then(setReadings).catch(()=>{});
    api("/api/rolls").then(setRolls).catch(()=>{});
    api("/api/masters/machines?machine_type=KNITTING").then(setMachines).catch(()=>{});
    api("/api/hardware/scales").then(setScales).catch(()=>{});
  },[]);
  useEffect(()=>{load()},[load]);
  const simKnit=async code=>{ setMsg(""); try{ const r=await api("/api/hardware/simulate/knitting",
    {method:"POST",body:{machine_code:code,samples:4}}); setMsg(`Ingested ${r.created} samples from ${code}`); load(); }
    catch(e){ setMsg(e.message); } };
  const simWeigh=async code=>{ setMsg(""); try{ const r=await api("/api/hardware/simulate/scale",
    {method:"POST",body:{scale_code:code,weight:(120+Math.floor(Math.random()*20)).toString(),
      reference_type:"ROLL",seq:Date.now()%100000}}); setMsg(`Captured weight on ${code}`); load(); }
    catch(e){ setMsg(e.message); } };
  const label=async id=>{ try{ const blob=await api(`/api/rolls/${id}/label`,{blob:true});
    const url=URL.createObjectURL(blob); window.open(url,"_blank");
    setTimeout(()=>URL.revokeObjectURL(url),10000); }catch(e){ alert(e.message); } };
  return (<div>
    <Panel title="Knitting machine monitoring">
      <div className="row mb">{machines.map(m=><button key={m.id} className="btn sec"
        onClick={()=>simKnit(m.code)}>Simulate {m.code}</button>)}</div>
      {summary.length===0 ? <div className="muted">No machine logs yet — run a simulation.</div> :
      <table><thead><tr><th>Machine</th><th className="num">Produced (kg)</th><th className="num">Run (min)</th>
        <th className="num">Downtime (min)</th><th className="num">Stops</th><th className="num">Efficiency</th></tr></thead>
      <tbody>{summary.map((s,i)=><tr key={i}><td>{s.machine}</td><td className="num">{inr2(s.produced_kg)}</td>
        <td className="num">{s.run_minutes}</td><td className="num">{s.downtime_minutes}</td>
        <td className="num">{s.stops}</td><td className="num">{Number(s.efficiency_pct).toFixed(1)}%</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Weighing scale readings">
      <div className="row mb">{scales.map(s=><button key={s.id} className="btn sec"
        onClick={()=>simWeigh(s.code)}>Capture weight · {s.code}</button>)}</div>
      {readings.length===0 ? <div className="muted">No readings yet.</div> :
      <table><thead><tr><th>Reading id</th><th className="num">Weight</th><th>Ref</th><th>Stable</th><th>At</th></tr></thead>
      <tbody>{readings.slice(0,10).map(r=><tr key={r.id}><td>{r.external_reading_id}</td>
        <td className="num">{inr2(r.weight)} {r.unit}</td><td>{r.reference_type||"-"}</td>
        <td>{r.stable?"Yes":"No"}</td><td>{(r.captured_at||"").slice(0,19).replace("T"," ")}</td></tr>)}</tbody></table>}
      {msg && <div className="muted" style={{marginTop:10}}>{msg}</div>}
    </Panel>
    <Panel title="Fabric roll labels (QR)">
      {rolls.length===0 ? <div className="muted">No rolls yet — record a knitting production.</div> :
      <table><thead><tr><th>Roll no.</th><th>Kind</th><th className="num">Weight</th><th>Quality</th><th></th></tr></thead>
      <tbody>{rolls.map(r=><tr key={r.id}><td>{r.roll_no}</td><td>{r.kind}</td>
        <td className="num">{inr2(r.weight_kg)} kg</td><td>{r.quality_status}</td>
        <td className="num"><button className="btn sec" onClick={()=>label(r.id)}>Print label</button></td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- masters manager (generic) --------------------------------------------
const MASTER_ENTITIES={
  "tax-codes":{label:"Tax codes",fields:[{n:"code",l:"Code"},{n:"name",l:"Name"},
    {n:"cgst_rate",l:"CGST %",t:"num"},{n:"sgst_rate",l:"SGST %",t:"num"},{n:"igst_rate",l:"IGST %",t:"num"},{n:"hsn_sac",l:"HSN/SAC"}]},
  "chemicals":{label:"Chemicals & dyes",fields:[{n:"code",l:"Code"},{n:"name",l:"Name"},
    {n:"is_dye",l:"Is dye?",t:"bool"},{n:"dye_class",l:"Dye class"},{n:"standard_cost",l:"Std cost",t:"num"},{n:"reorder_level",l:"Reorder",t:"num"}]},
  "machines":{label:"Machines",fields:[{n:"machine_type",l:"Type",t:"select",opts:["KNITTING","DYEING","FINISHING"]},
    {n:"code",l:"Code"},{n:"name",l:"Name"},{n:"capacity_kg",l:"Capacity kg",t:"num"}]},
  "shades":{label:"Shades",fields:[{n:"code",l:"Code"},{n:"name",l:"Name"}]},
  "fabric-types":{label:"Fabric types",fields:[{n:"code",l:"Code"},{n:"name",l:"Name"},
    {n:"construction",l:"Construction"},{n:"composition",l:"Composition"},{n:"gsm",l:"GSM",t:"num"},{n:"width_cm",l:"Width cm",t:"num"}]},
  "yarns":{label:"Yarns",fields:[{n:"code",l:"Code"},{n:"name",l:"Name"},{n:"yarn_type",l:"Type"},{n:"count",l:"Count"}]},
};
function MastersManager(){
  const [ent,setEnt]=useState("tax-codes"); const cfg=MASTER_ENTITIES[ent];
  const [rows,setRows]=useState([]); const [f,setF]=useState({}); const [err,setErr]=useState(""); const [ok,setOk]=useState("");
  const load=useCallback(()=>api(`/api/masters/${ent}`).then(setRows).catch(e=>setErr(e.message)),[ent]);
  useEffect(()=>{ setF({}); setErr(""); setOk(""); load(); },[ent,load]);
  const submit=async e=>{ e.preventDefault(); setErr(""); setOk("");
    const body={}; cfg.fields.forEach(fl=>{ let v=f[fl.n];
      if(fl.t==="bool"){ body[fl.n]=!!v; return; }
      if(v!==undefined && v!=="") body[fl.n]=v; });
    try{ const r=await api(`/api/masters/${ent}`,{method:"POST",body}); setOk(`Created ${r.code}`); setF({}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Manage master data">
      <div className="row mb"><div className="field"><label>Entity</label>
        <select value={ent} onChange={e=>setEnt(e.target.value)}>
          {Object.entries(MASTER_ENTITIES).map(([k,v])=><option key={k} value={k}>{v.label}</option>)}</select></div></div>
      <form className="row" onSubmit={submit}>
        {cfg.fields.map(fl=><div className="field" key={fl.n}><label>{fl.l}</label>
          {fl.t==="select" ? <select value={f[fl.n]||""} onChange={e=>setF({...f,[fl.n]:e.target.value})}>
              <option value="">—</option>{fl.opts.map(o=><option key={o}>{o}</option>)}</select>
           : fl.t==="bool" ? <select value={f[fl.n]?"yes":"no"} onChange={e=>setF({...f,[fl.n]:e.target.value==="yes"})}>
              <option value="no">No</option><option value="yes">Yes</option></select>
           : <input style={{width:fl.t==="num"?90:150}} value={f[fl.n]||""} onChange={e=>setF({...f,[fl.n]:e.target.value})}/>}
        </div>)}
        <button className="btn">Add</button>
      </form>
      {err && <div className="err">{err}</div>}
      {ok && <div style={{marginTop:8,color:"#16a34a",fontSize:12.5}}>{ok}</div>}
    </Panel>
    <Panel title={cfg.label}>
      {rows.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Code</th><th>Name</th></tr></thead>
      <tbody>{rows.map((r,i)=><tr key={r.id||i}><td>{r.code}</td><td>{r.name}</td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- manual vouchers ------------------------------------------------------
const VTYPES=[["JV","Journal"],["PV","Payment"],["RV","Receipt"],["CV","Contra"],["PI","Purchase"],
  ["SI","Sales"],["DN","Debit Note"],["CN","Credit Note"],["SJ","Stock Journal"],["PJ","Production Journal"]];
function Vouchers(){
  const accounts=useList("/api/accounts");
  const acctOpts=accounts.filter(a=>a.allow_posting).map(a=>({id:a.code,label:`${a.code} · ${a.name}`}));
  const [h,setH]=useState({voucher_type_code:"JV",voucher_date:new Date().toISOString().slice(0,10),
    posting_reference:genNo("JV"),narration:""});
  const [lines,setLines]=useState([{account_code:"",debit:"",credit:"",narration:""},{account_code:"",debit:"",credit:"",narration:""}]);
  const [res,setRes]=useState(null); const [err,setErr]=useState(""); const [recent,setRecent]=useState([]);
  const loadRecent=useCallback(()=>api("/api/vouchers").then(setRecent).catch(()=>{}),[]);
  useEffect(()=>{loadRecent()},[loadRecent]);
  const setLine=(i,k,v)=>setLines(ls=>ls.map((l,j)=>j===i?{...l,[k]:v}:l));
  const num=v=>Number(v||0);
  const totDr=lines.reduce((s,l)=>s+num(l.debit),0), totCr=lines.reduce((s,l)=>s+num(l.credit),0);
  const balanced=Math.abs(totDr-totCr)<0.005 && totDr>0;
  const submit=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    try{ const body={...h, lines:lines.filter(l=>l.account_code && (num(l.debit)||num(l.credit)))
        .map(l=>({account_code:l.account_code, debit:l.debit||"0", credit:l.credit||"0", narration:l.narration||null}))};
      const r=await api("/api/vouchers",{method:"POST",body}); setRes({voucher:r.number,status:r.status,total:r.total_debit});
      setH({...h,posting_reference:genNo("JV")});
      setLines([{account_code:"",debit:"",credit:"",narration:""},{account_code:"",debit:"",credit:"",narration:""}]); loadRecent(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Post a voucher">
      <form onSubmit={submit}>
        <div className="row mb">
          <div className="field"><label>Type</label><select value={h.voucher_type_code}
            onChange={e=>setH({...h,voucher_type_code:e.target.value})}>
            {VTYPES.map(([c,l])=><option key={c} value={c}>{c} · {l}</option>)}</select></div>
          <div className="field"><label>Date</label><input type="date" value={h.voucher_date}
            onChange={e=>setH({...h,voucher_date:e.target.value})}/></div>
          <div className="field"><label>Reference</label><input value={h.posting_reference}
            onChange={e=>setH({...h,posting_reference:e.target.value})}/></div>
          <div className="field" style={{flex:1}}><label>Narration</label><input value={h.narration}
            onChange={e=>setH({...h,narration:e.target.value})} placeholder="Description"/></div>
        </div>
        <table><thead><tr><th>Account</th><th className="num">Debit</th><th className="num">Credit</th><th>Narration</th><th></th></tr></thead>
        <tbody>{lines.map((l,i)=><tr key={i}>
          <td><Opts items={acctOpts} value={l.account_code} onChange={e=>setLine(i,"account_code",e.target.value)}/></td>
          <td className="num"><input style={{width:100}} value={l.debit} onChange={e=>setLine(i,"debit",e.target.value)}/></td>
          <td className="num"><input style={{width:100}} value={l.credit} onChange={e=>setLine(i,"credit",e.target.value)}/></td>
          <td><input style={{width:150}} value={l.narration} onChange={e=>setLine(i,"narration",e.target.value)}/></td>
          <td>{lines.length>2 && <button type="button" className="btn sec" onClick={()=>setLines(ls=>ls.filter((_,j)=>j!==i))}>✕</button>}</td>
        </tr>)}</tbody>
        <tfoot><tr><th>Totals</th><th className="num">{inr2(totDr)}</th><th className="num">{inr2(totCr)}</th>
          <th colSpan="2"><span className={"badge "+(balanced?"ok":"bad")}>{balanced?"Balanced":"Dr ≠ Cr"}</span></th></tr></tfoot></table>
        <div className="row" style={{marginTop:12}}>
          <button type="button" className="btn sec" onClick={()=>setLines(ls=>[...ls,{account_code:"",debit:"",credit:"",narration:""}])}>+ Add line</button>
          <button className="btn">Post voucher</button>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
    </Panel>
    <Panel title="Recent vouchers">
      {recent.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Number</th><th>Date</th><th>Status</th><th className="num">Debit</th><th>Narration</th></tr></thead>
      <tbody>{recent.map(v=><tr key={v.id}><td>{v.number}</td><td>{v.voucher_date}</td>
        <td><span className={"badge "+(v.status==="POSTED"?"ok":v.status==="REVERSED"?"warn":"")}>{v.status}</span></td>
        <td className="num">{inr2(v.total_debit)}</td><td>{v.narration||"-"}</td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- administration -------------------------------------------------------
function Admin(){
  const [periods,setPeriods]=useState([]); const [users,setUsers]=useState([]); const roles=useList("/api/admin/roles");
  const [err,setErr]=useState(""); const [ok,setOk]=useState("");
  const [nu,setNu]=useState({username:"",email:"",full_name:"",password:"",role_codes:[]});
  const load=useCallback(()=>{ api("/api/admin/periods").then(setPeriods).catch(e=>setErr(e.message));
    api("/api/admin/users").then(setUsers).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const setPeriod=async(id,action)=>{ setErr(""); try{ await api(`/api/admin/periods/${id}/${action}`,{method:"POST"}); load(); }catch(e){ setErr(e.message); } };
  const toggleRole=code=>setNu(n=>({...n, role_codes: n.role_codes.includes(code)? n.role_codes.filter(c=>c!==code): [...n.role_codes,code]}));
  const addUser=async e=>{ e.preventDefault(); setErr(""); setOk("");
    try{ await api("/api/admin/users",{method:"POST",body:nu}); setOk(`User ${nu.username} created`);
      setNu({username:"",email:"",full_name:"",password:"",role_codes:[]}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Accounting periods">
      <table><thead><tr><th>Code</th><th>Name</th><th>Status</th><th></th></tr></thead>
      <tbody>{periods.map(p=><tr key={p.id}><td>{p.code}</td><td>{p.name}</td>
        <td><span className={"badge "+(p.status==="OPEN"?"ok":"bad")}>{p.status}</span></td>
        <td className="num">{p.status==="OPEN"
          ? <button className="btn sec" onClick={()=>setPeriod(p.id,"close")}>Close</button>
          : <button className="btn sec" onClick={()=>setPeriod(p.id,"reopen")}>Reopen</button>}</td></tr>)}</tbody></table>
    </Panel>
    <Panel title="Add user">
      <form onSubmit={addUser}>
        <div className="row mb">
          <div className="field"><label>Username</label><input required value={nu.username} onChange={e=>setNu({...nu,username:e.target.value})}/></div>
          <div className="field"><label>Email</label><input value={nu.email} onChange={e=>setNu({...nu,email:e.target.value})}/></div>
          <div className="field"><label>Full name</label><input value={nu.full_name} onChange={e=>setNu({...nu,full_name:e.target.value})}/></div>
          <div className="field"><label>Password</label><input type="password" value={nu.password} onChange={e=>setNu({...nu,password:e.target.value})}/></div>
        </div>
        <div className="field mb"><label>Roles</label>
          <div className="row">{roles.map(r=><label key={r.code} style={{display:"flex",gap:5,alignItems:"center",fontSize:12.5}}>
            <input type="checkbox" checked={nu.role_codes.includes(r.code)} onChange={()=>toggleRole(r.code)}/> {r.code}</label>)}</div></div>
        <button className="btn">Create user</button>
      </form>
      {err && <div className="err">{err}</div>}
      {ok && <div style={{marginTop:8,color:"#16a34a",fontSize:12.5}}>{ok}</div>}
    </Panel>
    <Panel title="Users">
      <table><thead><tr><th>Username</th><th>Email</th><th>Roles</th><th>Active</th></tr></thead>
      <tbody>{users.map(u=><tr key={u.id}><td>{u.username}</td><td>{u.email}</td>
        <td>{u.roles.join(", ")||(u.is_superuser?"(superuser)":"-")}</td>
        <td><span className={"badge "+(u.is_active?"ok":"bad")}>{u.is_active?"Yes":"No"}</span></td></tr>)}</tbody></table>
    </Panel>
  </div>);
}


// ---- shell ----------------------------------------------------------------
// ---- transactions: purchase orders (PO -> GRN roll-up) ---------------------
const stBadge = s => <span className={"badge "+(s==="COMPLETED"?"ok":s==="CANCELLED"?"err":"")}>{s}</span>;
const today = () => new Date().toISOString().slice(0,10);

function PurchaseOrders(){
  const suppliers=useList("/api/masters/suppliers"); const chemicals=useList("/api/masters/chemicals");
  const taxes=useList("/api/masters/tax-codes");
  const [pos,setPos]=useState([]); const [open,setOpen]=useState(null);
  const [f,setF]=useState({supplier_id:"",number:genNo("PO"),order_date:today(),
    chemical_id:"",qty:"100",rate:"1200",tax_code_id:""});
  const [res,setRes]=useState(null); const [err,setErr]=useState("");
  const [dn,setDn]=useState({supplier_id:"",number:genNo("DN"),chemical_id:"",qty:"10",tax_code_id:"",reason:""});
  const [dnRes,setDnRes]=useState(null); const [dnErr,setDnErr]=useState("");
  const load=useCallback(()=>{ api("/api/procurement/purchase-orders").then(setPos).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const create=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    try{ const r=await api("/api/procurement/purchase-orders",{method:"POST",body:{
        supplier_id:f.supplier_id, number:f.number, order_date:f.order_date,
        lines:[{chemical_id:f.chemical_id||null, qty:f.qty, rate:f.rate, tax_code_id:f.tax_code_id||null}]}});
      setRes({po:r.number, status:r.status}); setF({...f,number:genNo("PO")}); load(); }
    catch(ex){ setErr(ex.message); } };
  const confirm=async id=>{ try{ await api(`/api/procurement/purchase-orders/${id}/confirm`,{method:"POST"}); load(); }
    catch(ex){ setErr(ex.message); } };
  const view=async id=>{ if(open&&open.id===id){ setOpen(null); return; }
    try{ setOpen(await api(`/api/procurement/purchase-orders/${id}`)); }catch(ex){ setErr(ex.message); } };
  const createDN=async e=>{ e.preventDefault(); setDnErr(""); setDnRes(null);
    try{ const r=await api("/api/purchase/debit-notes",{method:"POST",body:{
        supplier_id:dn.supplier_id, number:dn.number, note_date:today(), reason:dn.reason||null,
        lines:[{chemical_id:dn.chemical_id, qty:dn.qty, tax_code_id:dn.tax_code_id||null}]}});
      setDnRes(r); setDn({...dn,number:genNo("DN")}); }
    catch(ex){ setDnErr(ex.message); } };
  return (<div>
    <Panel title="Raise a purchase order">
      <form onSubmit={create}>
        <div className="row mb">
          <div className="field"><label>Supplier</label><Opts items={suppliers} value={f.supplier_id} onChange={e=>setF({...f,supplier_id:e.target.value})}/></div>
          <div className="field"><label>PO number</label><input value={f.number} onChange={e=>setF({...f,number:e.target.value})}/></div>
          <div className="field"><label>Order date</label><input type="date" value={f.order_date} onChange={e=>setF({...f,order_date:e.target.value})}/></div>
        </div>
        <div className="row">
          <div className="field"><label>Chemical</label><Opts items={chemicals} value={f.chemical_id} onChange={e=>setF({...f,chemical_id:e.target.value})}/></div>
          <div className="field"><label>Qty</label><input value={f.qty} onChange={e=>setF({...f,qty:e.target.value})}/></div>
          <div className="field"><label>Rate</label><input value={f.rate} onChange={e=>setF({...f,rate:e.target.value})}/></div>
          <div className="field"><label>Tax</label><Opts items={taxes} value={f.tax_code_id} onChange={e=>setF({...f,tax_code_id:e.target.value})} placeholder="—"/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Create PO</button></div>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
    </Panel>
    <Panel title="Purchase orders">
      {pos.length===0?<div className="muted">None yet. GRNs raised against a confirmed PO roll it to PARTIAL / COMPLETED.</div>:
      <table><thead><tr><th>Number</th><th>Date</th><th className="num">Lines</th><th>Status</th><th></th></tr></thead>
      <tbody>{pos.map(p=>[
        <tr key={p.id}><td>{p.number}</td><td>{p.order_date}</td><td className="num">{p.lines}</td><td>{stBadge(p.status)}</td>
          <td className="num"><button className="btn sec" onClick={()=>view(p.id)}>View</button>{" "}
            {p.status==="DRAFT" && <button className="btn" onClick={()=>confirm(p.id)}>Confirm</button>}</td></tr>,
        open&&open.id===p.id ? <tr key={p.id+"x"}><td colSpan="5">
          <table><thead><tr><th>#</th><th>Item</th><th className="num">Ordered</th><th className="num">Received</th></tr></thead>
          <tbody>{open.lines.map(l=><tr key={l.id}><td>{l.line_no}</td><td>{l.description||l.chemical_id||"—"}</td>
            <td className="num">{inr2(l.qty)}</td><td className="num">{inr2(l.received_qty)}</td></tr>)}</tbody></table>
        </td></tr> : null
      ])}</tbody></table>}
    </Panel>
    <Panel title="Debit note (return chemicals to supplier)">
      <form onSubmit={createDN}>
        <div className="row">
          <div className="field"><label>Supplier</label><Opts items={suppliers} value={dn.supplier_id} onChange={e=>setDn({...dn,supplier_id:e.target.value})}/></div>
          <div className="field"><label>DN number</label><input value={dn.number} onChange={e=>setDn({...dn,number:e.target.value})}/></div>
          <div className="field"><label>Chemical</label><Opts items={chemicals} value={dn.chemical_id} onChange={e=>setDn({...dn,chemical_id:e.target.value})}/></div>
          <div className="field"><label>Qty returned</label><input value={dn.qty} onChange={e=>setDn({...dn,qty:e.target.value})}/></div>
          <div className="field"><label>Tax</label><Opts items={taxes} value={dn.tax_code_id} onChange={e=>setDn({...dn,tax_code_id:e.target.value})} placeholder="—"/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Post debit note</button></div>
        </div>
        <div className="row"><div className="field" style={{flex:1}}><label>Reason</label><input value={dn.reason} onChange={e=>setDn({...dn,reason:e.target.value})} placeholder="e.g. damaged goods"/></div></div>
        {dnErr && <div className="err">{dnErr}</div>}
      </form>
      <Result r={dnRes}/>
    </Panel>
  </div>);
}

// ---- transactions: sales orders + dispatch (SO -> delivery -> COGS) --------
function SalesOrders(){
  const customers=useList("/api/masters/customers"); const fabrics=useList("/api/masters/fabric-types");
  const taxes=useList("/api/masters/tax-codes");
  const [sos,setSos]=useState([]); const [rolls,setRolls]=useState([]); const [sel,setSel]=useState({});
  const [f,setF]=useState({customer_id:"",number:genNo("SO"),order_date:today(),
    fabric_type_id:"",qty_kg:"250",rate:"300",tax_code_id:""});
  const [dsp,setDsp]=useState({customer_id:"",sales_order_id:""});
  const [cn,setCn]=useState({customer_id:"",number:genNo("CN"),amount:"10000",tax_code_id:"",reason:""});
  const [rret,setRret]=useState({customer_id:"",number:genNo("RET"),amount:"",tax_code_id:""});
  const [rsel,setRsel]=useState({}); const [delivered,setDelivered]=useState([]);
  const [res,setRes]=useState(null); const [dres,setDres]=useState(null); const [retRes,setRetRes]=useState(null);
  const [cnRes,setCnRes]=useState(null); const [cnErr,setCnErr]=useState(""); const [err,setErr]=useState("");
  const load=useCallback(()=>{ api("/api/sales/orders").then(setSos).catch(()=>{});
    api("/api/rolls?undelivered=true").then(setRolls).catch(()=>{});
    api("/api/rolls").then(rs=>setDelivered((rs||[]).filter(r=>r.delivered))).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const chosen=rolls.filter(r=>sel[r.id]); const totalKg=chosen.reduce((s,r)=>s+Number(r.weight_kg||0),0);
  const create=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    try{ const r=await api("/api/sales/orders",{method:"POST",body:{
        customer_id:f.customer_id, number:f.number, order_date:f.order_date,
        lines:[{fabric_type_id:f.fabric_type_id||null, qty_kg:f.qty_kg, rate:f.rate, tax_code_id:f.tax_code_id||null}]}});
      setRes({so:r.number, status:r.status, ordered_kg:r.ordered_kg}); setF({...f,number:genNo("SO")}); load(); }
    catch(ex){ setErr(ex.message); } };
  const confirm=async id=>{ try{ await api(`/api/sales/orders/${id}/confirm`,{method:"POST"}); load(); }
    catch(ex){ setErr(ex.message); } };
  const dispatch=async e=>{ e.preventDefault(); setErr(""); setDres(null);
    if(chosen.length===0){ setErr("Select at least one undelivered roll"); return; }
    if(!dsp.customer_id){ setErr("Pick the customer to dispatch to"); return; }
    try{ const r=await api("/api/sales/deliveries",{method:"POST",body:{
        customer_id:dsp.customer_id, sales_order_id:dsp.sales_order_id||null,
        delivery_date:today(), roll_ids:chosen.map(r=>r.id)}});
      setDres(r); setSel({}); load(); }
    catch(ex){ setErr(ex.message); } };
  const createCN=async e=>{ e.preventDefault(); setCnErr(""); setCnRes(null);
    try{ const r=await api("/api/sales/credit-notes",{method:"POST",body:{
        customer_id:cn.customer_id, number:cn.number, note_date:today(), reason:cn.reason||null,
        lines:[{description:cn.reason||"Adjustment", qty:"1", rate:cn.amount, tax_code_id:cn.tax_code_id||null}]}});
      setCnRes(r); setCn({...cn,number:genNo("CN")}); }
    catch(ex){ setCnErr(ex.message); } };
  const retSel=Object.keys(rsel).filter(k=>rsel[k]);
  const doReturn=async e=>{ e.preventDefault(); setCnErr(""); setRetRes(null);
    if(!rret.customer_id){ setCnErr("Pick the customer"); return; }
    try{ const r=await api("/api/sales/returns",{method:"POST",body:{
        customer_id:rret.customer_id, number:rret.number, note_date:today(), reason:"Sales return",
        restock_roll_ids:retSel,
        lines:[{description:"Sales return", qty:"1", rate:rret.amount||"0", tax_code_id:rret.tax_code_id||null}]}});
      setRetRes(r); setRret({...rret,number:genNo("RET"),amount:""}); setRsel({}); load(); }
    catch(ex){ setCnErr(ex.message); } };
  return (<div>
    <Panel title="Create a sales order">
      <form onSubmit={create}>
        <div className="row mb">
          <div className="field"><label>Customer</label><Opts items={customers} value={f.customer_id} onChange={e=>setF({...f,customer_id:e.target.value})}/></div>
          <div className="field"><label>SO number</label><input value={f.number} onChange={e=>setF({...f,number:e.target.value})}/></div>
          <div className="field"><label>Order date</label><input type="date" value={f.order_date} onChange={e=>setF({...f,order_date:e.target.value})}/></div>
        </div>
        <div className="row">
          <div className="field"><label>Fabric</label><Opts items={fabrics} value={f.fabric_type_id} onChange={e=>setF({...f,fabric_type_id:e.target.value})}/></div>
          <div className="field"><label>Qty (kg)</label><input value={f.qty_kg} onChange={e=>setF({...f,qty_kg:e.target.value})}/></div>
          <div className="field"><label>Rate</label><input value={f.rate} onChange={e=>setF({...f,rate:e.target.value})}/></div>
          <div className="field"><label>Tax</label><Opts items={taxes} value={f.tax_code_id} onChange={e=>setF({...f,tax_code_id:e.target.value})} placeholder="—"/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Create SO</button></div>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
    </Panel>
    <Panel title="Sales orders">
      {sos.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Number</th><th>Date</th><th className="num">Ordered kg</th><th className="num">Delivered kg</th><th>Status</th><th></th></tr></thead>
      <tbody>{sos.map(s=><tr key={s.id}><td>{s.number}</td><td>{s.order_date}</td>
        <td className="num">{s.ordered_kg?inr2(s.ordered_kg):"—"}</td><td className="num">{s.delivered_kg?inr2(s.delivered_kg):"—"}</td>
        <td>{stBadge(s.status)}</td>
        <td className="num">{s.status==="DRAFT" && <button className="btn" onClick={()=>confirm(s.id)}>Confirm</button>}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Dispatch finished rolls (delivery challan → COGS)">
      <form onSubmit={dispatch}>
        <div className="row mb">
          <div className="field"><label>Customer</label><Opts items={customers} value={dsp.customer_id} onChange={e=>setDsp({...dsp,customer_id:e.target.value})}/></div>
          <div className="field"><label>Against sales order</label><Opts items={sos.map(s=>({id:s.id,name:s.number}))} value={dsp.sales_order_id} onChange={e=>setDsp({...dsp,sales_order_id:e.target.value})} placeholder="—"/></div>
          <div className="field"><label>Selected</label><div style={{padding:"9px 0",fontWeight:700}}>{chosen.length} rolls · {inr2(totalKg)} kg</div></div>
        </div>
        <table><thead><tr><th style={{width:40}}></th><th>Roll no.</th><th>Fabric</th><th className="num">Weight (kg)</th></tr></thead>
        <tbody>{rolls.length===0 ? <tr><td colSpan="4" className="muted">No undelivered rolls — record a knitting production first.</td></tr>
          : rolls.map(r=><tr key={r.id}><td><input type="checkbox" checked={!!sel[r.id]} onChange={()=>setSel(s=>({...s,[r.id]:!s[r.id]}))}/></td>
            <td>{r.roll_no}</td><td>{r.fabric||"—"}</td><td className="num">{inr2(r.weight_kg)}</td></tr>)}</tbody></table>
        <div className="row" style={{marginTop:12}}><button className="btn">Dispatch ({chosen.length} rolls)</button></div>
      </form>
      <Result r={dres}/>
    </Panel>
    <Panel title="Credit note (customer adjustment)">
      <form onSubmit={createCN}>
        <div className="row">
          <div className="field"><label>Customer</label><Opts items={customers} value={cn.customer_id} onChange={e=>setCn({...cn,customer_id:e.target.value})}/></div>
          <div className="field"><label>CN number</label><input value={cn.number} onChange={e=>setCn({...cn,number:e.target.value})}/></div>
          <div className="field"><label>Taxable amount</label><input value={cn.amount} onChange={e=>setCn({...cn,amount:e.target.value})}/></div>
          <div className="field"><label>Tax</label><Opts items={taxes} value={cn.tax_code_id} onChange={e=>setCn({...cn,tax_code_id:e.target.value})} placeholder="—"/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Post credit note</button></div>
        </div>
        <div className="row"><div className="field" style={{flex:1}}><label>Reason</label><input value={cn.reason} onChange={e=>setCn({...cn,reason:e.target.value})} placeholder="e.g. rate difference / short shipment"/></div></div>
        {cnErr && <div className="err">{cnErr}</div>}
      </form>
      <Result r={cnRes}/>
    </Panel>
    <Panel title="Sales return (credit note + restock)">
      <form onSubmit={doReturn}>
        <div className="row mb">
          <div className="field"><label>Customer</label><Opts items={customers} value={rret.customer_id} onChange={e=>setRret({...rret,customer_id:e.target.value})}/></div>
          <div className="field"><label>RET number</label><input value={rret.number} onChange={e=>setRret({...rret,number:e.target.value})}/></div>
          <div className="field"><label>Credit amount (taxable)</label><input value={rret.amount} onChange={e=>setRret({...rret,amount:e.target.value})}/></div>
          <div className="field"><label>Tax</label><Opts items={taxes} value={rret.tax_code_id} onChange={e=>setRret({...rret,tax_code_id:e.target.value})} placeholder="—"/></div>
          <div className="field"><label>Selected</label><div style={{padding:"9px 0",fontWeight:700}}>{retSel.length} rolls</div></div>
        </div>
        <div className="muted" style={{marginBottom:6,fontSize:12}}>Tick returned rolls to take them back into stock and reverse their COGS.</div>
        <table><thead><tr><th style={{width:40}}></th><th>Roll no.</th><th>Fabric</th><th className="num">Weight (kg)</th></tr></thead>
        <tbody>{delivered.length===0?<tr><td colSpan="4" className="muted">No delivered rolls to return.</td></tr>
          : delivered.map(r=><tr key={r.id}><td><input type="checkbox" checked={!!rsel[r.id]} onChange={()=>setRsel(s=>({...s,[r.id]:!s[r.id]}))}/></td>
            <td>{r.roll_no}</td><td>{r.fabric||"—"}</td><td className="num">{inr2(r.weight_kg)}</td></tr>)}</tbody></table>
        <div className="row" style={{marginTop:12}}><button className="btn">Post sales return</button></div>
      </form>
      <Result r={retRes}/>
    </Panel>
  </div>);
}

// ---- fixed assets & depreciation ------------------------------------------
function FixedAssets(){
  const accounts=useList("/api/accounts");
  const [assets,setAssets]=useState([]); const [res,setRes]=useState(null);
  const [dep,setDep]=useState(null); const [err,setErr]=useState("");
  const [month,setMonth]=useState(today().slice(0,7));
  const [f,setF]=useState({code:genNo("FA"),name:"",category:"",asset_account_code:"",
    acquisition_date:today(),cost:"120000",salvage_value:"0",method:"SLM",
    useful_life_months:"60",dep_rate_pct:"15"});
  const load=useCallback(()=>{ api("/api/assets").then(setAssets).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const assetAccts=accounts.filter(a=>a.code && a.code.indexOf("15")===0 && a.allow_posting && a.code!=="1590")
    .map(a=>({id:a.code,name:a.code+" — "+a.name}));
  const create=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    const body={code:f.code,name:f.name,category:f.category||null,asset_account_code:f.asset_account_code,
      acquisition_date:f.acquisition_date,cost:f.cost,salvage_value:f.salvage_value,method:f.method};
    if(f.method==="SLM") body.useful_life_months=Number(f.useful_life_months);
    else body.dep_rate_pct=f.dep_rate_pct;
    try{ const r=await api("/api/assets",{method:"POST",body});
      setRes({asset:r.code,book_value:r.book_value}); setF({...f,code:genNo("FA"),name:""}); load(); }
    catch(ex){ setErr(ex.message); } };
  const runDep=async()=>{ setErr(""); setDep(null);
    try{ setDep(await api("/api/assets/depreciation-runs",{method:"POST",body:{period_month:month+"-01"}})); load(); }
    catch(ex){ setErr(ex.message); } };
  const dispose=async a=>{ const proceeds=prompt(`Dispose ${a.code} — sale proceeds (₹)?`,"0"); if(proceeds===null) return;
    try{ await api(`/api/assets/${a.id}/dispose`,{method:"POST",body:{disposal_date:today(),proceeds:proceeds||"0"}}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Register (capitalise) a fixed asset">
      <form onSubmit={create}>
        <div className="row mb">
          <div className="field"><label>Code</label><input value={f.code} onChange={e=>setF({...f,code:e.target.value})}/></div>
          <div className="field"><label>Name</label><input value={f.name} onChange={e=>setF({...f,name:e.target.value})} required/></div>
          <div className="field"><label>Category</label><input value={f.category} onChange={e=>setF({...f,category:e.target.value})}/></div>
          <div className="field"><label>Asset account</label><Opts items={assetAccts} value={f.asset_account_code} onChange={e=>setF({...f,asset_account_code:e.target.value})}/></div>
        </div>
        <div className="row">
          <div className="field"><label>Acquired</label><input type="date" value={f.acquisition_date} onChange={e=>setF({...f,acquisition_date:e.target.value})}/></div>
          <div className="field"><label>Cost</label><input value={f.cost} onChange={e=>setF({...f,cost:e.target.value})}/></div>
          <div className="field"><label>Salvage</label><input value={f.salvage_value} onChange={e=>setF({...f,salvage_value:e.target.value})}/></div>
          <div className="field"><label>Method</label><select value={f.method} onChange={e=>setF({...f,method:e.target.value})}><option value="SLM">Straight-line</option><option value="WDV">Written-down value</option></select></div>
          {f.method==="SLM"
            ? <div className="field"><label>Life (months)</label><input value={f.useful_life_months} onChange={e=>setF({...f,useful_life_months:e.target.value})}/></div>
            : <div className="field"><label>Rate (% / yr)</label><input value={f.dep_rate_pct} onChange={e=>setF({...f,dep_rate_pct:e.target.value})}/></div>}
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Capitalise</button></div>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
    </Panel>
    <Panel title="Run monthly depreciation">
      <div className="row">
        <div className="field"><label>Period (month)</label><input type="month" value={month} onChange={e=>setMonth(e.target.value)}/></div>
        <div className="field" style={{alignSelf:"end"}}><button className="btn" onClick={runDep}>Run depreciation</button></div>
      </div>
      <Result r={dep}/>
    </Panel>
    <Panel title="Asset register">
      {assets.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Code</th><th>Name</th><th>Method</th><th className="num">Cost</th>
        <th className="num">Accum. dep.</th><th className="num">Book value</th><th>Status</th><th></th></tr></thead>
      <tbody>{assets.map(a=><tr key={a.id}><td>{a.code}</td><td>{a.name}</td><td>{a.method}</td>
        <td className="num">{inr2(a.cost)}</td><td className="num">{inr2(a.accumulated_depreciation)}</td>
        <td className="num">{inr2(a.book_value)}</td><td>{stBadge(a.status)}</td>
        <td className="num">{a.status==="ACTIVE" && <button className="btn sec" onClick={()=>dispose(a)}>Dispose</button>}</td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- payroll --------------------------------------------------------------
function Payroll(){
  const [emps,setEmps]=useState([]); const [structs,setStructs]=useState([]); const [runs,setRuns]=useState([]);
  const [month,setMonth]=useState(today().slice(0,7));
  const [ef,setEf]=useState({code:genNo("EMP"),name:"",designation:""});
  const [sf,setSf]=useState({employee_id:"",basic:"20000",hra:"8000",other_allowances:"2000",
    pf_applicable:true,esi_applicable:false,professional_tax:"200",tds_monthly:"1000"});
  const [eRes,setERes]=useState(null); const [sRes,setSRes]=useState(null); const [run,setRun]=useState(null);
  const [err,setErr]=useState("");
  const load=useCallback(()=>{ api("/api/payroll/employees").then(setEmps).catch(()=>{});
    api("/api/payroll/salary-structures").then(setStructs).catch(()=>{});
    api("/api/payroll/runs").then(setRuns).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const addEmp=async e=>{ e.preventDefault(); setErr(""); setERes(null);
    try{ const r=await api("/api/payroll/employees",{method:"POST",body:ef});
      setERes(r); setEf({code:genNo("EMP"),name:"",designation:""}); load(); }
    catch(ex){ setErr(ex.message); } };
  const setStruct=async e=>{ e.preventDefault(); setErr(""); setSRes(null);
    try{ const r=await api("/api/payroll/salary-structures",{method:"POST",
        body:{...sf, effective_from:month+"-01"}});
      setSRes(r); load(); }
    catch(ex){ setErr(ex.message); } };
  const runPay=async()=>{ setErr(""); setRun(null);
    try{ setRun(await api("/api/payroll/runs",{method:"POST",body:{period_month:month+"-01"}})); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <div className="row" style={{gap:16,alignItems:"flex-start"}}>
      <div style={{flex:1}}><Panel title="Add employee">
        <form onSubmit={addEmp}>
          <div className="field"><label>Code</label><input value={ef.code} onChange={e=>setEf({...ef,code:e.target.value})}/></div>
          <div className="field"><label>Name</label><input value={ef.name} onChange={e=>setEf({...ef,name:e.target.value})} required/></div>
          <div className="field"><label>Designation</label><input value={ef.designation} onChange={e=>setEf({...ef,designation:e.target.value})}/></div>
          <div style={{marginTop:10}}><button className="btn">Add employee</button></div>
        </form>
        <Result r={eRes}/>
      </Panel></div>
      <div style={{flex:2}}><Panel title="Set salary structure">
        <form onSubmit={setStruct}>
          <div className="row mb">
            <div className="field"><label>Employee</label><Opts items={emps} value={sf.employee_id} onChange={e=>setSf({...sf,employee_id:e.target.value})}/></div>
            <div className="field"><label>Basic</label><input value={sf.basic} onChange={e=>setSf({...sf,basic:e.target.value})}/></div>
            <div className="field"><label>HRA</label><input value={sf.hra} onChange={e=>setSf({...sf,hra:e.target.value})}/></div>
            <div className="field"><label>Allowances</label><input value={sf.other_allowances} onChange={e=>setSf({...sf,other_allowances:e.target.value})}/></div>
          </div>
          <div className="row">
            <div className="field"><label>Prof. tax</label><input value={sf.professional_tax} onChange={e=>setSf({...sf,professional_tax:e.target.value})}/></div>
            <div className="field"><label>TDS / month</label><input value={sf.tds_monthly} onChange={e=>setSf({...sf,tds_monthly:e.target.value})}/></div>
            <div className="field"><label>PF?</label><select value={sf.pf_applicable?"y":"n"} onChange={e=>setSf({...sf,pf_applicable:e.target.value==="y"})}><option value="y">Yes</option><option value="n">No</option></select></div>
            <div className="field"><label>ESI?</label><select value={sf.esi_applicable?"y":"n"} onChange={e=>setSf({...sf,esi_applicable:e.target.value==="y"})}><option value="n">No</option><option value="y">Yes</option></select></div>
            <div className="field" style={{alignSelf:"end"}}><button className="btn">Save structure</button></div>
          </div>
        </form>
        <Result r={sRes}/>
      </Panel></div>
    </div>
    <Panel title="Salary structures">
      {structs.length===0?<div className="muted">None yet — add an employee and set a structure.</div>:
      <table><thead><tr><th>Employee</th><th className="num">Basic</th><th className="num">Gross</th>
        <th className="num">Deductions</th><th className="num">Net pay</th></tr></thead>
      <tbody>{structs.map(s=><tr key={s.id}><td>{s.employee_code} — {s.employee}</td>
        <td className="num">{inr2(s.basic)}</td><td className="num">{inr2(s.gross)}</td>
        <td className="num">{inr2(s.deductions)}</td><td className="num">{inr2(s.net_pay)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Run monthly payroll">
      <div className="row">
        <div className="field"><label>Pay month</label><input type="month" value={month} onChange={e=>setMonth(e.target.value)}/></div>
        <div className="field" style={{alignSelf:"end"}}><button className="btn" onClick={runPay}>Run payroll</button></div>
      </div>
      <Result r={run}/>
      {err && <div className="err">{err}</div>}
    </Panel>
    <Panel title="Payroll runs">
      {runs.length===0?<div className="muted">None yet.</div>:
      <table><thead><tr><th>Period</th><th className="num">Employees</th><th className="num">Gross</th>
        <th className="num">Net paid</th><th>Status</th></tr></thead>
      <tbody>{runs.map(r=><tr key={r.id}><td>{r.period}</td><td className="num">{r.employees}</td>
        <td className="num">{inr2(r.total_gross)}</td><td className="num">{inr2(r.total_net)}</td>
        <td>{stBadge(r.status)}</td></tr>)}</tbody></table>}
    </Panel>
  </div>);
}

// ---- approvals (maker-checker) --------------------------------------------
function Approvals(){
  const suppliers=useList("/api/masters/suppliers"); const banks=useList("/api/banking/accounts");
  const [rules,setRules]=useState([]); const [reqs,setReqs]=useState([]);
  const [rf,setRf]=useState({min_amount:"100000",approvals_required:"1"});
  const [pf,setPf]=useState({supplier_id:"",bank_account_id:"",amount:"150000",note:""});
  const [rRes,setRRes]=useState(null); const [pRes,setPRes]=useState(null); const [err,setErr]=useState("");
  const load=useCallback(()=>{ api("/api/approvals/rules").then(setRules).catch(()=>{});
    api("/api/approvals/requests?status_filter=PENDING").then(setReqs).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const addRule=async e=>{ e.preventDefault(); setErr(""); setRRes(null);
    try{ const r=await api("/api/approvals/rules",{method:"POST",body:{doc_type:"PAYMENT",
        min_amount:rf.min_amount, approvals_required:Number(rf.approvals_required)}});
      setRRes(r); load(); }catch(ex){ setErr(ex.message); } };
  const reqPay=async e=>{ e.preventDefault(); setErr(""); setPRes(null);
    try{ const r=await api("/api/approvals/payments",{method:"POST",body:{
        supplier_id:pf.supplier_id, bank_account_id:pf.bank_account_id, amount:pf.amount, note:pf.note||null}});
      setPRes(r); load(); }catch(ex){ setErr(ex.message); } };
  const decide=async(id,action)=>{ setErr("");
    try{ await api(`/api/approvals/requests/${id}/${action}`,{method:"POST",body:{}}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <div className="row" style={{gap:16,alignItems:"flex-start"}}>
      <div style={{flex:1}}><Panel title="Payment approval threshold">
        <form onSubmit={addRule}>
          <div className="field"><label>Needs approval at or above (₹)</label><input value={rf.min_amount} onChange={e=>setRf({...rf,min_amount:e.target.value})}/></div>
          <div className="field"><label>Approvals required</label><input value={rf.approvals_required} onChange={e=>setRf({...rf,approvals_required:e.target.value})}/></div>
          <div style={{marginTop:10}}><button className="btn">Save rule</button></div>
        </form>
        <Result r={rRes}/>
        {rules.length>0 && <table style={{marginTop:12}}><thead><tr><th>Type</th><th className="num">Threshold</th><th className="num">Approvals</th></tr></thead>
          <tbody>{rules.map(r=><tr key={r.id}><td>{r.doc_type}</td><td className="num">{inr2(r.min_amount)}</td><td className="num">{r.approvals_required}</td></tr>)}</tbody></table>}
      </Panel></div>
      <div style={{flex:1}}><Panel title="Request a supplier payment">
        <form onSubmit={reqPay}>
          <div className="field"><label>Supplier</label><Opts items={suppliers} value={pf.supplier_id} onChange={e=>setPf({...pf,supplier_id:e.target.value})}/></div>
          <div className="field"><label>Bank account</label><Opts items={banks} value={pf.bank_account_id} onChange={e=>setPf({...pf,bank_account_id:e.target.value})}/></div>
          <div className="field"><label>Amount (₹)</label><input value={pf.amount} onChange={e=>setPf({...pf,amount:e.target.value})}/></div>
          <div className="field"><label>Note</label><input value={pf.note} onChange={e=>setPf({...pf,note:e.target.value})}/></div>
          <div style={{marginTop:10}}><button className="btn">Request payment</button></div>
        </form>
        <Result r={pRes}/>
      </Panel></div>
    </div>
    <Panel title="Pending approvals">
      <div className="muted" style={{marginBottom:8,fontSize:12}}>Maker-checker: the person who requested a payment cannot approve it — a different approver must sign off.</div>
      {reqs.length===0?<div className="muted">Nothing awaiting approval.</div>:
      <table><thead><tr><th>Document</th><th className="num">Amount</th><th className="num">Approvals</th><th></th></tr></thead>
      <tbody>{reqs.map(r=><tr key={r.id}><td>{r.title}</td><td className="num">{inr2(r.amount)}</td>
        <td className="num">{r.approvals_received}/{r.approvals_required}</td>
        <td className="num"><button className="btn" onClick={()=>decide(r.id,"approve")}>Approve</button>{" "}
          <button className="btn sec" onClick={()=>decide(r.id,"reject")}>Reject</button></td></tr>)}</tbody></table>}
      {err && <div className="err">{err}</div>}
    </Panel>
  </div>);
}

// ---- GST adjustments & year-end close -------------------------------------
function AccountingOps(){
  const [g,setG]=useState({kind:"itc-reversal",reference:genNo("GA"),cgst:"900",sgst:"900",igst:"0"});
  const [ro,setRo]=useState({reference:genNo("RO"),amount:"0.40"});
  const [fy,setFy]=useState("2026-2027");
  const [gRes,setGRes]=useState(null); const [roRes,setRoRes]=useState(null); const [yRes,setYRes]=useState(null);
  const [gErr,setGErr]=useState(""); const [roErr,setRoErr]=useState(""); const [yErr,setYErr]=useState("");
  const postGst=async e=>{ e.preventDefault(); setGErr(""); setGRes(null);
    try{ const r=await api(`/api/accounting/gst-adjustments/${g.kind}`,{method:"POST",body:{
        vdate:today(), reference:g.reference, cgst:g.cgst||"0", sgst:g.sgst||"0", igst:g.igst||"0"}});
      setGRes(r); setG({...g,reference:genNo("GA")}); }
    catch(ex){ setGErr(ex.message); } };
  const postRound=async e=>{ e.preventDefault(); setRoErr(""); setRoRes(null);
    try{ const r=await api("/api/accounting/gst-adjustments/round-off",{method:"POST",body:{
        vdate:today(), reference:ro.reference, amount:ro.amount}});
      setRoRes(r); setRo({...ro,reference:genNo("RO")}); }
    catch(ex){ setRoErr(ex.message); } };
  const closeYear=async e=>{ e.preventDefault(); setYErr(""); setYRes(null);
    if(!confirm(`Close the books for ${fy}? Income and expense accounts will be moved to retained earnings and every period in the year will be locked.`)) return;
    try{ setYRes(await api("/api/accounting/year-end/close",{method:"POST",body:{fiscal_year:fy}})); }
    catch(ex){ setYErr(ex.message); } };
  return (<div>
    <Panel title="GST adjustment voucher">
      <form onSubmit={postGst}>
        <div className="row mb">
          <div className="field"><label>Type</label><select value={g.kind} onChange={e=>setG({...g,kind:e.target.value})}>
            <option value="itc-reversal">ITC reversal</option>
            <option value="itc-reclaim">ITC reclaim</option>
            <option value="rcm">Reverse charge (RCM)</option></select></div>
          <div className="field"><label>Reference</label><input value={g.reference} onChange={e=>setG({...g,reference:e.target.value})}/></div>
        </div>
        <div className="row">
          <div className="field"><label>CGST</label><input value={g.cgst} onChange={e=>setG({...g,cgst:e.target.value})}/></div>
          <div className="field"><label>SGST</label><input value={g.sgst} onChange={e=>setG({...g,sgst:e.target.value})}/></div>
          <div className="field"><label>IGST</label><input value={g.igst} onChange={e=>setG({...g,igst:e.target.value})}/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Post adjustment</button></div>
        </div>
        <div className="muted" style={{marginTop:8,fontSize:12}}>
          {g.kind==="itc-reversal" ? "Dr Reversal expense · Cr Input CGST/SGST/IGST"
            : g.kind==="itc-reclaim" ? "Dr Input CGST/SGST/IGST · Cr Reversal expense (mirror of reversal)"
            : "Dr Input CGST/SGST/IGST · Cr Output CGST/SGST/IGST (net-nil to P&L)"}</div>
        {gErr && <div className="err">{gErr}</div>}
      </form>
      <Result r={gRes}/>
    </Panel>
    <Panel title="GST round off">
      <form onSubmit={postRound}>
        <div className="row">
          <div className="field"><label>Reference</label><input value={ro.reference} onChange={e=>setRo({...ro,reference:e.target.value})}/></div>
          <div className="field"><label>Amount (± ₹)</label><input value={ro.amount} onChange={e=>setRo({...ro,amount:e.target.value})}/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Post round off</button></div>
        </div>
        <div className="muted" style={{marginTop:8,fontSize:12}}>Positive → Dr receivable · Cr Rounding Off (gain); negative → Dr Rounding Off · Cr receivable (cost).</div>
        {roErr && <div className="err">{roErr}</div>}
      </form>
      <Result r={roRes}/>
    </Panel>
    <Panel title="Year-end close">
      <form onSubmit={closeYear}>
        <div className="row">
          <div className="field"><label>Fiscal year</label><input value={fy} onChange={e=>setFy(e.target.value)} placeholder="2026-2027"/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Close financial year</button></div>
        </div>
        <div className="muted" style={{marginTop:8,fontSize:12}}>Zeroes income &amp; expense accounts into retained earnings and locks every period in the year. Idempotent.</div>
        {yErr && <div className="err">{yErr}</div>}
      </form>
      <Result r={yRes}/>
    </Panel>
  </div>);
}

// ---- inventory ops: transfers + roll split/merge --------------------------
function InventoryOps(){
  const chemicals=useList("/api/masters/chemicals"); const warehouses=useList("/api/masters/warehouses");
  const [rolls,setRolls]=useState([]); const [transfers,setTransfers]=useState([]);
  const [tf,setTf]=useState({item_id:"",from_warehouse_id:"",to_warehouse_id:"",qty:"100",method:"FIFO"});
  const [sp,setSp]=useState({roll_id:"",a:"100",b:"150"});
  const [mSel,setMSel]=useState({});
  const [cf,setCf]=useState({item_id:"",warehouse_id:"",counted_qty:"",reason:""});
  const [tRes,setTRes]=useState(null); const [spRes,setSpRes]=useState(null); const [mRes,setMRes]=useState(null);
  const [cRes,setCRes]=useState(null);
  const [err,setErr]=useState("");
  const count=e=>{ e.preventDefault(); setCRes(null); go(async()=>{
    setCRes(await api("/api/inventory/stock-count",{method:"POST",body:{item_type:"CHEMICAL",
      item_id:cf.item_id, warehouse_id:cf.warehouse_id||null, counted_qty:cf.counted_qty, reason:cf.reason||null}}));
    setCf({...cf,counted_qty:""}); }); };
  const load=useCallback(()=>{ api("/api/rolls?undelivered=true").then(setRolls).catch(()=>{});
    api("/api/inventory/transfers").then(setTransfers).catch(()=>{}); },[]);
  useEffect(()=>{load()},[load]);
  const go=async(fn)=>{ setErr(""); try{ await fn(); load(); }catch(ex){ setErr(ex.message); } };
  const transfer=e=>{ e.preventDefault(); setTRes(null); go(async()=>{
    setTRes(await api("/api/inventory/transfers",{method:"POST",body:{item_type:"CHEMICAL",
      item_id:tf.item_id, from_warehouse_id:tf.from_warehouse_id, to_warehouse_id:tf.to_warehouse_id,
      qty:tf.qty, method:tf.method}})); }); };
  const split=e=>{ e.preventDefault(); setSpRes(null); go(async()=>{
    const parts=[{weight_kg:sp.a}]; if(Number(sp.b)>0) parts.push({weight_kg:sp.b});
    const r=await api(`/api/inventory/rolls/${sp.roll_id}/split`,{method:"POST",body:{parts}});
    setSpRes({parent_remaining_kg:r.parent_remaining_kg, parent_status:r.parent_status,
      children:(r.children||[]).map(c=>`${c.roll_no} (${c.weight_kg} kg)`).join(", ")});
    setSp({...sp,roll_id:""}); }); };
  const mergeSel=Object.keys(mSel).filter(k=>mSel[k]);
  const merge=e=>{ e.preventDefault(); setMRes(null);
    if(mergeSel.length<2){ setErr("Select at least two rolls to merge"); return; }
    go(async()=>{ setMRes(await api("/api/inventory/rolls/merge",{method:"POST",body:{roll_ids:mergeSel}}));
      setMSel({}); }); };
  return (<div>
    <Panel title="Physical stock count (inventory adjustment)">
      <form onSubmit={count}>
        <div className="row">
          <div className="field"><label>Chemical</label><Opts items={chemicals} value={cf.item_id} onChange={e=>setCf({...cf,item_id:e.target.value})}/></div>
          <div className="field"><label>Warehouse</label><Opts items={warehouses} value={cf.warehouse_id} onChange={e=>setCf({...cf,warehouse_id:e.target.value})} placeholder="— all —"/></div>
          <div className="field"><label>Counted qty</label><input value={cf.counted_qty} onChange={e=>setCf({...cf,counted_qty:e.target.value})}/></div>
          <div className="field"><label>Reason</label><input value={cf.reason} onChange={e=>setCf({...cf,reason:e.target.value})} placeholder="e.g. spillage"/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Post count</button></div>
        </div>
        <div className="muted" style={{marginTop:8,fontSize:12}}>Posts the costed variance vs book stock: shortage → Dr Inventory adjustment / Cr inventory; surplus → the reverse.</div>
      </form>
      <Result r={cRes}/>
    </Panel>
    <Panel title="Stock transfer (between warehouses)">
      <form onSubmit={transfer}>
        <div className="row">
          <div className="field"><label>Chemical</label><Opts items={chemicals} value={tf.item_id} onChange={e=>setTf({...tf,item_id:e.target.value})}/></div>
          <div className="field"><label>From</label><Opts items={warehouses} value={tf.from_warehouse_id} onChange={e=>setTf({...tf,from_warehouse_id:e.target.value})}/></div>
          <div className="field"><label>To</label><Opts items={warehouses} value={tf.to_warehouse_id} onChange={e=>setTf({...tf,to_warehouse_id:e.target.value})}/></div>
          <div className="field"><label>Qty</label><input value={tf.qty} onChange={e=>setTf({...tf,qty:e.target.value})}/></div>
          <div className="field"><label>Method</label><select value={tf.method} onChange={e=>setTf({...tf,method:e.target.value})}><option>FIFO</option><option>FEFO</option></select></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Transfer</button></div>
        </div>
      </form>
      <Result r={tRes}/>
      {transfers.length>0 && <table style={{marginTop:12}}><thead><tr><th>Number</th><th>Date</th><th className="num">Qty</th><th className="num">Value</th></tr></thead>
        <tbody>{transfers.map(t=><tr key={t.id}><td>{t.number}</td><td>{t.date}</td><td className="num">{inr2(t.qty)}</td><td className="num">{inr2(t.cost_value)}</td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Split a fabric roll">
      <form onSubmit={split}>
        <div className="row">
          <div className="field" style={{flex:2}}><label>Roll</label><Opts items={rolls.map(r=>({id:r.id,name:`${r.roll_no} (${r.weight_kg} kg)`}))} value={sp.roll_id} onChange={e=>setSp({...sp,roll_id:e.target.value})}/></div>
          <div className="field"><label>Part 1 (kg)</label><input value={sp.a} onChange={e=>setSp({...sp,a:e.target.value})}/></div>
          <div className="field"><label>Part 2 (kg)</label><input value={sp.b} onChange={e=>setSp({...sp,b:e.target.value})}/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Split roll</button></div>
        </div>
        <div className="muted" style={{marginTop:8,fontSize:12}}>Parts can't exceed the roll weight; the parent keeps any remainder (or is closed when fully split).</div>
      </form>
      <Result r={spRes}/>
    </Panel>
    <Panel title="Merge fabric rolls">
      <form onSubmit={merge}>
        <table><thead><tr><th style={{width:40}}></th><th>Roll no.</th><th>Fabric</th><th className="num">Weight (kg)</th></tr></thead>
        <tbody>{rolls.length===0?<tr><td colSpan="4" className="muted">No active undelivered rolls.</td></tr>
          : rolls.map(r=><tr key={r.id}><td><input type="checkbox" checked={!!mSel[r.id]} onChange={()=>setMSel(s=>({...s,[r.id]:!s[r.id]}))}/></td>
            <td>{r.roll_no}</td><td>{r.fabric||"—"}</td><td className="num">{inr2(r.weight_kg)}</td></tr>)}</tbody></table>
        <div className="row" style={{marginTop:12}}><button className="btn">Merge ({mergeSel.length} rolls)</button></div>
      </form>
      <Result r={mRes}/>
      {err && <div className="err">{err}</div>}
    </Panel>
  </div>);
}

const PAGES={
  owner:{t:"Owner Dashboard",c:OwnerDash}, factory:{t:"Factory Dashboard",c:FactoryDash},
  accounts:{t:"Accounts Dashboard",c:AccountsDash}, chemical:{t:"Chemical Dashboard",c:ChemicalDash},
  purchaseorders:{t:"Purchase Orders",c:PurchaseOrders},
  purchase:{t:"Purchase (GRN)",c:Purchase}, knitting:{t:"Knitting Production",c:Knitting},
  batching:{t:"Batching",c:Batching},
  inventoryops:{t:"Inventory Ops (transfer / split / merge)",c:InventoryOps},
  pipeline:{t:"Sales Pipeline (enquiry → quote → SO)",c:SalesPipeline},
  salesorders:{t:"Sales Orders & Dispatch",c:SalesOrders},
  sales:{t:"Sales Invoice",c:Sales}, dyeing:{t:"Dyeing & Dispensing",c:Dyeing},
  customers:{t:"Customers",c:()=> <PartyPage kind="customers"/>},
  suppliers:{t:"Suppliers",c:()=> <PartyPage kind="suppliers"/>},
  warehouses:{t:"Warehouses",c:Warehouses}, quality:{t:"Quality & Finishing",c:Quality},
  assets:{t:"Fixed Assets & Depreciation",c:FixedAssets},
  payroll:{t:"Payroll",c:Payroll},
  approvals:{t:"Approvals (maker-checker)",c:Approvals},
  acctops:{t:"GST Adjustments & Year-End",c:AccountingOps},
  costing:{t:"Costing & Profitability",c:Costing}, devices:{t:"Devices & Labels",c:Devices},
  masters:{t:"Master Data",c:MastersManager}, vouchers:{t:"Vouchers",c:Vouchers}, admin:{t:"Administration",c:Admin},
  reports:{t:"Reports",c:Reports},
};
const NAV_ICON={owner:"📊",factory:"🏭",accounts:"💼",chemical:"🧪",purchaseorders:"📝",purchase:"📥",
  knitting:"🧶",batching:"🧺",pipeline:"🎯",salesorders:"🛒",sales:"🧾",dyeing:"🎨",vouchers:"📒",
  inventoryops:"🔁",masters:"🗃️",customers:"👤",suppliers:"🏢",warehouses:"🏬",quality:"🔍",assets:"🏗️",
  payroll:"💰",costing:"📐",devices:"🖨️",reports:"📈",approvals:"✔️",acctops:"🧮",admin:"⚙️"};
function fyLabel(){ const d=new Date(); const y=d.getMonth()>=3?d.getFullYear():d.getFullYear()-1; return `FY ${y}–${(y+1)%100}`; }

function App(){
  const [page,setPage]=useState("owner"); const [me,setMe]=useState(null);
  useEffect(()=>{ api("/api/auth/me").then(setMe).catch(()=>{}); },[]);
  const Cur=PAGES[page].c;
  const navBtn=(id,label)=> <button className={page===id?"active":""} onClick={()=>setPage(id)}>
    <span className="nav-ico">{NAV_ICON[id]||"•"}</span>{label}</button>;
  return (<div className="app">
    <aside className="sidebar">
      <div className="brand">◆ Textile ERP<small>Knit · Dye · Finish · Grow</small></div>
      <nav className="nav">
        <div className="group">Dashboards</div>
        {navBtn("owner","Owner")}{navBtn("factory","Factory")}
        {navBtn("accounts","Accounts")}{navBtn("chemical","Chemical")}
        <div className="group">Transactions</div>
        {navBtn("purchaseorders","Purchase Orders")}{navBtn("purchase","Purchase (GRN)")}{navBtn("knitting","Knitting")}
        {navBtn("batching","Batching")}{navBtn("pipeline","Sales Pipeline")}{navBtn("salesorders","Sales Orders")}{navBtn("sales","Sales Invoice")}{navBtn("dyeing","Dyeing & Dispensing")}{navBtn("vouchers","Vouchers")}
        {navBtn("inventoryops","Inventory Ops")}
        <div className="group">Masters</div>
        {navBtn("masters","Master Data")}{navBtn("customers","Customers")}{navBtn("suppliers","Suppliers")}{navBtn("warehouses","Warehouses")}
        <div className="group">Quality</div>{navBtn("quality","Quality")}
        <div className="group">Assets</div>{navBtn("assets","Fixed Assets")}
        <div className="group">Payroll</div>{navBtn("payroll","Payroll")}
        <div className="group">Costing</div>{navBtn("costing","Costing")}
        <div className="group">Devices</div>{navBtn("devices","Devices & Labels")}
        <div className="group">Reports</div>{navBtn("reports","Reports")}
        <div className="group">Admin</div>{navBtn("approvals","Approvals")}{navBtn("acctops","GST Adj / Year-End")}{navBtn("admin","Administration")}
        <div className="group">Session</div>
        <button onClick={()=>{setToken(null); location.reload();}}>Log out</button>
      </nav>
    </aside>
    <div className="main">
      <div className="topbar"><h1>{PAGES[page].t}</h1>
        <div className="who-wrap">
          <span className="fychip">{fyLabel()}</span>
          <div className="who">{me? (me.full_name||me.username) : ""} · {new Date().toLocaleDateString("en-IN")}</div>
        </div></div>
      <div className="content"><Cur/></div>
    </div>
  </div>);
}

function Root(){
  const [authed,setAuthed]=useState(!!getToken());
  return authed ? <App/> : <Login onLogin={()=>setAuthed(true)} />;
}
ReactDOM.createRoot(document.getElementById("root")).render(<Root/>);
