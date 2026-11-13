// Source JSX for the ERP SPA. Precompiled to app.js (see build-ui.sh).
const {useState, useEffect, useCallback} = React;
const R = window.Recharts || null;

// ---- api helper ----------------------------------------------------------
const TOKEN_KEY = "erp_token";
const COMPANY_KEY = "erp_company_id", FACTORY_KEY = "erp_factory_id";
function getToken(){ try{ return localStorage.getItem(TOKEN_KEY) }catch(e){ return null } }
function setToken(t){ try{ t?localStorage.setItem(TOKEN_KEY,t):localStorage.removeItem(TOKEN_KEY) }catch(e){} }
// Active company/factory scope — sent as request headers so switching actually
// changes the data the backend returns (authorization is validated server-side).
function getScope(k){ try{ return localStorage.getItem(k)||"" }catch(e){ return "" } }
function setScope(k,v){ try{ v?localStorage.setItem(k,v):localStorage.removeItem(k) }catch(e){} }

async function api(path, {method, body, form, blob}={}){
  if(!method) method = (form || body!==undefined) ? "POST" : "GET";
  const headers={}; const tok=getToken(); if(tok) headers["Authorization"]="Bearer "+tok;
  const cid=getScope(COMPANY_KEY); if(cid) headers["X-Company-Id"]=cid;
  const fid=getScope(FACTORY_KEY); if(fid) headers["X-Factory-Id"]=fid;
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

// Richer KPI card: coloured icon chip + value + optional % delta. Clickable when
// onClick is given (shows a hover affordance + drills into the module).
function Stat({icon, label, value, sub, delta, tone, onClick}){
  const d = (delta===undefined||delta===null) ? null : Number(delta);
  return (<div className={"stat "+(tone||"")+(onClick?" clickable":"")}
      onClick={onClick} role={onClick?"button":undefined} tabIndex={onClick?0:undefined}
      onKeyDown={onClick?(e=>{(e.key==="Enter"||e.key===" ")&&onClick();}):undefined}>
    <div className="stat-icon">{icon}</div>
    <div className="stat-body">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
      {d!==null && <div className={"stat-delta "+(d>=0?"up":"down")}>
        {d>=0?"▲":"▼"} {Math.abs(d).toFixed(1)}% vs last month</div>}
    </div>
  </div>);
}

function Panel({title, children}){ return (
  <div className="panel">{title && <h3>{title}</h3>}{children}</div> ); }

// Reusable enterprise table: client-side search, sortable headers, pagination.
// columns: [{key, label, num?, sortable?, render?(row)}]. searchKeys defaults to
// all column keys. Row actions = a column with a render returning buttons.
function DataTable({columns, rows, searchKeys, pageSize=10, empty="Nothing to show.", initialSort, right, rowClass, expand}){
  const [q,setQ]=useState(""); const [sort,setSort]=useState(initialSort||null); const [page,setPage]=useState(0);
  const [open,setOpen]=useState(null);   // id of the expanded row
  const sk = searchKeys || columns.map(c=>c.key);
  let data = rows||[];
  if(q){ const qq=q.toLowerCase(); data=data.filter(r=> sk.some(k=> String(r[k]??"").toLowerCase().includes(qq))); }
  if(sort){ const {key,dir}=sort; data=[...data].sort((a,b)=>{ let x=a[key],y=b[key];
    const nx=Number(x),ny=Number(y);
    if(x!==""&&y!==""&&x!=null&&y!=null&&!isNaN(nx)&&!isNaN(ny)){x=nx;y=ny;}
    else {x=String(x??"").toLowerCase(); y=String(y??"").toLowerCase();}
    return (x<y?-1:x>y?1:0)*(dir==="asc"?1:-1); }); }
  const total=data.length; const pages=Math.max(1,Math.ceil(total/pageSize));
  const pg=Math.min(page,pages-1); const slice=data.slice(pg*pageSize,pg*pageSize+pageSize);
  const toggle=key=>setSort(s=> s&&s.key===key ? {key,dir:s.dir==="asc"?"desc":"asc"} : {key,dir:"asc"});
  const arrow=key=> sort&&sort.key===key ? (sort.dir==="asc"?" ▲":" ▼") : "";
  return (<div>
    <div className="dt-toolbar">
      <input className="dt-search" placeholder="Search…" value={q} onChange={e=>{setQ(e.target.value); setPage(0);}}/>
      {right}
      <span style={{flex:1}}/>
      <span className="dt-count">{total} row{total!==1?"s":""}</span>
    </div>
    {total===0? <div className="muted">{empty}</div> :
    <table><thead><tr>
      {expand && <th className="dt-expcol"></th>}
      {columns.map(c=><th key={c.key} className={(c.num?"num ":"")+(c.sortable!==false?"dt-th":"")}
        onClick={()=>c.sortable!==false&&toggle(c.key)}>{c.label}{c.sortable!==false?arrow(c.key):""}</th>)}</tr></thead>
    <tbody>{slice.map((r,i)=>{ const id=r.id||i; const isOpen=expand&&open===id;
      return <React.Fragment key={id}>
        <tr className={rowClass?rowClass(r):undefined}>
          {expand && <td className="dt-expcol"><button className="dt-exp" aria-label="expand"
            onClick={()=>setOpen(isOpen?null:id)}>{isOpen?"▾":"▸"}</button></td>}
          {columns.map(c=><td key={c.key} className={c.num?"num":""}>{c.render?c.render(r):(r[c.key]??"")}</td>)}
        </tr>
        {isOpen && <tr className="dt-detail"><td colSpan={columns.length+1}>{expand(r)}</td></tr>}
      </React.Fragment>; })}</tbody></table>}
    {pages>1 && <div className="dt-pager">
      <button className="btn sec" disabled={pg===0} onClick={()=>setPage(pg-1)}>‹ Prev</button>
      <span className="muted">Page {pg+1} of {pages}</span>
      <button className="btn sec" disabled={pg>=pages-1} onClick={()=>setPage(pg+1)}>Next ›</button>
    </div>}
  </div>);
}

// Reusable line-items editor for transaction forms: configurable input columns,
// add/remove rows, an optional computed per-row amount and a live subtotal.
// NOTE: statutory GST/total are posted by the backend — the subtotal here is the
// taxable (qty x rate) figure only, shown for data entry, never as the tax source.
function LineItems({columns, rows, setRows, newRow, amount, amountLabel="Taxable subtotal", note, minRows=1, footer}){
  const set=(i,k,v)=>setRows(rs=>rs.map((r,j)=>j===i?{...r,[k]:v}:r));
  const del=i=>setRows(rs=>rs.length>minRows?rs.filter((_,j)=>j!==i):rs);
  const subtotal = amount? rows.reduce((s,r)=>s+Number(amount(r)||0),0) : null;
  return (<div>
    <table><thead><tr>
      {columns.map(c=><th key={c.key} className={c.num?"num":""}>{c.label}</th>)}
      {amount && <th className="num">Amount</th>}<th style={{width:36}}></th></tr></thead>
    <tbody>{rows.map((r,i)=><tr key={i}>
      {columns.map(c=><td key={c.key} className={c.num?"num":""}>
        {c.type==="opts"? <Opts items={c.items||[]} value={r[c.key]} onChange={e=>set(i,c.key,e.target.value)} placeholder={c.placeholder||"—"}/>
         : c.type==="select"? <select value={r[c.key]} onChange={e=>set(i,c.key,e.target.value)}>{(c.options||[]).map(o=><option key={o[0]} value={o[0]}>{o[1]}</option>)}</select>
         : <input style={{width:c.width||120}} value={r[c.key]} onChange={e=>set(i,c.key,e.target.value)} placeholder={c.placeholder}/>}
      </td>)}
      {amount && <td className="num">{inr2(amount(r))}</td>}
      <td>{rows.length>minRows && <button type="button" className="btn sec" title="Remove" onClick={()=>del(i)}>✕</button>}</td>
    </tr>)}</tbody>
    {footer? <tfoot>{footer(rows)}</tfoot>
      : amount? <tfoot><tr><th colSpan={columns.length}>{amountLabel}</th><th className="num">{inr2(subtotal)}</th><th></th></tr></tfoot> : null}
    </table>
    <div className="row" style={{marginTop:10,alignItems:"center",gap:12}}>
      <button type="button" className="btn sec" onClick={()=>setRows(rs=>[...rs,newRow()])}>+ Add line</button>
      {note && <span className="muted" style={{fontSize:12}}>{note}</span>}
    </div>
  </div>);
}

// Lazy line-item detail panels for expandable order rows.
function PODetail({id, names}){
  const [d]=useData("/api/procurement/purchase-orders/"+id);
  if(!d) return <div className="muted">Loading…</div>;
  const nm=l=>l.description||(names&&names[l.chemical_id])||(l.chemical_id?"Chemical":"—");
  return (<table><thead><tr><th>#</th><th>Item</th><th className="num">Ordered</th><th className="num">Received</th></tr></thead>
    <tbody>{(d.lines||[]).map(l=><tr key={l.id}><td>{l.line_no}</td><td>{nm(l)}</td>
      <td className="num">{inr2(l.qty)}</td><td className="num">{inr2(l.received_qty)}</td></tr>)}</tbody></table>);
}
function SODetail({id}){
  const [d]=useData("/api/sales/orders/"+id);
  if(!d) return <div className="muted">Loading…</div>;
  return (<table><thead><tr><th>#</th><th>Fabric</th><th className="num">Qty (kg)</th><th className="num">Rate</th><th className="num">Amount</th></tr></thead>
    <tbody>{(d.lines||[]).map(l=><tr key={l.id}><td>{l.line_no}</td><td>{l.description||l.fabric_type_id||"—"}</td>
      <td className="num">{inr2(l.qty_kg)}</td><td className="num">{inr2(l.rate)}</td>
      <td className="num">{inr2(Number(l.qty_kg)*Number(l.rate))}</td></tr>)}</tbody></table>);
}

// ---- login ----------------------------------------------------------------
function Login({onLogin}){
  const [u,setU]=useState(""); const [p,setP]=useState(""); const [err,setErr]=useState(""); const [busy,setBusy]=useState(false);
  const submit=async e=>{ e.preventDefault(); setErr(""); setBusy(true);
    try{ const r=await api("/api/auth/login",{form:{username:u,password:p}}); setToken(r.access_token); onLogin(); }
    catch(ex){ setErr("Invalid username or password"); } finally{ setBusy(false); } };
  return (<div className="login"><form className="box" onSubmit={submit}>
    <h2>Textile ERP</h2><p>Integrated Knitting &amp; Dyeing ERP</p>
    <div className="field"><label>Username</label><input value={u} autoComplete="username" autoFocus onChange={e=>setU(e.target.value)} /></div>
    <div className="field"><label>Password</label><input type="password" autoComplete="current-password" value={p} onChange={e=>setP(e.target.value)} /></div>
    <button className="btn" style={{width:"100%"}} disabled={busy||!u||!p}>{busy?"Signing in…":"Sign in"}</button>
    {err && <div className="err">{err}</div>}
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

const FLOW_NAV={"Yarn Issue":"knitting","Knitting":"knitting","Greige":"stock","Dyeing":"dyeing","Finishing":"quality","Finished Goods":"stock"};
function OwnerDash({onNav, asOf, refreshKey}){
  const nav=onNav||(()=>{});
  const q=p=>p+(p.includes("?")?"&":"?")+(asOf?("as_of="+asOf):"")+"&_r="+(refreshKey||0);
  const [d,err]=useData(q("/api/dashboard/owner"));
  const trend=useList(q("/api/dashboard/production-trend"));
  const chem=useData(q("/api/dashboard/chemical"))[0];
  const util=useList(q("/api/hardware/knitting-summary"));
  const batches=useList(q("/api/dyeing/batches"));
  const acts=useList(q("/api/vouchers"));
  if(err) return <Panel><div className="muted">Couldn't load dashboard data — {String(err)}</div>
    <button className="btn sec" style={{marginTop:10}} onClick={()=>location.reload()}>Retry</button></Panel>;
  if(!d) return <Panel><div className="muted">Loading dashboard…</div></Panel>;
  const dl=d.deltas||{}; const k=d.kpis||{};
  const mix=((chem&&chem.top_consumption)||[]).map((m,i)=>({name:m.material, value:Number(m.cost), c:DONUT[i%DONUT.length]}));
  const mixTotal=mix.reduce((s,m)=>s+m.value,0);
  const alerts=d.alerts||[];
  const dayShort=s=>s?s.slice(5):"";
  const sevCls={bad:"bad",warn:"warn",info:"info"};
  return (<div>
    {/* ---- operational KPI grid (the 8 headline KPIs) ---- */}
    <div className="kpi-grid">
      <Stat tone="" icon="🧶" label="Today's Production" value={inr2(k.production_today_kg)+" kg"} onClick={()=>nav("knitting")} />
      <Stat tone="teal" icon="📅" label="Monthly Production" value={inr2(k.production_month_kg)+" kg"} onClick={()=>nav("knitting")} />
      <Stat tone="green" icon="🛒" label="Active Orders" value={k.active_orders} onClick={()=>nav("salesorders")} />
      <Stat tone="amber" icon="⏳" label="Pending Orders" value={k.pending_orders} onClick={()=>nav("salesorders")} />
      <Stat tone="violet" icon="📦" label="Finished Goods" value={inr2(k.finished_goods_kg)+" kg"} sub={inr(k.finished_goods_value)} onClick={()=>nav("stock")} />
      <Stat tone="" icon="🧵" label="Greige Stock" value={inr2(k.greige_kg)+" kg"} sub={inr(k.greige_value)} onClick={()=>nav("stock")} />
      <Stat tone="amber" icon="📥" label="Receivables" value={inr(k.receivables)} sub={Number(d.overdue_receivables)>0?("overdue "+inr(d.overdue_receivables)):""} onClick={()=>nav("customers")} />
      <Stat tone="rose" icon="📤" label="Payables" value={inr(k.payables)} sub={Number(d.overdue_payables)>0?("overdue "+inr(d.overdue_payables)):""} onClick={()=>nav("suppliers")} />
    </div>

    {/* ---- manufacturing process flow ---- */}
    <Panel title="Manufacturing flow">
      <div className="flow">
        {(d.manufacturing_flow||[]).map((s,i,arr)=><React.Fragment key={s.stage}>
          <div className="flow-stage" onClick={()=>nav(FLOW_NAV[s.stage]||"owner")} role="button" tabIndex={0}>
            <div className="flow-stage-name">{s.stage}</div>
            <div className="flow-stage-val">{isNaN(Number(s.value))?s.value:inr2(s.value)}</div>
            <div className="flow-stage-metric">{s.metric}</div>
          </div>
          {i<arr.length-1 && <div className="flow-arrow">→</div>}
        </React.Fragment>)}
      </div>
    </Panel>

    {/* ---- alerts & approvals ---- */}
    {alerts.length>0 && <Panel title="Alerts & approvals">
      <div className="alert-list">{alerts.map((a,i)=>
        <div className={"alert-row "+(sevCls[a.severity]||"")} key={i} onClick={()=>nav(a.page)} role="button" tabIndex={0}>
          <span className="alert-dot"></span>
          <span className="alert-kind">{a.kind}</span>
          <span style={{flex:1}}>{a.text}</span>
          <span className="alert-go">View →</span>
        </div>)}</div>
    </Panel>}

    {/* ---- financial overview ---- */}
    <div className="section-head">Financial overview</div>
    <div className="stats">
      <Stat tone="" icon="₹" label="Revenue (YTD)" value={inr(d.revenue)} delta={dl.revenue} />
      <Stat tone="green" icon="📈" label="Gross Profit" value={inr(d.gross_profit)} delta={dl.gross_profit} />
      <Stat tone="violet" icon="％" label="Net Profit" value={inr(d.net_profit)} delta={dl.net_profit} />
      <Stat tone="teal" icon="🏦" label="Cash & Bank" value={inr(d.cash_and_bank)} />
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

function FactoryDash({onNav, asOf, refreshKey}){
  const nav=onNav||(()=>{});
  const q=p=>p+(p.includes("?")?"&":"?")+(asOf?("as_of="+asOf):"")+"&_r="+(refreshKey||0);
  const [d,err]=useData(q("/api/dashboard/factory"));
  const trend=useList(q("/api/dashboard/production-trend"));
  const util=useList(q("/api/hardware/knitting-summary"));
  const batches=useList(q("/api/dyeing/batches"));
  if(err) return <Panel><div className="muted">Couldn't load — {String(err)}</div></Panel>; if(!d) return <Panel><div className="muted">Loading…</div></Panel>;
  const dayShort=s=>s?s.slice(5):"";
  return (<div>
    <div className="kpi-grid">
      <Stat tone="" icon="🧶" label="Knitting output (kg)" value={inr2(d.knitting_output_kg)} onClick={()=>nav("knitting")} />
      <Stat tone="violet" icon="🎨" label="Dyeing output (kg)" value={inr2(d.dyeing_output_kg)} onClick={()=>nav("dyeing")} />
      <Stat tone="amber" icon="⚙️" label="Active batches" value={d.active_batches} onClick={()=>nav("batching")} />
      <Stat tone="teal" icon="🧪" label="Chemical consumption" value={inr(d.chemical_consumption_cost)} onClick={()=>nav("chemical")} />
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

function AccountsDash({onNav, asOf, refreshKey}){
  const nav=onNav||(()=>{});
  const q=p=>p+(p.includes("?")?"&":"?")+(asOf?("as_of="+asOf):"")+"&_r="+(refreshKey||0);
  const [d,err]=useData(q("/api/dashboard/accounts"));
  const acts=useList(q("/api/vouchers"));
  if(err) return <Panel><div className="muted">Couldn't load — {String(err)}</div></Panel>; if(!d) return <Panel><div className="muted">Loading…</div></Panel>;
  return (<div>
    <div className="kpi-grid">
      <Stat tone="green" icon="📥" label="Today's receipts" value={inr(d.today_receipts)} onClick={()=>nav("vouchers")} />
      <Stat tone="rose" icon="📤" label="Today's payments" value={inr(d.today_payments)} onClick={()=>nav("vouchers")} />
      <Stat tone="amber" icon="👤" label="Debtors outstanding" value={inr(d.debtors_total)} onClick={()=>nav("customers")} />
      <Stat tone="violet" icon="🏢" label="Creditors outstanding" value={inr(d.creditors_total)} onClick={()=>nav("suppliers")} />
      <Stat tone="teal" icon="🧾" label="GST net payable" value={inr(d.gst_net_payable)} onClick={()=>nav("acctops")} />
      <Stat tone="" icon={d.trial_balance_balanced?"✅":"⚠️"} label="Trial balance" onClick={()=>nav("reports")}
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

function ChemicalDash({onNav, asOf, refreshKey}){
  const nav=onNav||(()=>{});
  const q=p=>p+(p.includes("?")?"&":"?")+(asOf?("as_of="+asOf):"")+"&_r="+(refreshKey||0);
  const [d,err]=useData(q("/api/dashboard/chemical"));
  if(err) return <Panel><div className="muted">Couldn't load — {String(err)}</div></Panel>; if(!d) return <Panel><div className="muted">Loading…</div></Panel>;
  const bars=(d.top_consumption||[]).map(r=>({material:r.material, cost:Number(r.cost)}));
  return (<div>
    <div className="kpi-grid">
      <Stat tone="violet" icon="🎨" label="Dye cost" value={inr(d.dye_cost)} onClick={()=>nav("dyeing")} />
      <Stat tone="teal" icon="🧪" label="Chemical cost" value={inr(d.chemical_cost)} onClick={()=>nav("stock")} />
      <Stat tone="" icon="Σ" label="Total consumption" value={inr(d.total_consumption_cost)} onClick={()=>nav("costing")} />
      <Stat tone={d.dispensing_errors?"rose":"green"} icon={d.dispensing_errors?"⚠️":"✅"} label="Dispensing errors" value={d.dispensing_errors} onClick={()=>nav("devices")} />
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

function GeneralLedger({code, onPick}){
  const accounts=useList("/api/accounts");
  const [sel,setSel]=useState(code||"");
  const [rng,setRng]=useState({from:"",to:today()});
  const [data,setData]=useState(null); const [err,setErr]=useState(""); const [busy,setBusy]=useState(false);
  useEffect(()=>{ if(code) setSel(code); },[code]);
  const load=useCallback(async(c)=>{ const acc=c||sel; if(!acc){ setData(null); return; }
    setErr(""); setBusy(true);
    try{ let q=`/api/reports/ledger/${acc}`; const p=[];
      if(rng.from) p.push("date_from="+rng.from); if(rng.to) p.push("date_to="+rng.to);
      if(p.length) q+="?"+p.join("&");
      setData(await api(q)); }
    catch(ex){ setErr(ex.message); setData(null); } finally{ setBusy(false); } },[sel,rng.from,rng.to]);
  useEffect(()=>{ if(code){ setSel(code); load(code); } },[code]);
  return (<Panel title="General ledger (by account)">
    <div className="row mb" style={{alignItems:"end",gap:10}}>
      <div className="field" style={{minWidth:280}}><label>Account</label>
        <select value={sel} onChange={e=>{setSel(e.target.value); load(e.target.value);}}>
          <option value="">Select an account…</option>
          {accounts.map(a=><option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></div>
      <div className="field"><label>From</label><input type="date" value={rng.from} onChange={e=>setRng({...rng,from:e.target.value})}/></div>
      <div className="field"><label>To</label><input type="date" value={rng.to} onChange={e=>setRng({...rng,to:e.target.value})}/></div>
      <button className="btn" disabled={busy||!sel} onClick={()=>load()}>Refresh</button>
    </div>
    {err && <div className="err">{err}</div>}
    {data && <>
      <div className="mb" style={{fontWeight:700}}>{data.account_code} · {data.account_name}</div>
      <table><thead><tr><th>Date</th><th>Voucher</th><th>Narration</th>
        <th className="num">Debit</th><th className="num">Credit</th><th className="num">Balance</th></tr></thead>
      <tbody>
        {data.entries.length===0 && <tr><td colSpan="6" className="muted">No postings in this period.</td></tr>}
        {data.entries.map((e,i)=><tr key={i}><td>{e.date}</td><td>{e.voucher_number}</td><td>{e.narration||"—"}</td>
          <td className="num">{Number(e.debit)?inr2(e.debit):""}</td>
          <td className="num">{Number(e.credit)?inr2(e.credit):""}</td>
          <td className="num">{inr2(e.running_balance)}</td></tr>)}
      </tbody>
      <tfoot><tr><th colSpan="5">Closing balance</th><th className="num">{inr2(data.closing_balance)}</th></tr></tfoot></table>
      <div className="muted" style={{fontSize:12,marginTop:6}}>Balance is debit-positive (Dr +, Cr −).</div>
    </>}
  </Panel>);
}

function TrialBalance({onDrill}){
  const [rng,setRng]=useState({from:"",to:today()});
  const [data,setData]=useState(null); const [err,setErr]=useState(""); const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{ setErr(""); setBusy(true);
    try{ let q="/api/reports/trial-balance"; const p=[];
      if(rng.from) p.push("date_from="+rng.from); if(rng.to) p.push("date_to="+rng.to);
      if(p.length) q+="?"+p.join("&");
      setData(await api(q)); }
    catch(ex){ setErr(ex.message); } finally{ setBusy(false); } },[rng.from,rng.to]);
  useEffect(()=>{load()},[]);
  const n=v=>Number(v)?inr2(v):"";
  const t=data&&data.totals;
  return (<Panel title="Trial balance">
    <div className="row mb" style={{alignItems:"end",gap:10}}>
      <div className="field"><label>From</label><input type="date" value={rng.from} onChange={e=>setRng({...rng,from:e.target.value})}/></div>
      <div className="field"><label>To</label><input type="date" value={rng.to} onChange={e=>setRng({...rng,to:e.target.value})}/></div>
      <button className="btn" disabled={busy} onClick={load}>Refresh</button>
      {data && <span className={"badge "+(data.is_balanced?"ok":"bad")} style={{alignSelf:"center"}}>
        {data.is_balanced?"Balanced":"Out of balance"}</span>}
    </div>
    {err && <div className="err">{err}</div>}
    {data && <table><thead><tr><th>Code</th><th>Account</th>
      <th className="num">Opening Dr</th><th className="num">Opening Cr</th>
      <th className="num">Period Dr</th><th className="num">Period Cr</th>
      <th className="num">Closing Dr</th><th className="num">Closing Cr</th></tr></thead>
    <tbody>{data.rows.map(r=><tr key={r.account_code}>
      <td><a href="#gl" onClick={ev=>{ev.preventDefault(); onDrill&&onDrill(r.account_code);}}
        style={{color:"#2563eb",cursor:"pointer",textDecoration:"underline"}}>{r.account_code}</a></td>
      <td>{r.account_name}</td>
      <td className="num">{n(r.opening_debit)}</td><td className="num">{n(r.opening_credit)}</td>
      <td className="num">{n(r.period_debit)}</td><td className="num">{n(r.period_credit)}</td>
      <td className="num">{n(r.closing_debit)}</td><td className="num">{n(r.closing_credit)}</td></tr>)}</tbody>
    {t && <tfoot><tr><th colSpan="2">Totals</th>
      <th className="num">{inr2(t.opening_debit)}</th><th className="num">{inr2(t.opening_credit)}</th>
      <th className="num">{inr2(t.period_debit)}</th><th className="num">{inr2(t.period_credit)}</th>
      <th className="num">{inr2(t.closing_debit)}</th><th className="num">{inr2(t.closing_credit)}</th></tr></tfoot>}
    </table>}
    <div className="muted" style={{fontSize:12,marginTop:6}}>Click an account code to open its general ledger.</div>
  </Panel>);
}

function Reports(){
  const [fmt,setFmt]=useState("xlsx"); const [busy,setBusy]=useState("");
  const [glCode,setGlCode]=useState("");
  const drill=c=>{ setGlCode(c); setTimeout(()=>{ const el=document.getElementById("gl"); if(el) el.scrollIntoView({behavior:"smooth"}); },50); };
  const dl=async(rep)=>{ setBusy(rep);
    try{ const blob=await api(`/api/reports/export/${rep}?format=${fmt}`,{blob:true});
      const url=URL.createObjectURL(blob); const a=document.createElement("a");
      a.href=url; a.download=`${rep}.${fmt}`; a.click(); URL.revokeObjectURL(url); }
    catch(e){ alert(e.message); } finally{ setBusy(""); } };
  return (<div>
    <TrialBalance onDrill={drill}/>
    <div id="gl"><GeneralLedger code={glCode}/></div>
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
      <DataTable rows={insp} pageSize={10} empty="No inspections yet." initialSort={{key:"inspection_date",dir:"desc"}}
        searchKeys={["number","inspection_date","result","colour_fastness"]}
        columns={[
          {key:"number",label:"No."},{key:"inspection_date",label:"Date"},
          {key:"delta_e",label:"Delta E",render:r=>r.delta_e||"-"},
          {key:"colour_fastness",label:"Fastness",render:r=>r.colour_fastness||"-"},
          {key:"result",label:"Result",render:r=><span className={"badge "+(RESULT_CLS[r.result]||"")}>{r.result}</span>},
        ]}/>
    </Panel>
    <Panel title="Customer complaints">
      <DataTable rows={comp} pageSize={10} empty="No complaints." initialSort={{key:"complaint_date",dir:"desc"}}
        searchKeys={["number","complaint_date","severity","status","description"]}
        columns={[
          {key:"number",label:"No."},{key:"complaint_date",label:"Date"},{key:"severity",label:"Severity"},
          {key:"status",label:"Status",render:r=><span className={"badge "+(r.status==="OPEN"?"warn":"ok")}>{r.status}</span>},
          {key:"description",label:"Description"},
        ]}/>
    </Panel>
  </div>);
}

// ---- masters: parties -----------------------------------------------------
function PartyLedger({kind, party, onClose}){  // party: {id,name}
  const isCust = kind==="customers";
  const [rng,setRng]=useState({from:"",to:today()});
  const [data,setData]=useState(null); const [err,setErr]=useState(""); const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{ setErr(""); setBusy(true);
    try{ const idp=isCust?"customer_id":"supplier_id"; const ep=isCust?"customer-statement":"supplier-statement";
      let q=`/api/reports/${ep}?${idp}=${party.id}`;
      if(rng.from) q+=`&date_from=${rng.from}`; if(rng.to) q+=`&date_to=${rng.to}`;
      setData(await api(q)); }
    catch(ex){ setErr(ex.message); } finally{ setBusy(false); } },[party.id,rng.from,rng.to,isCust]);
  useEffect(()=>{load()},[party.id]); // reload when party changes; date changes apply on Refresh
  const nature = isCust ? "Dr (owes us)" : "Cr (we owe)";
  return (<Panel title={`Ledger — ${party.name}`}>
    <div className="row mb" style={{alignItems:"end",gap:10}}>
      <div className="field"><label>From</label><input type="date" value={rng.from} onChange={e=>setRng({...rng,from:e.target.value})}/></div>
      <div className="field"><label>To</label><input type="date" value={rng.to} onChange={e=>setRng({...rng,to:e.target.value})}/></div>
      <button className="btn" disabled={busy} onClick={load}>Refresh</button>
      <span style={{flex:1}}/>
      <button className="btn sec" onClick={onClose}>Close</button>
    </div>
    {err && <div className="err">{err}</div>}
    {data && <>
      <table><thead><tr><th>Date</th><th>Voucher</th><th>Narration</th>
        <th className="num">Debit</th><th className="num">Credit</th><th className="num">Balance</th></tr></thead>
      <tbody>
        <tr><td colSpan="5"><b>Opening balance</b></td><td className="num"><b>{inr2(data.opening)}</b></td></tr>
        {data.rows.length===0 && <tr><td colSpan="6" className="muted">No transactions in this period.</td></tr>}
        {data.rows.map((r,i)=><tr key={i}><td>{r.date}</td><td>{r.voucher}</td><td>{r.narration||"—"}</td>
          <td className="num">{Number(r.debit)?inr2(r.debit):""}</td>
          <td className="num">{Number(r.credit)?inr2(r.credit):""}</td>
          <td className="num">{inr2(r.balance)}</td></tr>)}
      </tbody>
      <tfoot><tr><th colSpan="5">Closing balance</th><th className="num">{inr2(data.closing)}</th></tr></tfoot></table>
      <div className="muted" style={{fontSize:12,marginTop:6}}>Positive balance = outstanding · {nature}.</div>
    </>}
  </Panel>);
}

function PartyPage({kind}){  // kind: "customers" | "suppliers"
  const isCust = kind==="customers";
  const [rows,setRows]=useState(null); const [err,setErr]=useState("");
  const [ledger,setLedger]=useState(null);   // {id,name} of party whose ledger is open
  const blank={code:"",name:"",gstin:"",state_code:"",credit_limit:"0"};
  const [f,setF]=useState(blank);
  const load=useCallback(()=>api(`/api/masters/${kind}`).then(setRows).catch(e=>setErr(e.message)),[kind]);
  useEffect(()=>{load(); setLedger(null);},[load]);
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
    {ledger && <PartyLedger kind={kind} party={ledger} onClose={()=>setLedger(null)}/>}
    <Panel title={title.charAt(0).toUpperCase()+title.slice(1)+"s"}>
      <DataTable rows={rows||[]} pageSize={12} empty={rows?"None yet.":"Loading…"}
        searchKeys={["code","name","gstin","state_code"]}
        columns={[
          {key:"code",label:"Code"},
          {key:"name",label:"Name"},
          {key:"gstin",label:"GSTIN",render:r=>r.gstin||"-"},
          {key:"state_code",label:"State",render:r=>r.state_code||"-"},
          ...(isCust?[{key:"credit_limit",label:"Credit limit",num:true,render:r=>Number(r.credit_limit)>0?inr2(r.credit_limit):"—"}]:[]),
          {key:"act",label:"",sortable:false,num:true,render:r=><button className="btn sec" onClick={()=>setLedger({id:r.id,name:r.name})}>Ledger</button>},
        ]}/>
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
      <LineItems rows={lines} setRows={setLines}
        newRow={()=>({chemical_id:"",warehouse_id:"",qty:"",unit_cost:"",tax_code_id:""})}
        amount={l=>Number(l.qty||0)*Number(l.unit_cost||0)}
        amountLabel="Goods value (before GST)"
        note="Input GST and payable are computed by the backend on posting."
        columns={[
          {key:"chemical_id",label:"Chemical",type:"opts",items:chemicals},
          {key:"warehouse_id",label:"Warehouse",type:"opts",items:warehouses},
          {key:"qty",label:"Qty",num:true,width:90},
          {key:"unit_cost",label:"Unit cost",num:true,width:100},
          {key:"tax_code_id",label:"Tax",type:"opts",items:taxes},
        ]}/>
      <div className="row" style={{marginTop:14}}>
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
function EInvoiceBox({invoiceId, total}){
  const [cfg,setCfg]=useState(null); const [ei,setEi]=useState(null);
  const [err,setErr]=useState(""); const [busy,setBusy]=useState(false);
  useEffect(()=>{ api("/api/einvoice/config").then(setCfg).catch(()=>{});
    api("/api/einvoice/"+invoiceId).then(setEi).catch(()=>setEi(null)); },[invoiceId]);
  const gen=async()=>{ setErr(""); setBusy(true);
    try{ setEi(await api("/api/einvoice/"+invoiceId+"/generate",{method:"POST"})); }
    catch(ex){ setErr(ex.message); } finally{ setBusy(false); } };
  const cancel=async()=>{ setErr(""); setBusy(true);
    try{ setEi(await api("/api/einvoice/"+invoiceId+"/cancel",{method:"POST",body:{reason_code:"2",remarks:"Cancelled from UI"}})); }
    catch(ex){ setErr(ex.message); } finally{ setBusy(false); } };
  const st=ei&&ei.status;
  return (<div className="panel" style={{marginTop:14}}>
    <h3>E-invoice (IRN)</h3>
    <div className="row mb" style={{alignItems:"center",gap:10}}>
      <span className="muted" style={{fontSize:12.5}}>Mode:</span>
      <span className={"badge "+(cfg&&cfg.live?"info":"")}>{cfg? (cfg.live?"LIVE GSP":"SIMULATOR"):"…"}</span>
      {cfg&&cfg.live&&!cfg.configured && <span className="badge bad">credentials missing</span>}
      <span style={{flex:1}}/>
      {st!=="GENERATED" && <button className="btn" disabled={busy} onClick={gen}>Generate IRN</button>}
      {st==="GENERATED" && <button className="btn sec" disabled={busy} onClick={cancel}>Cancel IRN</button>}
    </div>
    {ei&&ei.irn && <div className="act" style={{flexDirection:"column",alignItems:"flex-start",gap:4}}>
      <div><b>Status:</b> <span className={"badge "+(st==="GENERATED"?"ok":st==="CANCELLED"?"bad":"warn")}>{st}</span></div>
      <div style={{fontFamily:"monospace",fontSize:12,wordBreak:"break-all"}}><b>IRN:</b> {ei.irn}</div>
      {ei.ack_no && <div style={{fontSize:12.5}}><b>Ack:</b> {ei.ack_no} · {ei.ack_date}</div>}
      {ei.signed_qr && <div style={{fontSize:11,color:"#64748b",wordBreak:"break-all"}}><b>Signed QR:</b> {ei.signed_qr.slice(0,80)}…</div>}
    </div>}
    {!ei && <div className="muted" style={{fontSize:12.5}}>No IRN yet for this invoice.</div>}
    {err && <div className="err">{err}</div>}
  </div>);
}

function EWayBillBox({invoiceId}){
  const [cfg,setCfg]=useState(null); const [eb,setEb]=useState(null);
  const [t,setT]=useState({trans_mode:"1",vehicle_no:"",distance_km:"",transporter_id:""});
  const [err,setErr]=useState(""); const [busy,setBusy]=useState(false);
  useEffect(()=>{ api("/api/ewaybill/config").then(setCfg).catch(()=>{});
    api("/api/ewaybill/"+invoiceId).then(setEb).catch(()=>setEb(null)); },[invoiceId]);
  const gen=async()=>{ setErr(""); setBusy(true);
    try{ setEb(await api("/api/ewaybill/"+invoiceId+"/generate",{method:"POST",body:{
        trans_mode:t.trans_mode, vehicle_no:t.vehicle_no||null,
        distance_km:Number(t.distance_km||0), transporter_id:t.transporter_id||null}})); }
    catch(ex){ setErr(ex.message); } finally{ setBusy(false); } };
  const cancel=async()=>{ setErr(""); setBusy(true);
    try{ setEb(await api("/api/ewaybill/"+invoiceId+"/cancel",{method:"POST",body:{reason_code:"2",remarks:"Cancelled from UI"}})); }
    catch(ex){ setErr(ex.message); } finally{ setBusy(false); } };
  const st=eb&&eb.status;
  return (<div className="panel" style={{marginTop:14}}>
    <h3>E-way bill</h3>
    <div className="row mb" style={{alignItems:"center",gap:10}}>
      <span className="muted" style={{fontSize:12.5}}>Mode:</span>
      <span className={"badge "+(cfg&&cfg.live?"info":"")}>{cfg? (cfg.live?"LIVE GSP":"SIMULATOR"):"…"}</span>
    </div>
    {st!=="GENERATED" && <div className="row mb">
      <div className="field"><label>Transport mode</label>
        <select value={t.trans_mode} onChange={e=>setT({...t,trans_mode:e.target.value})}>
          <option value="1">Road</option><option value="2">Rail</option>
          <option value="3">Air</option><option value="4">Ship</option></select></div>
      <div className="field"><label>Vehicle no.</label><input value={t.vehicle_no} onChange={e=>setT({...t,vehicle_no:e.target.value})} placeholder="PB10AB1234"/></div>
      <div className="field"><label>Distance (km)</label><input value={t.distance_km} onChange={e=>setT({...t,distance_km:e.target.value})} placeholder="120"/></div>
      <div className="field"><label>Transporter ID</label><input value={t.transporter_id} onChange={e=>setT({...t,transporter_id:e.target.value})} placeholder="optional GSTIN"/></div>
      <div className="field" style={{alignSelf:"end"}}><button className="btn" disabled={busy} onClick={gen}>Generate EWB</button></div>
    </div>}
    {eb&&eb.ewb_no && <div className="act" style={{flexDirection:"column",alignItems:"flex-start",gap:4}}>
      <div><b>Status:</b> <span className={"badge "+(st==="GENERATED"?"ok":st==="CANCELLED"?"bad":"warn")}>{st}</span>
        {st==="GENERATED" && <button className="btn sec" style={{marginLeft:10}} disabled={busy} onClick={cancel}>Cancel EWB</button>}</div>
      <div style={{fontFamily:"monospace",fontSize:13}}><b>EWB No:</b> {eb.ewb_no}</div>
      {eb.valid_until && <div style={{fontSize:12.5}}><b>Valid until:</b> {eb.valid_until}</div>}
      {eb.irn && <div style={{fontSize:11,color:"#64748b"}}>linked to IRN {eb.irn.slice(0,16)}…</div>}
    </div>}
    {err && <div className="err">{err}</div>}
  </div>);
}

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
      <LineItems rows={lines} setRows={setLines}
        newRow={()=>({description:"",qty:"",rate:"",tax_code_id:""})}
        amount={l=>Number(l.qty||0)*Number(l.rate||0)}
        note="GST and invoice total are computed by the backend on posting."
        columns={[
          {key:"description",label:"Description",width:260,placeholder:"Dyed fabric"},
          {key:"qty",label:"Qty",num:true,width:90},
          {key:"rate",label:"Rate",num:true,width:90},
          {key:"tax_code_id",label:"Tax",type:"opts",items:taxes},
        ]}/>
      <div className="row" style={{marginTop:14}}>
        <button className="btn">Post invoice</button>
      </div>
      {err && <div className="err">{err}</div>}
    </form>
    <Result r={res}/>
    {res && res.invoice_id && <EInvoiceBox invoiceId={res.invoice_id} total={res.total_value}/>}
    {res && res.invoice_id && <EWayBillBox invoiceId={res.invoice_id}/>}
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
  const buildBody=()=>({...h, lines:lines.filter(l=>l.account_code && (num(l.debit)||num(l.credit)))
      .map(l=>({account_code:l.account_code, debit:l.debit||"0", credit:l.credit||"0", narration:l.narration||null}))});
  const submitApproval=async()=>{ setErr(""); setRes(null);
    try{ const r=await api("/api/vouchers/submit-approval",{method:"POST",body:buildBody()});
      setRes(r.approval_required ? {status:"HELD", approval:"Routed for approval ("+r.approvals_required+" needed)"}
                                 : {voucher:r.voucher_number, status:"POSTED", note:"Below threshold — posted directly"});
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
        <LineItems rows={lines} setRows={setLines} minRows={2}
          newRow={()=>({account_code:"",debit:"",credit:"",narration:""})}
          columns={[
            {key:"account_code",label:"Account",type:"opts",items:acctOpts},
            {key:"debit",label:"Debit",num:true,width:100},
            {key:"credit",label:"Credit",num:true,width:100},
            {key:"narration",label:"Narration",width:150},
          ]}
          footer={()=><tr><th>Totals</th><th className="num">{inr2(totDr)}</th><th className="num">{inr2(totCr)}</th>
            <th colSpan="2"><span className={"badge "+(balanced?"ok":"bad")}>{balanced?"Balanced":"Dr ≠ Cr"}</span></th></tr>}/>
        <div className="row" style={{marginTop:12}}>
          <button type="button" className="btn sec" disabled={!balanced} onClick={submitApproval}>Submit for approval</button>
          <button className="btn">Post voucher</button>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
    </Panel>
    <Panel title="Recent vouchers">
      <DataTable rows={recent} pageSize={10} empty="None yet." initialSort={{key:"voucher_date",dir:"desc"}}
        searchKeys={["number","status","narration"]}
        columns={[
          {key:"number",label:"Number"},
          {key:"voucher_date",label:"Date"},
          {key:"status",label:"Status",render:v=><span className={"badge "+(v.status==="POSTED"?"ok":v.status==="REVERSED"?"warn":"")}>{v.status}</span>},
          {key:"total_debit",label:"Debit",num:true,render:v=>inr2(v.total_debit)},
          {key:"narration",label:"Narration",render:v=>v.narration||"-"},
        ]}/>
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
  const submitApproval=async id=>{ setErr(""); setRes(null);
    try{ const r=await api(`/api/procurement/purchase-orders/${id}/submit-approval`,{method:"POST"});
      setRes(r.approval_required ? {status:r.status, approval:"Routed for approval ("+r.approvals_required+" needed)"}
                                 : {status:r.status, note:"Below threshold — confirmed directly"}); load(); }
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
      <DataTable rows={pos} pageSize={10} initialSort={{key:"order_date",dir:"desc"}}
        empty="None yet. GRNs raised against a confirmed PO roll it to PARTIAL / COMPLETED."
        searchKeys={["number","order_date","status"]}
        expand={p=><PODetail id={p.id} names={Object.fromEntries((chemicals||[]).map(c=>[c.id, c.name||c.label||c.code]))}/>}
        columns={[
          {key:"number",label:"Number"},
          {key:"order_date",label:"Date"},
          {key:"lines",label:"Lines",num:true},
          {key:"status",label:"Status",render:p=>stBadge(p.status)},
          {key:"act",label:"",sortable:false,num:true,render:p=><>
            {p.status==="DRAFT" && <button className="btn sec" onClick={()=>submitApproval(p.id)}>Submit for approval</button>}{" "}
            {p.status==="DRAFT" && <button className="btn" onClick={()=>confirm(p.id)}>Confirm</button>}</>},
        ]}/>
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
      <DataTable rows={sos} pageSize={10} empty="None yet." initialSort={{key:"order_date",dir:"desc"}}
        searchKeys={["number","order_date","status"]} expand={s=><SODetail id={s.id}/>}
        columns={[
          {key:"number",label:"Number"},
          {key:"order_date",label:"Date"},
          {key:"ordered_kg",label:"Ordered kg",num:true,render:s=>s.ordered_kg?inr2(s.ordered_kg):"—"},
          {key:"delivered_kg",label:"Delivered kg",num:true,render:s=>s.delivered_kg?inr2(s.delivered_kg):"—"},
          {key:"status",label:"Status",render:s=>stBadge(s.status)},
          {key:"act",label:"",sortable:false,num:true,render:s=>s.status==="DRAFT"?<button className="btn" onClick={()=>confirm(s.id)}>Confirm</button>:null},
        ]}/>
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
      <DataTable rows={assets} pageSize={12} empty="None yet." searchKeys={["code","name","method","status"]}
        columns={[
          {key:"code",label:"Code"},{key:"name",label:"Name"},{key:"method",label:"Method"},
          {key:"cost",label:"Cost",num:true,render:a=>inr2(a.cost)},
          {key:"accumulated_depreciation",label:"Accum. dep.",num:true,render:a=>inr2(a.accumulated_depreciation)},
          {key:"book_value",label:"Book value",num:true,render:a=>inr2(a.book_value)},
          {key:"status",label:"Status",render:a=>stBadge(a.status)},
          {key:"act",label:"",sortable:false,num:true,render:a=>a.status==="ACTIVE"?<button className="btn sec" onClick={()=>dispose(a)}>Dispose</button>:null},
        ]}/>
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
      <DataTable rows={structs.map(s=>({...s, employee_disp:(s.employee_code?s.employee_code+" — ":"")+s.employee}))}
        pageSize={12} empty="None yet — add an employee and set a structure." searchKeys={["employee_disp"]}
        columns={[
          {key:"employee_disp",label:"Employee"},
          {key:"basic",label:"Basic",num:true,render:s=>inr2(s.basic)},
          {key:"gross",label:"Gross",num:true,render:s=>inr2(s.gross)},
          {key:"deductions",label:"Deductions",num:true,render:s=>inr2(s.deductions)},
          {key:"net_pay",label:"Net pay",num:true,render:s=>inr2(s.net_pay)},
        ]}/>
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
      <DataTable rows={runs} pageSize={12} empty="None yet." initialSort={{key:"period",dir:"desc"}}
        searchKeys={["period","status"]}
        columns={[
          {key:"period",label:"Period"},
          {key:"employees",label:"Employees",num:true},
          {key:"total_gross",label:"Gross",num:true,render:r=>inr2(r.total_gross)},
          {key:"total_net",label:"Net paid",num:true,render:r=>inr2(r.total_net)},
          {key:"status",label:"Status",render:r=>stBadge(r.status)},
        ]}/>
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
      <DataTable rows={reqs} pageSize={10} empty="Nothing awaiting approval." searchKeys={["title","doc_type"]}
        columns={[
          {key:"title",label:"Document"},
          {key:"amount",label:"Amount",num:true,render:r=>inr2(r.amount)},
          {key:"approvals",label:"Approvals",num:true,sortable:false,render:r=>`${r.approvals_received}/${r.approvals_required}`},
          {key:"act",label:"",sortable:false,num:true,render:r=><>
            <button className="btn" onClick={()=>decide(r.id,"approve")}>Approve</button>{" "}
            <button className="btn sec" onClick={()=>decide(r.id,"reject")}>Reject</button></>},
        ]}/>
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
      {transfers.length>0 && <div style={{marginTop:12}}><DataTable rows={transfers} pageSize={8}
        searchKeys={["number","date"]} initialSort={{key:"date",dir:"desc"}}
        columns={[
          {key:"number",label:"Number"},{key:"date",label:"Date"},
          {key:"qty",label:"Qty",num:true,render:t=>inr2(t.qty)},
          {key:"cost_value",label:"Value",num:true,render:t=>inr2(t.cost_value)},
        ]}/></div>}
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

function Notifications({onChange}){
  const [rows,setRows]=useState([]);
  const load=useCallback(()=>api("/api/notifications").then(setRows).catch(()=>{}),[]);
  useEffect(()=>{load()},[load]);
  const readAll=async()=>{ try{ await api("/api/notifications/mark-read",{method:"POST",body:{}}); load(); onChange&&onChange(); }catch(e){} };
  const kindCls={APPROVAL:"info",ALERT:"bad",INFO:""};
  return (<Panel title="Notifications">
    <div className="row mb"><button className="btn sec" onClick={readAll}>Mark all read</button></div>
    {rows.length===0?<div className="muted">Nothing here yet.</div>:
      rows.map(n=><div className="act" key={n.id} style={{opacity:n.is_read?.55:1}}>
        <span className="tt">{n.created_at?n.created_at.slice(5,10):""}</span>
        <span style={{flex:1}}><b>{n.title}</b>{n.message?" — "+n.message:""}</span>
        <span className={"badge "+(kindCls[n.kind]||"")}>{n.kind}</span></div>)}
  </Panel>);
}

function JobWork(){
  const customers=useList("/api/masters/customers"); const suppliers=useList("/api/masters/suppliers");
  const fabrics=useList("/api/masters/fabric-types"); const taxes=useList("/api/masters/tax-codes");
  const [rows,setRows]=useState([]); const [err,setErr]=useState(""); const [res,setRes]=useState(null);
  const [f,setF]=useState({direction:"INWARD",process:"DYEING",party_id:"",fabric_type_id:"",qty_kg:"1000",rate:"25",tax_code_id:"",description:"",number:genNo("JW")});
  const load=useCallback(()=>api("/api/jobwork").then(setRows).catch(()=>{}),[]);
  useEffect(()=>{load()},[load]);
  const parties = f.direction==="INWARD" ? customers : suppliers;
  const create=async e=>{ e.preventDefault(); setErr(""); setRes(null);
    try{ const r=await api("/api/jobwork",{method:"POST",body:{number:f.number,direction:f.direction,process:f.process,
        party_id:f.party_id, order_date:today(), qty_kg:f.qty_kg, rate:f.rate,
        fabric_type_id:f.fabric_type_id||null, tax_code_id:f.tax_code_id||null, description:f.description||null}});
      setRes(r); setF({...f,number:genNo("JW"),party_id:""}); load(); }
    catch(ex){ setErr(ex.message); } };
  const act=async(id,action)=>{ setErr("");
    try{ await api(`/api/jobwork/${id}/${action}`,{method:"POST"}); load(); }catch(ex){ setErr(ex.message); } };
  const stCls={OPEN:"",SENT:"warn",RECEIVED:"warn",BILLED:"ok",CANCELLED:"bad"};
  return (<div>
    <Panel title="Job work — inward (we process for others) / outward (we send ours out)">
      <form onSubmit={create}>
        <div className="row mb">
          <div className="field"><label>Direction</label>
            <select value={f.direction} onChange={e=>setF({...f,direction:e.target.value,party_id:""})}>
              <option value="INWARD">Inward — we dye/finish others' fabric (we earn)</option>
              <option value="OUTWARD">Outward — we send our fabric to another dyer (we pay)</option></select></div>
          <div className="field"><label>Process</label>
            <select value={f.process} onChange={e=>setF({...f,process:e.target.value})}>
              <option value="DYEING">Dyeing</option><option value="FINISHING">Finishing</option>
              <option value="KNITTING">Knitting</option><option value="OTHER">Other</option></select></div>
          <div className="field"><label>{f.direction==="INWARD"?"Customer":"Subcontractor (supplier)"}</label>
            <Opts items={parties} value={f.party_id} onChange={e=>setF({...f,party_id:e.target.value})}/></div>
          <div className="field"><label>JW number</label><input value={f.number} onChange={e=>setF({...f,number:e.target.value})}/></div>
        </div>
        <div className="row">
          <div className="field"><label>Fabric type</label><Opts items={fabrics} value={f.fabric_type_id} onChange={e=>setF({...f,fabric_type_id:e.target.value})} placeholder="—"/></div>
          <div className="field"><label>Qty (kg)</label><input value={f.qty_kg} onChange={e=>setF({...f,qty_kg:e.target.value})}/></div>
          <div className="field"><label>Rate / kg</label><input value={f.rate} onChange={e=>setF({...f,rate:e.target.value})}/></div>
          <div className="field"><label>Tax</label><Opts items={taxes} value={f.tax_code_id} onChange={e=>setF({...f,tax_code_id:e.target.value})} placeholder="—"/></div>
          <div className="field" style={{flex:1}}><label>Note</label><input value={f.description} onChange={e=>setF({...f,description:e.target.value})} placeholder="e.g. navy, 1000 kg"/></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Create</button></div>
        </div>
        {err && <div className="err">{err}</div>}
      </form>
      <Result r={res}/>
      <div className="muted" style={{fontSize:12,marginTop:6}}>Inward material belongs to the customer — it is never valued in your stock; you bill a processing charge (income). Outward keeps your fabric as your asset; on receipt you book the dyer's processing charge as a cost + payable.</div>
    </Panel>
    <Panel title="Job work orders">
      <DataTable rows={rows} pageSize={10} empty="None yet." initialSort={{key:"number",dir:"desc"}}
        searchKeys={["number","direction","process","party","status"]}
        columns={[
          {key:"number",label:"Number"},
          {key:"direction",label:"Dir",render:j=><span className={"badge "+(j.direction==="INWARD"?"ok":"info")}>{j.direction}</span>},
          {key:"process",label:"Process"},
          {key:"party",label:"Party"},
          {key:"qty_kg",label:"Qty",num:true,render:j=>inr2(j.qty_kg)},
          {key:"charge",label:"Charge",num:true,sortable:false,render:j=>Number(j.total_value)?inr2(j.total_value):inr2(Number(j.qty_kg)*Number(j.rate))},
          {key:"status",label:"Status",render:j=><span className={"badge "+(stCls[j.status]||"")}>{j.status}</span>},
          {key:"act",label:"",sortable:false,num:true,render:j=><>
            {j.direction==="OUTWARD" && j.status==="OPEN" && <button className="btn sec" onClick={()=>act(j.id,"dispatch")}>Dispatch</button>}{" "}
            {j.status!=="BILLED" && j.status!=="CANCELLED" && <button className="btn" onClick={()=>act(j.id,"bill")}>Bill</button>}</>},
        ]}/>
    </Panel>
  </div>);
}

function Stores(){
  const warehouses=useList("/api/masters/warehouses"); const suppliers=useList("/api/masters/suppliers");
  const [items,setItems]=useState([]); const [err,setErr]=useState(""); const [res,setRes]=useState(null);
  const [ni,setNi]=useState({code:"",name:"",category:"NEEDLE",uom:"PCS"});
  const [rc,setRc]=useState({store_item_id:"",qty:"",unit_cost:"",warehouse_id:"",supplier_id:""});
  const [is,setIs]=useState({store_item_id:"",qty:"",warehouse_id:"",machine:""});
  const load=useCallback(()=>api("/api/stores/items").then(setItems).catch(()=>{}),[]);
  useEffect(()=>{load()},[load]);
  const go=async fn=>{ setErr(""); setRes(null); try{ await fn(); load(); }catch(ex){ setErr(ex.message); } };
  const addItem=e=>{ e.preventDefault(); go(async()=>{ await api("/api/stores/items",{method:"POST",body:ni});
    setRes({created:ni.code}); setNi({code:"",name:"",category:ni.category,uom:"PCS"}); }); };
  const receive=e=>{ e.preventDefault(); go(async()=>{ const r=await api("/api/stores/receipts",{method:"POST",body:{
      store_item_id:rc.store_item_id, qty:rc.qty, unit_cost:rc.unit_cost,
      warehouse_id:rc.warehouse_id||null, supplier_id:rc.supplier_id||null}});
    setRes({received:r.value, voucher:r.voucher_number}); setRc({...rc,qty:"",unit_cost:""}); }); };
  const issue=e=>{ e.preventDefault(); go(async()=>{ const r=await api("/api/stores/issues",{method:"POST",body:{
      store_item_id:is.store_item_id, qty:is.qty, warehouse_id:is.warehouse_id||null, machine:is.machine||null}});
    setRes({issued_cost:r.cost, voucher:r.voucher_number}); setIs({...is,qty:""}); }); };
  const itemOpts=items.map(i=>({id:i.id,label:`${i.code} · ${i.name}`}));
  return (<div>
    <Panel title="Add store item (needles / spares / packing)">
      <form className="row" onSubmit={addItem}>
        <div className="field"><label>Code</label><input required value={ni.code} onChange={e=>setNi({...ni,code:e.target.value})} placeholder="NDL-9G" style={{width:120}}/></div>
        <div className="field" style={{flex:1}}><label>Name</label><input required value={ni.name} onChange={e=>setNi({...ni,name:e.target.value})} placeholder="9G Knitting Needle"/></div>
        <div className="field"><label>Category</label>
          <select value={ni.category} onChange={e=>setNi({...ni,category:e.target.value})}>
            <option value="NEEDLE">Needle</option><option value="SPARE">Spare</option>
            <option value="PACKING">Packing</option><option value="OTHER">Other</option></select></div>
        <div className="field"><label>UoM</label><input value={ni.uom} onChange={e=>setNi({...ni,uom:e.target.value})} style={{width:80}}/></div>
        <button className="btn">Add</button>
      </form>
    </Panel>
    <div className="row" style={{gap:16,alignItems:"flex-start"}}>
      <div style={{flex:1}}><Panel title="Receive into store">
        <form onSubmit={receive}>
          <div className="field mb"><label>Item</label><Opts items={itemOpts} value={rc.store_item_id} onChange={e=>setRc({...rc,store_item_id:e.target.value})}/></div>
          <div className="row mb">
            <div className="field"><label>Qty</label><input value={rc.qty} onChange={e=>setRc({...rc,qty:e.target.value})} placeholder="500"/></div>
            <div className="field"><label>Unit cost</label><input value={rc.unit_cost} onChange={e=>setRc({...rc,unit_cost:e.target.value})} placeholder="20"/></div>
          </div>
          <div className="row mb">
            <div className="field"><label>Warehouse</label><Opts items={warehouses} value={rc.warehouse_id} onChange={e=>setRc({...rc,warehouse_id:e.target.value})} placeholder="—"/></div>
            <div className="field"><label>Supplier (credit) / blank = cash</label><Opts items={suppliers} value={rc.supplier_id} onChange={e=>setRc({...rc,supplier_id:e.target.value})} placeholder="cash"/></div>
          </div>
          <button className="btn">Receive</button>
        </form>
      </Panel></div>
      <div style={{flex:1}}><Panel title="Issue to machine">
        <form onSubmit={issue}>
          <div className="field mb"><label>Item</label><Opts items={itemOpts} value={is.store_item_id} onChange={e=>setIs({...is,store_item_id:e.target.value})}/></div>
          <div className="row mb">
            <div className="field"><label>Qty</label><input value={is.qty} onChange={e=>setIs({...is,qty:e.target.value})} placeholder="150"/></div>
            <div className="field"><label>Machine</label><input value={is.machine} onChange={e=>setIs({...is,machine:e.target.value})} placeholder="KNIT-01"/></div>
          </div>
          <div className="field mb"><label>From warehouse</label><Opts items={warehouses} value={is.warehouse_id} onChange={e=>setIs({...is,warehouse_id:e.target.value})} placeholder="—"/></div>
          <button className="btn">Issue (expense at FIFO cost)</button>
        </form>
      </Panel></div>
    </div>
    {err && <div className="err">{err}</div>}
    <Result r={res}/>
    <Panel title="Store items">
      <DataTable rows={items} pageSize={10} empty="None yet."
        searchKeys={["code","name","category"]}
        columns={[
          {key:"code",label:"Code"},{key:"name",label:"Name"},
          {key:"category",label:"Category"},{key:"uom",label:"UoM"},
        ]}/>
      <div className="muted" style={{fontSize:12,marginTop:6}}>On-hand quantities show on the Stock on Hand page (item type STORE).</div>
    </Panel>
  </div>);
}

function StockOnHand(){
  const warehouses=useList("/api/masters/warehouses");
  const [wh,setWh]=useState(""); const [it,setIt]=useState("");
  const [rows,setRows]=useState([]); const [err,setErr]=useState(""); const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{ setErr(""); setBusy(true);
    try{ const p=[]; if(wh) p.push("warehouse_id="+wh); if(it) p.push("item_type="+it);
      setRows(await api("/api/inventory/stock-on-hand"+(p.length?"?"+p.join("&"):""))); }
    catch(ex){ setErr(ex.message); } finally{ setBusy(false); } },[wh,it]);
  useEffect(()=>{load()},[wh,it]);
  const totVal=rows.reduce((s,r)=>s+Number(r.value||0),0);
  const typeBadge={YARN:"",CHEMICAL:"info",FABRIC:"ok"};
  return (<div>
    <Panel title="Stock on hand (live)">
      <div className="row mb" style={{alignItems:"end",gap:10}}>
        <div className="field"><label>Warehouse</label>
          <select value={wh} onChange={e=>setWh(e.target.value)}>
            <option value="">All warehouses</option>
            {warehouses.map(w=><option key={w.id} value={w.id}>{w.code} · {w.name}</option>)}</select></div>
        <div className="field"><label>Item type</label>
          <select value={it} onChange={e=>setIt(e.target.value)}>
            <option value="">All</option><option value="YARN">Yarn</option>
            <option value="CHEMICAL">Chemical / Dye</option><option value="FABRIC">Fabric</option></select></div>
        <button className="btn sec" disabled={busy} onClick={load}>Refresh</button>
        <span style={{flex:1}}/>
        <div style={{textAlign:"right"}}><div className="muted" style={{fontSize:12}}>Total stock value</div>
          <div style={{fontWeight:700,fontSize:16}}>{inr2(totVal)}</div></div>
      </div>
      {err && <div className="err">{err}</div>}
      <DataTable rows={rows} pageSize={12} empty="No stock on hand for this filter."
        searchKeys={["item_type","item_code","item_name","warehouse"]}
        initialSort={{key:"value",dir:"desc"}}
        columns={[
          {key:"item_type",label:"Type",render:r=><span className={"badge "+(typeBadge[r.item_type]||"")}>{r.item_type}</span>},
          {key:"item_code",label:"Code",render:r=>r.item_code||"—"},
          {key:"item_name",label:"Item"},
          {key:"warehouse",label:"Warehouse"},
          {key:"qty",label:"Qty",num:true,render:r=>inr2(r.qty)},
          {key:"value",label:"Value",num:true,render:r=>inr2(r.value)},
        ]}/>
      <div className="muted" style={{fontSize:12,marginTop:6}}>Live from open FIFO cost layers · fabric grouped by type &amp; greige/finished.</div>
    </Panel>
  </div>);
}

const ACCT_TYPES=[["ASSET","Asset"],["LIABILITY","Liability"],["EQUITY","Equity"],["INCOME","Income"],["EXPENSE","Expense"]];
function ChartOfAccounts(){
  const [rows,setRows]=useState([]); const [err,setErr]=useState(""); const [ok,setOk]=useState("");
  const [f,setF]=useState({code:"",name:"",account_type:"EXPENSE",parent_code:"",is_group:false});
  const load=useCallback(()=>api("/api/accounts").then(setRows).catch(e=>setErr(e.message)),[]);
  useEffect(()=>{load()},[load]);
  const groups=rows.filter(a=>a.is_group && a.account_type===f.account_type);
  const byCode=Object.fromEntries(rows.map(a=>[a.id,a.code]));
  const add=async e=>{ e.preventDefault(); setErr(""); setOk("");
    try{ await api("/api/accounts",{method:"POST",body:{code:f.code,name:f.name,account_type:f.account_type,
        parent_code:f.parent_code||null, is_group:f.is_group}});
      setOk(`Account ${f.code} created`); setF({code:"",name:"",account_type:f.account_type,parent_code:"",is_group:false}); load(); }
    catch(ex){ setErr(ex.message); } };
  return (<div>
    <Panel title="Add account">
      <form onSubmit={add}>
        <div className="row mb">
          <div className="field"><label>Code</label><input required value={f.code} onChange={e=>setF({...f,code:e.target.value})} placeholder="e.g. 2511" style={{width:120}}/></div>
          <div className="field" style={{flex:1}}><label>Name</label><input required value={f.name} onChange={e=>setF({...f,name:e.target.value})} placeholder="e.g. HDFC Term Loan A/c 1234"/></div>
          <div className="field"><label>Type</label>
            <select value={f.account_type} onChange={e=>setF({...f,account_type:e.target.value,parent_code:""})}>
              {ACCT_TYPES.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></div>
          <div className="field"><label>Under group</label>
            <select value={f.parent_code} onChange={e=>setF({...f,parent_code:e.target.value})}>
              <option value="">— top level —</option>
              {groups.map(g=><option key={g.id} value={g.code}>{g.code} · {g.name}</option>)}</select></div>
          <div className="field" style={{alignSelf:"end"}}><label style={{display:"flex",gap:6,alignItems:"center",fontSize:12.5}}>
            <input type="checkbox" checked={f.is_group} onChange={e=>setF({...f,is_group:e.target.checked})}/> is a heading</label></div>
          <div className="field" style={{alignSelf:"end"}}><button className="btn">Add</button></div>
        </div>
        {err && <div className="err">{err}</div>}
        {ok && <div style={{color:"#16a34a",fontSize:12.5}}>{ok}</div>}
      </form>
      <div className="muted" style={{fontSize:12}}>Direct expenses sit under <b>5000 Direct Expenses &amp; COGS</b>, indirect under <b>6000 Indirect Expenses</b>, loans under <b>2500 Long-term Liabilities</b> or <b>2000 Current Liabilities</b>. A heading cannot receive postings.</div>
    </Panel>
    <Panel title="Chart of accounts">
      <DataTable rows={rows} pageSize={15} rowClass={a=>a.is_group?"row-group":undefined}
        searchKeys={["code","name","account_type"]}
        columns={[
          {key:"code",label:"Code"},
          {key:"name",label:"Name"},
          {key:"account_type",label:"Type"},
          {key:"under",label:"Under",sortable:false,render:a=>a.parent_id?byCode[a.parent_id]:"—"},
          {key:"postable",label:"Postable",sortable:false,render:a=>a.is_group?"heading":(a.allow_posting?"yes":"no")},
        ]}/>
    </Panel>
  </div>);
}

// Group & Tenancy admin — set up organizations (groups), assign companies to a
// group, grant company memberships, and configure inter-company account mappings.
function Tenancy(){
  const [orgs,setOrgs]=useState([]); const [companies,setCompanies]=useState([]);
  const [users,setUsers]=useState([]); const [maps,setMaps]=useState([]);
  const [msg,setMsg]=useState(""); const [err,setErr]=useState("");
  const [org,setOrg]=useState({code:"",name:""});
  const [assign,setAssign]=useState({company_id:"",organization_id:""});
  const [mem,setMem]=useState({user_id:"",company_id:"",is_default:true});
  const [icm,setIcm]=useState({company_id:"",counterparty_company_id:"",due_from_account_code:"",due_to_account_code:""});
  const reload=useCallback(()=>{ setErr("");
    api("/api/admin/organizations").then(setOrgs).catch(e=>setErr(e.message));
    api("/api/admin/companies").then(setCompanies).catch(e=>setErr(e.message));
    api("/api/admin/users").then(setUsers).catch(()=>{});
    api("/api/admin/intercompany-mappings").then(setMaps).catch(()=>{});
  },[]);
  useEffect(()=>{ reload(); },[reload]);
  const run=async(fn)=>{ setErr(""); setMsg(""); try{ await fn(); reload(); }catch(e){ setErr(e.message); } };
  const coName=id=>{ const c=companies.find(x=>x.id===id); return c?c.code:(id||"").slice(0,8); };
  return (<div>
    {err && <div className="alert-list"><div className="muted" style={{color:"var(--rose,#c0392b)"}}>⚠ {err}</div></div>}
    {msg && <div className="alert-list"><div className="muted">✓ {msg}</div></div>}

    <Panel title="Organizations (company groups)">
      <table><thead><tr><th>Code</th><th>Name</th></tr></thead>
        <tbody>{orgs.map(o=><tr key={o.id}><td>{o.code}</td><td>{o.name}</td></tr>)}
          {orgs.length===0 && <tr><td colSpan={2} className="muted">No groups yet.</td></tr>}</tbody></table>
      <div className="form-row" style={{marginTop:10,display:"flex",gap:8,flexWrap:"wrap",alignItems:"end"}}>
        <div className="field"><label>Code</label><input value={org.code} onChange={e=>setOrg({...org,code:e.target.value})} placeholder="GRP"/></div>
        <div className="field"><label>Name</label><input value={org.name} onChange={e=>setOrg({...org,name:e.target.value})} placeholder="Textile Group"/></div>
        <button onClick={()=>run(async()=>{ await api("/api/admin/organizations",{body:org}); setOrg({code:"",name:""}); setMsg("Group created"); })}>Create group</button>
      </div>
    </Panel>

    <Panel title="Assign a company to a group">
      <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"end"}}>
        <div className="field"><label>Company</label>
          <select value={assign.company_id} onChange={e=>setAssign({...assign,company_id:e.target.value})}>
            <option value="">— select —</option>{companies.map(c=><option key={c.id} value={c.id}>{c.code} · {c.name}{c.organization_id?" (grouped)":""}</option>)}</select></div>
        <div className="field"><label>Group</label>
          <select value={assign.organization_id} onChange={e=>setAssign({...assign,organization_id:e.target.value})}>
            <option value="">— none —</option>{orgs.map(o=><option key={o.id} value={o.id}>{o.code}</option>)}</select></div>
        <button disabled={!assign.company_id} onClick={()=>run(async()=>{ await api(`/api/admin/companies/${assign.company_id}/organization`,{body:{organization_id:assign.organization_id||null}}); setMsg("Company assignment updated"); })}>Assign</button>
      </div>
    </Panel>

    <Panel title="Grant company membership">
      <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"end"}}>
        <div className="field"><label>User</label>
          <select value={mem.user_id} onChange={e=>setMem({...mem,user_id:e.target.value})}>
            <option value="">— select —</option>{users.map(u=><option key={u.id} value={u.id}>{u.username}</option>)}</select></div>
        <div className="field"><label>Company</label>
          <select value={mem.company_id} onChange={e=>setMem({...mem,company_id:e.target.value})}>
            <option value="">— select —</option>{companies.map(c=><option key={c.id} value={c.id}>{c.code}</option>)}</select></div>
        <label style={{display:"flex",gap:4,alignItems:"center"}}><input type="checkbox" checked={mem.is_default} onChange={e=>setMem({...mem,is_default:e.target.checked})}/> default</label>
        <button disabled={!mem.user_id||!mem.company_id} onClick={()=>run(async()=>{ await api("/api/admin/memberships/company",{body:mem}); setMsg("Membership granted"); })}>Grant</button>
      </div>
    </Panel>

    <Panel title="Inter-company account mappings">
      <table><thead><tr><th>From → To</th><th>Approved</th><th></th></tr></thead>
        <tbody>{maps.map(m=><tr key={m.id}>
          <td>{coName(m.company_id)} → {coName(m.counterparty_company_id)}</td>
          <td>{m.is_approved?"✓":"pending"}</td>
          <td>{!m.is_approved && <button onClick={()=>run(async()=>{ await api(`/api/admin/intercompany-mappings/${m.id}/approve`,{method:"POST"}); setMsg("Mapping approved"); })}>Approve</button>}</td></tr>)}
          {maps.length===0 && <tr><td colSpan={3} className="muted">No mappings configured.</td></tr>}</tbody></table>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"end",marginTop:10}}>
        <div className="field"><label>Company</label>
          <select value={icm.company_id} onChange={e=>setIcm({...icm,company_id:e.target.value})}>
            <option value="">—</option>{companies.map(c=><option key={c.id} value={c.id}>{c.code}</option>)}</select></div>
        <div className="field"><label>Counterparty</label>
          <select value={icm.counterparty_company_id} onChange={e=>setIcm({...icm,counterparty_company_id:e.target.value})}>
            <option value="">—</option>{companies.map(c=><option key={c.id} value={c.id}>{c.code}</option>)}</select></div>
        <div className="field"><label>Due-from a/c</label><input value={icm.due_from_account_code} onChange={e=>setIcm({...icm,due_from_account_code:e.target.value})} placeholder="1050"/></div>
        <div className="field"><label>Due-to a/c</label><input value={icm.due_to_account_code} onChange={e=>setIcm({...icm,due_to_account_code:e.target.value})} placeholder="2070"/></div>
        <button disabled={!icm.company_id||!icm.counterparty_company_id} onClick={()=>run(async()=>{ await api("/api/admin/intercompany-mappings",{body:icm}); setIcm({company_id:"",counterparty_company_id:"",due_from_account_code:"",due_to_account_code:""}); setMsg("Mapping created (pending approval)"); })}>Add mapping</button>
      </div>
      <div className="muted" style={{marginTop:6}}>Due-from must be an ASSET account, due-to a LIABILITY account, in the company's own chart of accounts. An accountant approves before posting.</div>
    </Panel>
  </div>);
}

// Group (consolidated) dashboard — company-wise + consolidated figures for the
// user's company group, from the read-only consolidation APIs.
function GroupDash({refreshKey}){
  const [ctx,setCtx]=useState(null); const [pnl,setPnl]=useState(null);
  const [recon,setRecon]=useState([]); const [byFactory,setByFactory]=useState([]);
  const [err,setErr]=useState(""); const [note,setNote]=useState(""); const [loading,setLoading]=useState(true);
  useEffect(()=>{ let alive=true; setLoading(true); setErr(""); setNote("");
    api("/api/me/context").then(c=>{ if(!alive) return; setCtx(c);
      const org=(c.companies||[]).map(x=>x.organization_id).find(Boolean);
      if(!org){ setNote("No company group is configured. Assign companies to an Organization (Administration) to see consolidated figures."); setLoading(false); return; }
      Promise.all([
        api("/api/reports/consolidated/pnl?organization_id="+org).catch(e=>{setErr(e.message);return null;}),
        api("/api/admin/intercompany-reconciliation").catch(()=>[]),
        api("/api/costing/by-factory").catch(()=>[]),
      ]).then(([p,r,f])=>{ if(!alive) return; setPnl(p); setRecon(r||[]); setByFactory(f||[]); setLoading(false); });
    }).catch(e=>{ if(alive){ setErr(e.message); setLoading(false);} });
    return ()=>{alive=false;};
  },[refreshKey]);
  if(loading) return <Panel><div className="muted">Loading group figures…</div></Panel>;
  if(note) return <Panel title="Group Dashboard"><div className="muted">{note}</div></Panel>;
  if(err && !pnl) return <Panel title="Group Dashboard"><div className="muted">Consolidated view unavailable: {err}. You must be authorized for every company in the group.</div></Panel>;
  const companies=(pnl&&pnl.companies)||[]; const readyPairs=recon.filter(r=>r.ready_for_elimination);
  const net=pnl?Number(pnl.net_profit):0;
  const mono={fontFamily:"monospace",fontSize:".85em"};
  const cols={display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(320px,1fr))",gap:"12px"};
  return (<div>
    <div className="kpi-grid">
      <Stat icon="🏢" label="Companies in group" value={companies.length} tone="info"/>
      <Stat icon="💹" label="Consolidated income" value={pnl?inr(pnl.total_income):"—"} tone="green"/>
      <Stat icon="💸" label="Consolidated expense" value={pnl?inr(pnl.total_expense):"—"} tone="amber"/>
      <Stat icon="📊" label="Consolidated net profit" value={pnl?inr(pnl.net_profit):"—"} tone={net>=0?"green":"rose"}/>
    </div>
    {pnl && !pnl.approved && <div className="alert-list"><div className="muted">
      ⚠ {pnl.eliminations_pending.length} elimination(s) pending finance approval — figures exclude unapproved eliminations.</div></div>}
    <div style={cols}>
      <Panel title="Companies in the group">
        <table><thead><tr><th>Code</th><th>Name</th></tr></thead>
          <tbody>{companies.map(c=><tr key={c.id}><td>{c.code}</td><td>{c.name}</td></tr>)}</tbody></table>
      </Panel>
      <Panel title="Inter-company reconciliation">
        {recon.length===0 ? <div className="muted">No inter-company mappings configured.</div> :
        <table><thead><tr><th>Direction</th><th>Approved</th><th>Reciprocal</th><th>Ready</th></tr></thead>
          <tbody>{recon.map((r,i)=><tr key={i}>
            <td style={mono}>{(r.company_id||"").slice(0,8)} → {(r.counterparty_company_id||"").slice(0,8)}</td>
            <td>{r.approved?"✓":"—"}</td><td>{r.reciprocal_approved?"✓":(r.reciprocal_configured?"unapproved":"missing")}</td>
            <td>{r.ready_for_elimination?"✓":"—"}</td></tr>)}</tbody></table>}
        <div className="muted" style={{marginTop:6}}>{readyPairs.length} pair(s) ready for elimination.</div>
      </Panel>
    </div>
    <Panel title="Consolidated Profit & Loss">
      <table><thead><tr><th>Account</th><th className="num">Income</th><th className="num">Expense</th></tr></thead>
        <tbody>
          {(pnl.income||[]).map(r=><tr key={"i"+r.account_code}><td>{r.account_code} · {r.account_name}</td><td className="num">{inr2(r.amount)}</td><td className="num"></td></tr>)}
          {(pnl.expense||[]).map(r=><tr key={"e"+r.account_code}><td>{r.account_code} · {r.account_name}</td><td className="num"></td><td className="num">{inr2(r.amount)}</td></tr>)}
          <tr><td><b>Totals</b></td><td className="num"><b>{inr2(pnl.total_income)}</b></td><td className="num"><b>{inr2(pnl.total_expense)}</b></td></tr>
          <tr><td><b>Net profit</b></td><td className="num" colSpan={2}><b>{inr(pnl.net_profit)}</b></td></tr>
        </tbody></table>
    </Panel>
    {byFactory.length>0 && <Panel title="Factory production (acting company)">
      <table><thead><tr><th>Factory</th><th className="num">Knit kg</th><th className="num">Dye kg</th><th className="num">Efficiency %</th></tr></thead>
        <tbody>{byFactory.map((r,i)=><tr key={i}><td>{r.factory}</td><td className="num">{inr2(r.knitting_produced_kg)}</td>
          <td className="num">{inr2(r.dyeing_fabric_kg)}</td><td className="num">{r.knitting_efficiency_pct}</td></tr>)}</tbody></table>
    </Panel>}
  </div>);
}

// ---- Fabric BOM (version-controlled) + production orders -------------------
function FabricBOM(){
  const [boms,setBoms]=useState(null); const [sel,setSel]=useState(null); // selected BOM detail
  const [verId,setVerId]=useState(""); const [err,setErr]=useState(""); const [msg,setMsg]=useState("");
  const yarns=useList("/api/masters/yarns");
  const machines=useList("/api/masters/machines"); const shades=useList("/api/masters/shades");
  const fabrics=useList("/api/masters/fabric-types");
  const [nb,setNb]=useState({code:"",name:"",fabric_type_id:"",shade_id:"",finished_quality:""});
  const [spec,setSpec]=useState({}); const [yrows,setYrows]=useState([]); const [knit,setKnit]=useState({});
  const [frows,setFrows]=useState([]); const [cost,setCost]=useState(null);
  const [orders,setOrders]=useState([]); const [ord,setOrd]=useState({number:"",planned_qty_kg:"100",order_date:today()});
  const [odet,setOdet]=useState(null); const [reqs,setReqs]=useState([]); const [varr,setVarr]=useState(null);

  const loadBoms=useCallback(()=>api("/api/bom").then(setBoms).catch(e=>setErr(e.message)),[]);
  useEffect(()=>{loadBoms()},[loadBoms]);
  const openBom=async id=>{ setErr("");setMsg("");setVarr(null);setOdet(null);
    try{ const b=await api(`/api/bom/${id}`); setSel(b);
      const v=b.versions[b.versions.length-1]; selectVer(b,v.id);
      setOrders(await api("/api/bom/orders").then(os=>os.filter(o=>b.versions.some(x=>x.id===o.bom_version_id)))); }
    catch(e){ setErr(e.message); } };
  const selectVer=(b,id)=>{ setVerId(id); const v=(b||sel).versions.find(x=>x.id===id); setCost(null);
    setSpec({construction:v.construction||"",composition:v.composition||"",yarn_count:v.yarn_count||"",gsm:v.gsm||"",
      width_cm:v.width_cm||"",gauge:v.gauge||"",diameter_inch:v.diameter_inch||"",stitch_length_mm:v.stitch_length_mm||"",
      std_knitting_cost:v.std_knitting_cost||"",
      std_dyeing_cost:v.std_dyeing_cost||"",std_labour_cost:v.std_labour_cost||"",std_overhead_cost:v.std_overhead_cost||""});
    setYrows(v.yarns.length?v.yarns.map(y=>({...y})):[{yarn_id:"",blend_percent:"100",std_consumption_per_kg:"1.0",wastage_percent:"2",process_loss_percent:"1",std_unit_cost:""}]);
    setKnit(v.knitting||{machine_type:"KNITTING"}); setFrows(v.finishing_steps.length?v.finishing_steps.map(s=>({...s})):[]); };
  const curVer=sel&&sel.versions.find(x=>x.id===verId);
  const isDraft=curVer&&curVer.status==="DRAFT";

  const createBom=async e=>{ e.preventDefault(); setErr("");
    try{ const b=await api("/api/bom",{method:"POST",body:{...nb,fabric_type_id:nb.fabric_type_id||null,shade_id:nb.shade_id||null}});
      setNb({code:"",name:"",fabric_type_id:"",shade_id:"",finished_quality:""}); await loadBoms(); openBom(b.id); }
    catch(ex){ setErr(ex.message); } };
  const clean=o=>{const r={};for(const k in o){if(o[k]!==""&&o[k]!=null)r[k]=o[k];}return r;};
  const saveSpec=async()=>{ setErr("");setMsg("");
    try{ await api(`/api/bom/versions/${verId}/spec`,{method:"PUT",body:clean(spec)}); setMsg("Spec saved"); refresh(); }
    catch(e){ setErr(e.message);} };
  const saveYarns=async()=>{ setErr("");setMsg("");
    try{ await api(`/api/bom/versions/${verId}/yarns`,{method:"PUT",body:yrows.map((y,i)=>({...y,sequence:i+1}))}); setMsg("Yarn BOM saved"); refresh(); }
    catch(e){ setErr(e.message);} };
  const saveKnit=async()=>{ setErr("");setMsg("");
    try{ await api(`/api/bom/versions/${verId}/knitting`,{method:"PUT",body:clean(knit)}); setMsg("Knitting saved"); refresh(); }
    catch(e){ setErr(e.message);} };
  const saveFin=async()=>{ setErr("");setMsg("");
    try{ await api(`/api/bom/versions/${verId}/finishing`,{method:"PUT",body:frows.map((s,i)=>({...s,sequence:i+1}))}); setMsg("Finishing saved"); refresh(); }
    catch(e){ setErr(e.message);} };
  const refresh=async()=>{ const b=await api(`/api/bom/${sel.id}`); setSel(b); };
  const previewCost=async()=>{ setErr(""); try{ setCost(await api(`/api/bom/versions/${verId}/cost`)); }catch(e){setErr(e.message);} };
  const approve=async()=>{ setErr("");setMsg("");
    try{ await api(`/api/bom/versions/${verId}/approve`,{method:"POST",body:{effective_from:today()}}); setMsg("Version approved"); await refresh(); }
    catch(e){ setErr(e.message);} };
  const newRev=async()=>{ setErr("");
    try{ const v=await api(`/api/bom/${sel.id}/versions`,{method:"POST",body:{clone_from_version_id:verId,revision_notes:"revision"}});
      const b=await api(`/api/bom/${sel.id}`); setSel(b); selectVer(b,v.id); setMsg("New draft version created"); }
    catch(e){ setErr(e.message);} };

  const createOrder=async e=>{ e.preventDefault(); setErr("");setMsg("");
    try{ const o=await api("/api/bom/orders",{method:"POST",body:{number:ord.number,planned_qty_kg:ord.planned_qty_kg,
      bom_id:sel.id,order_date:ord.order_date}});
      setOrders([...orders,o]); setOrd({number:"",planned_qty_kg:"100",order_date:today()}); openOrder(o.id); setMsg("Order created; reservations exploded"); }
    catch(ex){ setErr(ex.message);} };
  const openOrder=async id=>{ setVarr(null); const o=await api(`/api/bom/orders/${id}`); setOdet(o);
    setReqs(await api(`/api/bom/orders/${id}/requirements`)); };
  const issue=async r=>{ setErr("");
    try{ await api(`/api/bom/orders/${odet.id}/issue`,{method:"POST",body:{reservation_id:r.reservation_id,qty:r.planned_qty}});
      openOrder(odet.id); }
    catch(e){ setErr(e.message);} };
  const complete=async()=>{ setErr("");
    try{ await api(`/api/bom/orders/${odet.id}/complete`,{method:"POST",body:{produced_qty_kg:odet.planned_qty_kg}});
      setVarr(await api(`/api/bom/orders/${odet.id}/variance`)); openOrder(odet.id); }
    catch(e){ setErr(e.message);} };
  const showVar=async()=>{ setErr(""); try{ setVarr(await api(`/api/bom/orders/${odet.id}/variance`)); }catch(e){setErr(e.message);} };

  const setYr=(i,k,v)=>setYrows(yrows.map((r,j)=>j===i?{...r,[k]:v}:r));
  const setFr=(i,k,v)=>setFrows(frows.map((r,j)=>j===i?{...r,[k]:v}:r));
  return (<div>
    {err && <div className="err">{err}</div>}{msg && <div className="badge ok" style={{marginBottom:8}}>{msg}</div>}
    <div className="grid3">
      <Panel title="New fabric BOM">
        <form onSubmit={createBom}>
          <div className="field"><label>Code</label><input required value={nb.code} onChange={e=>setNb({...nb,code:e.target.value})} placeholder="FB-NAVY-SJ"/></div>
          <div className="field"><label>Name</label><input required value={nb.name} onChange={e=>setNb({...nb,name:e.target.value})} placeholder="Navy Single Jersey 160"/></div>
          <div className="field"><label>Fabric type</label><select value={nb.fabric_type_id} onChange={e=>setNb({...nb,fabric_type_id:e.target.value})}>
            <option value="">—</option>{fabrics.map(f=><option key={f.id} value={f.id}>{f.label||f.code}</option>)}</select></div>
          <div className="field"><label>Shade</label><select value={nb.shade_id} onChange={e=>setNb({...nb,shade_id:e.target.value})}>
            <option value="">—</option>{shades.map(s=><option key={s.id} value={s.id}>{s.label||s.code||s.name}</option>)}</select></div>
          <div className="field"><label>Finished quality</label><input value={nb.finished_quality} onChange={e=>setNb({...nb,finished_quality:e.target.value})} placeholder="Export A"/></div>
          <button className="btn">Create BOM</button>
        </form>
      </Panel>
      <Panel title="Fabric BOMs">
        {!boms?"Loading…":!boms.length?<div className="muted">No BOMs yet.</div>:
        <table><thead><tr><th>Code</th><th>Name</th></tr></thead><tbody>
          {boms.map(b=><tr key={b.id} className={sel&&sel.id===b.id?"sel":""} style={{cursor:"pointer"}} onClick={()=>openBom(b.id)}>
            <td>{b.code}</td><td>{b.name}</td></tr>)}
        </tbody></table>}
      </Panel>
      {sel && <Panel title={"Versions · "+sel.code}>
        <table><thead><tr><th>#</th><th>Status</th><th>Effective</th><th className="num">Cost/kg</th></tr></thead><tbody>
          {sel.versions.map(v=><tr key={v.id} className={v.id===verId?"sel":""} style={{cursor:"pointer"}} onClick={()=>selectVer(sel,v.id)}>
            <td>v{v.version_no}</td><td><span className={"badge "+(v.status==="APPROVED"?"ok":v.status==="DRAFT"?"":"bad")}>{v.status}</span></td>
            <td>{v.effective_from||"—"}{v.effective_to?" → "+v.effective_to:""}</td><td className="num">{inr2(v.std_total_cost_per_kg)}</td></tr>)}
        </tbody></table>
        {curVer && !isDraft && <button className="btn" style={{marginTop:8}} onClick={newRev}>New revision (clone v{curVer.version_no})</button>}
      </Panel>}
    </div>

    {curVer && <Panel title={"Version v"+curVer.version_no+(isDraft?" (draft — editable)":" ("+curVer.status+")")}>
      {!isDraft && <div className="muted" style={{marginBottom:8}}>Approved versions are frozen. Create a new revision to change the BOM. Historical production orders keep the version they were created against.</div>}
      <div className="grid2">
        <div>
          <h4>Finished-fabric spec</h4>
          <div className="row">
            {["construction","composition","yarn_count","gsm","width_cm","gauge","diameter_inch","stitch_length_mm"].map(k=>
              <div className="field" key={k}><label>{k.replace(/_/g," ")}</label>
                <input disabled={!isDraft} value={spec[k]||""} onChange={e=>setSpec({...spec,[k]:e.target.value})}/></div>)}
          </div>
          <div className="muted" style={{fontSize:12,margin:"4px 0"}}>Yarn-only BOM — dye/chemical usage is not tracked at this stage.</div>
          <div className="row">
            {["std_knitting_cost","std_dyeing_cost","std_labour_cost","std_overhead_cost"].map(k=>
              <div className="field" key={k}><label>{k.replace("std_","").replace(/_/g," ")} /kg</label>
                <input disabled={!isDraft} value={spec[k]||""} onChange={e=>setSpec({...spec,[k]:e.target.value})}/></div>)}
          </div>
          {isDraft && <button className="btn" onClick={saveSpec}>Save spec</button>}
          <h4 style={{marginTop:14}}>Knitting process</h4>
          <div className="row">
            <div className="field"><label>machine type</label><input disabled={!isDraft} value={knit.machine_type||""} onChange={e=>setKnit({...knit,machine_type:e.target.value})}/></div>
            <div className="field"><label>gauge</label><input disabled={!isDraft} value={knit.gauge||""} onChange={e=>setKnit({...knit,gauge:e.target.value})}/></div>
            <div className="field"><label>feeders</label><input disabled={!isDraft} value={knit.feeders||""} onChange={e=>setKnit({...knit,feeders:e.target.value})}/></div>
            <div className="field"><label>stitch len mm</label><input disabled={!isDraft} value={knit.stitch_length_mm||""} onChange={e=>setKnit({...knit,stitch_length_mm:e.target.value})}/></div>
            <div className="field"><label>efficiency %</label><input disabled={!isDraft} value={knit.efficiency_percent||""} onChange={e=>setKnit({...knit,efficiency_percent:e.target.value})}/></div>
          </div>
          {isDraft && <button className="btn" onClick={saveKnit}>Save knitting</button>}
        </div>
        <div>
          <h4>Yarn BOM (blend)</h4>
          <table><thead><tr><th>Yarn</th><th>Blend %</th><th>Cons/kg</th><th>Wst%</th><th>Loss%</th><th>₹/kg</th><th></th></tr></thead>
            <tbody>{yrows.map((y,i)=><tr key={i}>
              <td><select disabled={!isDraft} value={y.yarn_id||""} onChange={e=>setYr(i,"yarn_id",e.target.value)}>
                <option value="">—</option>{yarns.map(o=><option key={o.id} value={o.id}>{o.code}</option>)}</select></td>
              <td><input disabled={!isDraft} style={{width:56}} value={y.blend_percent||""} onChange={e=>setYr(i,"blend_percent",e.target.value)}/></td>
              <td><input disabled={!isDraft} style={{width:56}} value={y.std_consumption_per_kg||""} onChange={e=>setYr(i,"std_consumption_per_kg",e.target.value)}/></td>
              <td><input disabled={!isDraft} style={{width:44}} value={y.wastage_percent||""} onChange={e=>setYr(i,"wastage_percent",e.target.value)}/></td>
              <td><input disabled={!isDraft} style={{width:44}} value={y.process_loss_percent||""} onChange={e=>setYr(i,"process_loss_percent",e.target.value)}/></td>
              <td><input disabled={!isDraft} style={{width:64}} value={y.std_unit_cost||""} onChange={e=>setYr(i,"std_unit_cost",e.target.value)}/></td>
              <td>{isDraft&&<button className="btn-ghost" onClick={()=>setYrows(yrows.filter((_,j)=>j!==i))}>✕</button>}</td></tr>)}
            </tbody></table>
          {isDraft && <div><button className="btn-ghost" onClick={()=>setYrows([...yrows,{yarn_id:"",blend_percent:"",std_consumption_per_kg:"1.0",wastage_percent:"2",process_loss_percent:"1",std_unit_cost:""}])}>+ yarn</button>
            <button className="btn" onClick={saveYarns}>Save yarn BOM</button></div>}
          <h4 style={{marginTop:14}}>Finishing steps</h4>
          <table><thead><tr><th>Process</th><th>Machine</th><th>₹/kg</th><th></th></tr></thead>
            <tbody>{frows.map((s,i)=><tr key={i}>
              <td><select disabled={!isDraft} value={s.process_type||""} onChange={e=>setFr(i,"process_type",e.target.value)}>
                {["COMPACTING","STENTER","HEAT_SETTING","RAISING","SLITTING","DRYING","HYDRO"].map(p=><option key={p}>{p}</option>)}</select></td>
              <td><input disabled={!isDraft} style={{width:80}} value={s.machine_type||""} onChange={e=>setFr(i,"machine_type",e.target.value)}/></td>
              <td><input disabled={!isDraft} style={{width:64}} value={s.std_cost_per_kg||""} onChange={e=>setFr(i,"std_cost_per_kg",e.target.value)}/></td>
              <td>{isDraft&&<button className="btn-ghost" onClick={()=>setFrows(frows.filter((_,j)=>j!==i))}>✕</button>}</td></tr>)}
            </tbody></table>
          {isDraft && <div><button className="btn-ghost" onClick={()=>setFrows([...frows,{process_type:"COMPACTING",std_cost_per_kg:""}])}>+ step</button>
            <button className="btn" onClick={saveFin}>Save finishing</button></div>}
        </div>
      </div>
      <div style={{marginTop:12,display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
        <button className="btn-ghost" onClick={previewCost}>Preview standard cost</button>
        {isDraft && <button className="btn" onClick={approve}>Approve version</button>}
        {cost && <span>Yarn ₹{inr2(cost.std_yarn_cost)} · Finishing ₹{inr2(cost.std_finishing_cost)} ·
          <b> Total ₹{inr2(cost.std_total_cost_per_kg)}/kg</b></span>}
      </div>
    </Panel>}

    {sel && <Panel title={"Production orders · "+sel.code}>
      <form className="row" onSubmit={createOrder}>
        <div className="field"><label>Order no.</label><input required value={ord.number} onChange={e=>setOrd({...ord,number:e.target.value})} placeholder="PO-26001"/></div>
        <div className="field"><label>Planned kg</label><input required value={ord.planned_qty_kg} onChange={e=>setOrd({...ord,planned_qty_kg:e.target.value})}/></div>
        <div className="field"><label>Order date</label><input type="date" value={ord.order_date} onChange={e=>setOrd({...ord,order_date:e.target.value})}/></div>
        <button className="btn">Create order (explode BOM)</button>
      </form>
      <div className="muted" style={{fontSize:12,margin:"4px 0"}}>Uses the approved version effective on the order date and stamps it onto the order.</div>
      {!!orders.length && <table><thead><tr><th>No.</th><th>Status</th><th className="num">Planned</th><th className="num">Produced</th><th></th></tr></thead>
        <tbody>{orders.map(o=><tr key={o.id} className={odet&&odet.id===o.id?"sel":""}><td>{o.number}</td><td>{o.status}</td>
          <td className="num">{inr2(o.planned_qty_kg)}</td><td className="num">{inr2(o.produced_qty_kg)}</td>
          <td><button className="btn-ghost" onClick={()=>openOrder(o.id)}>open</button></td></tr>)}</tbody></table>}
      {odet && <div style={{marginTop:10}}>
        <h4>Order {odet.number} — material requirements</h4>
        <table><thead><tr><th>Stage</th><th>Type</th><th className="num">Planned</th><th className="num">Issued</th><th className="num">On hand</th><th className="num">Short</th><th></th></tr></thead>
          <tbody>{reqs.map((r,i)=><tr key={i}><td>{r.stage}</td><td>{r.item_type}</td><td className="num">{inr2(r.planned_qty)}</td>
            <td className="num">{inr2(r.issued_qty)}</td><td className="num">{inr2(r.on_hand)}</td>
            <td className="num">{Number(r.shortage)>0?<span className="badge bad">{inr2(r.shortage)}</span>:"0"}</td>
            <td>{r.status!=="ISSUED"&&<button className="btn-ghost" onClick={()=>issue(r)}>issue</button>}</td></tr>)}</tbody></table>
        <div style={{display:"flex",gap:8,marginTop:8}}>
          <button className="btn" onClick={complete}>Complete & cost variance</button>
          <button className="btn-ghost" onClick={showVar}>Variance report</button></div>
        {varr && <div>
          <div className="muted" style={{fontSize:12,margin:"6px 0"}}>Material cost is a genuine standard-vs-actual comparison (FIFO issues). Conversion is standard; its <b>actual</b> is shown only when genuinely recorded.</div>
          <table style={{marginTop:4}}><thead><tr><th>Stage</th>
            <th className="num">Plan matl ₹</th><th className="num">Actual matl ₹</th><th className="num">Matl var ₹</th><th className="num">%</th>
            <th className="num">Std conv ₹</th><th className="num">Actual conv ₹</th><th className="num">Actual out kg</th><th className="num">Wastage</th></tr></thead>
          <tbody>{varr.stages.map((s,i)=><tr key={i}><td>{s.stage}</td>
            <td className="num">{inr2(s.planned_material_cost)}</td><td className="num">{inr2(s.actual_material_cost)}</td>
            <td className="num">{inr2(s.material_variance)}</td><td className="num">{s.material_variance_pct}</td>
            <td className="num">{inr2(s.std_conversion_cost)}</td>
            <td className="num">{s.actual_conversion_cost==null?<span className="muted" title="not recorded per order">n/a</span>:inr2(s.actual_conversion_cost)}</td>
            <td className="num">{s.actual_output_kg==null?<span className="muted">—</span>:inr2(s.actual_output_kg)}</td>
            <td className="num">{inr2(s.wastage_qty)}</td></tr>)}
            <tr style={{fontWeight:700}}><td>Total</td>
              <td className="num">{inr2(varr.total_planned_material_cost)}</td><td className="num">{inr2(varr.total_actual_material_cost)}</td>
              <td className="num">{inr2(varr.total_material_variance)}</td><td className="num">{varr.total_material_variance_pct}</td>
              <td className="num">{inr2(varr.total_std_conversion_cost)}</td><td className="num" colSpan="3">
                {varr.total_actual_cost==null?<span className="muted">actual total n/a (conversion not posted per order)</span>:inr2(varr.total_actual_cost)}</td></tr>
          </tbody></table>
          {(varr.notes||[]).map((n,i)=><div key={i} className="muted" style={{fontSize:12,marginTop:4}}>⚠ {n}</div>)}
        </div>}
      </div>}
    </Panel>}
  </div>);
}

// ---- Production Cost: grey fabric actual cost + dyeing → actual dyed cost ----
function ProductionCost(){
  const [orders,setOrders]=useState([]); const [gc,setGc]=useState(null); const [goid,setGoid]=useState("");
  const [conv,setConv]=useState(""); const [jobs,setJobs]=useState([]); const [err,setErr]=useState(""); const [msg,setMsg]=useState("");
  const [nj,setNj]=useState({number:"",mode:"INTERNAL",grey_production_order_id:""});
  const [sel,setSel]=useState(null); const [jc,setJc]=useState(null); const [tr,setTr]=useState(null);
  const [iss,setIss]=useState({source_production_order_id:"",qty_kg:""});
  const [costs,setCosts]=useState({dyeing_charge:"",jobwork_charge:"",chemical_cost:"",finishing_cost:"",testing_cost:"",other_cost:"",reprocess_cost:""});
  const [outLot,setOutLot]=useState({lot_code:"",qty_kg:""}); const [comp,setComp]=useState({rejection_kg:"0",reprocess_qty_kg:"0"});

  const loadOrders=useCallback(()=>api("/api/bom/orders").then(setOrders).catch(e=>setErr(e.message)),[]);
  const loadJobs=useCallback(()=>api("/api/dyeing/jobs").then(setJobs).catch(e=>setErr(e.message)),[]);
  useEffect(()=>{loadOrders();loadJobs()},[loadOrders,loadJobs]);
  const E=fn=>fn.catch(e=>setErr(e.message));
  const openGrey=async id=>{ setErr("");setGoid(id); try{ setGc(await api(`/api/bom/orders/${id}/grey-cost`)); }catch(e){setErr(e.message);} };
  const saveConv=async()=>{ setErr("");setMsg(""); try{ await api(`/api/bom/orders/${goid}/conversion-cost`,{method:"POST",body:{amount:conv}}); setMsg("Conversion cost recorded"); openGrey(goid);}catch(e){setErr(e.message);} };
  const valueGrey=async()=>{ setErr("");setMsg(""); try{ await api(`/api/bom/orders/${goid}/value-grey`,{method:"POST",body:{}}); setMsg("Grey fabric valued into inventory"); openGrey(goid); loadOrders(); }catch(e){setErr(e.message);} };

  const createJob=async e=>{ e.preventDefault(); setErr("");setMsg(""); try{ const j=await api("/api/dyeing/jobs",{method:"POST",body:{...nj,grey_production_order_id:nj.grey_production_order_id||null}}); setNj({number:"",mode:"INTERNAL",grey_production_order_id:""}); await loadJobs(); openJob(j.id);}catch(ex){setErr(ex.message);} };
  const openJob=async id=>{ setErr("");setTr(null); try{ const j=await api(`/api/dyeing/jobs/${id}`); setSel(j); setIss({source_production_order_id:j.grey_production_order_id||"",qty_kg:""}); try{setJc(await api(`/api/dyeing/jobs/${id}/cost`));}catch(e){setJc(null);} }catch(e){setErr(e.message);} };
  const refreshJob=async()=>{ if(sel) await openJob(sel.id); };
  const clean=o=>{const r={};for(const k in o){if(o[k]!==""&&o[k]!=null)r[k]=o[k];}return r;};
  const doIssue=async()=>{ setErr("");setMsg(""); try{ await api(`/api/dyeing/jobs/${sel.id}/issue-grey`,{method:"POST",body:iss}); setMsg("Grey issued (FIFO)"); refreshJob();}catch(e){setErr(e.message);} };
  const saveCosts=async()=>{ setErr("");setMsg(""); try{ await api(`/api/dyeing/jobs/${sel.id}/costs`,{method:"POST",body:clean(costs)}); setMsg("Costs recorded"); refreshJob();}catch(e){setErr(e.message);} };
  const addOutput=async()=>{ setErr("");setMsg(""); try{ await api(`/api/dyeing/jobs/${sel.id}/output`,{method:"POST",body:{lots:[outLot]}}); setOutLot({lot_code:"",qty_kg:""}); setMsg("Output lot added"); refreshJob();}catch(e){setErr(e.message);} };
  const complete=async()=>{ setErr("");setMsg(""); try{ await api(`/api/dyeing/jobs/${sel.id}/complete`,{method:"POST",body:comp}); setMsg("Job completed + dyed fabric valued"); refreshJob();}catch(e){setErr(e.message);} };
  const showTrace=async()=>{ setErr(""); try{ setTr(await api(`/api/dyeing/jobs/${sel.id}/trace`)); }catch(e){setErr(e.message);} };
  const num=v=>v==null?"—":inr2(v);

  return (<div>
    {err && <div className="err">{err}</div>}{msg && <div className="badge ok" style={{marginBottom:8}}>{msg}</div>}
    <div className="grid2">
      <Panel title="Grey fabric — standard vs actual cost">
        <table><thead><tr><th>Order</th><th>Status</th><th className="num">Produced</th><th>Grey valued</th></tr></thead>
          <tbody>{orders.map(o=><tr key={o.id} className={goid===o.id?"sel":""} style={{cursor:"pointer"}} onClick={()=>openGrey(o.id)}>
            <td>{o.number}</td><td>{o.status}</td><td className="num">{inr2(o.produced_qty_kg)}</td>
            <td>{Number(o.grey_inventory_value)>0?<span className="badge ok">valued</span>:<span className="muted">no</span>}</td></tr>)}</tbody></table>
        {gc && <div style={{marginTop:10}}>
          <h4>{gc.number} — grey cost/kg</h4>
          <table><tbody>
            <tr><td>Standard grey cost/kg</td><td className="num">{num(gc.std_grey_cost_per_kg)}</td></tr>
            <tr><td>Actual yarn (FIFO)</td><td className="num">{num(gc.actual_yarn_cost)}</td></tr>
            <tr><td>Actual knitting conversion</td><td className="num">{gc.actual_knitting_conversion_cost==null?<span className="muted">not entered</span>:inr2(gc.actual_knitting_conversion_cost)}</td></tr>
            <tr style={{fontWeight:700}}><td>Actual grey cost/kg</td><td className="num">{num(gc.actual_grey_cost_per_kg)}</td></tr>
            <tr><td>Variance/kg (actual − std)</td><td className="num">{num(gc.variance_per_kg)}</td></tr>
          </tbody></table>
          {!gc.cost_complete && <div className="muted" style={{fontSize:12}}>⚠ Incomplete: missing {gc.missing_cost_components.join(", ")}</div>}
          <div className="row" style={{marginTop:6}}>
            <div className="field"><label>Actual knitting conversion ₹</label><input value={conv} onChange={e=>setConv(e.target.value)} placeholder="e.g. 600"/></div>
            <button className="btn-ghost" onClick={saveConv}>Record conversion</button>
            {!gc.grey_valued && <button className="btn" onClick={valueGrey}>Value grey → inventory</button>}
          </div>
        </div>}
      </Panel>

      <Panel title="Dyeing jobs">
        <form className="row" onSubmit={createJob}>
          <div className="field"><label>Job no.</label><input required value={nj.number} onChange={e=>setNj({...nj,number:e.target.value})} placeholder="DJ-1"/></div>
          <div className="field"><label>Mode</label><select value={nj.mode} onChange={e=>setNj({...nj,mode:e.target.value})}><option>INTERNAL</option><option>JOBWORK</option></select></div>
          <div className="field"><label>Grey order</label><select value={nj.grey_production_order_id} onChange={e=>setNj({...nj,grey_production_order_id:e.target.value})}>
            <option value="">—</option>{orders.filter(o=>Number(o.grey_inventory_value)>0).map(o=><option key={o.id} value={o.id}>{o.number}</option>)}</select></div>
          <button className="btn">Create job</button>
        </form>
        {!!jobs.length && <table style={{marginTop:6}}><thead><tr><th>No.</th><th>Mode</th><th>Status</th><th className="num">In</th><th className="num">Out</th><th></th></tr></thead>
          <tbody>{jobs.map(j=><tr key={j.id} className={sel&&sel.id===j.id?"sel":""}><td>{j.number}</td><td>{j.mode}</td><td>{j.status}</td>
            <td className="num">{inr2(j.input_qty_kg)}</td><td className="num">{inr2(j.output_qty_kg)}</td>
            <td><button className="btn-ghost" onClick={()=>openJob(j.id)}>open</button></td></tr>)}</tbody></table>}
      </Panel>
    </div>

    {sel && <Panel title={"Dyeing job "+sel.number+" ("+sel.status+")"}>
      <div className="grid2">
        <div>
          <h4>Operate</h4>
          <div className="row">
            <div className="field"><label>Issue grey qty kg</label><input value={iss.qty_kg} onChange={e=>setIss({...iss,qty_kg:e.target.value})}/></div>
            <button className="btn-ghost" onClick={doIssue} disabled={sel.status!=="PLANNED"&&sel.status!=="IN_PROGRESS"}>Issue grey (FIFO)</button>
          </div>
          <div className="row">
            {["dyeing_charge","jobwork_charge","chemical_cost","finishing_cost","testing_cost","other_cost","reprocess_cost"].map(k=>
              <div className="field" key={k} style={{minWidth:120}}><label>{k.replace(/_/g," ")} ₹</label>
                <input value={costs[k]} onChange={e=>setCosts({...costs,[k]:e.target.value})}/></div>)}
          </div>
          <button className="btn-ghost" onClick={saveCosts}>Record costs</button>
          <div className="row" style={{marginTop:8}}>
            <div className="field"><label>Output lot code</label><input value={outLot.lot_code} onChange={e=>setOutLot({...outLot,lot_code:e.target.value})}/></div>
            <div className="field"><label>Output qty kg</label><input value={outLot.qty_kg} onChange={e=>setOutLot({...outLot,qty_kg:e.target.value})}/></div>
            <button className="btn-ghost" onClick={addOutput}>Add output lot</button>
          </div>
          <div className="row">
            <div className="field"><label>Rejection kg</label><input value={comp.rejection_kg} onChange={e=>setComp({...comp,rejection_kg:e.target.value})}/></div>
            <div className="field"><label>Reprocess kg</label><input value={comp.reprocess_qty_kg} onChange={e=>setComp({...comp,reprocess_qty_kg:e.target.value})}/></div>
            <button className="btn" onClick={complete} disabled={sel.status==="COMPLETED"}>Complete + value dyed</button>
            <button className="btn-ghost" onClick={showTrace}>Trace</button>
          </div>
        </div>
        <div>
          <h4>Actual dyed fabric cost</h4>
          {!jc?<div className="muted">—</div>:<div>
            {!jc.cost_complete && <div className="badge bad" style={{marginBottom:6}}>INCOMPLETE</div>}
            <table><tbody>
              <tr><td>Grey consumed (FIFO)</td><td className="num">{num(jc.cost_components.grey_cost_consumed)}</td></tr>
              {["dyeing_charge","jobwork_charge","chemical_cost","finishing_cost","testing_cost","other_cost","reprocess_cost"].map(k=>
                <tr key={k}><td>{k.replace(/_/g," ")}</td><td className="num">{jc.cost_components[k]==null?<span className="muted">not recorded</span>:inr2(jc.cost_components[k])}</td></tr>)}
              <tr style={{fontWeight:700}}><td>Total actual cost</td><td className="num">{num(jc.total_actual_cost)}</td></tr>
              <tr><td>Input / saleable output kg</td><td className="num">{inr2(jc.input_qty_kg)} / {inr2(jc.saleable_output_kg)}</td></tr>
              <tr><td>Process loss / %</td><td className="num">{inr2(jc.process_loss_kg)} / {jc.loss_pct==null?"—":jc.loss_pct}</td></tr>
              <tr style={{fontWeight:700}}><td>Actual dyed cost/kg</td><td className="num">{jc.actual_dyed_cost_per_kg==null?"—":inr2(jc.actual_dyed_cost_per_kg)}{!jc.cost_complete&&" (lower bound)"}</td></tr>
            </tbody></table>
            {jc.note && <div className="muted" style={{fontSize:12,marginTop:4}}>⚠ {jc.note}</div>}
            {!!(jc.output_lots||[]).length && <table style={{marginTop:6}}><thead><tr><th>Lot</th><th className="num">kg</th><th className="num">Alloc ₹</th><th className="num">₹/kg</th></tr></thead>
              <tbody>{jc.output_lots.map((l,i)=><tr key={i}><td>{l.lot_code}</td><td className="num">{inr2(l.qty_kg)}</td><td className="num">{inr2(l.allocated_cost)}</td><td className="num">{num(l.cost_per_kg)}</td></tr>)}</tbody></table>}
          </div>}
          {tr && <div style={{marginTop:8}}><h4>Traceability</h4>
            {tr.grey_sources.map((g,i)=><div key={i} className="muted" style={{fontSize:12}}>
              Grey PO {g.number} — {inr2(g.grey_issued_kg)} kg @ ₹{inr2(g.grey_issue_cost)} · yarn issues: {g.yarn_issues.length}
              {g.yarn_issues.map((y,j)=><div key={j} style={{marginLeft:12}}>· yarn {String(y.yarn_item_id).slice(0,8)} lot {y.lot_id?String(y.lot_id).slice(0,8):"—"} {inr2(y.qty_kg)}kg ₹{inr2(y.cost)}</div>)}
            </div>)}
          </div>}
        </div>
      </div>
    </Panel>}
  </div>);
}

const PAGES={
  group:{t:"Group Dashboard",c:GroupDash},
  owner:{t:"Owner Dashboard",c:OwnerDash}, factory:{t:"Factory Dashboard",c:FactoryDash},
  accounts:{t:"Accounts Dashboard",c:AccountsDash}, chemical:{t:"Chemical Dashboard",c:ChemicalDash},
  purchaseorders:{t:"Purchase Orders",c:PurchaseOrders},
  purchase:{t:"Purchase (GRN)",c:Purchase}, knitting:{t:"Knitting Production",c:Knitting},
  batching:{t:"Batching",c:Batching},
  bom:{t:"Fabric BOM & Production Orders",c:FabricBOM},
  prodcost:{t:"Production Cost (Grey → Dyed)",c:ProductionCost},
  inventoryops:{t:"Inventory Ops (transfer / split / merge)",c:InventoryOps},
  stock:{t:"Stock on Hand",c:StockOnHand},
  stores:{t:"Stores & Spares",c:Stores},
  pipeline:{t:"Sales Pipeline (enquiry → quote → SO)",c:SalesPipeline},
  salesorders:{t:"Sales Orders & Dispatch",c:SalesOrders},
  sales:{t:"Sales Invoice",c:Sales}, dyeing:{t:"Dyeing & Dispensing",c:Dyeing},
  jobwork:{t:"Job Work (inward / outward)",c:JobWork},
  customers:{t:"Customers",c:()=> <PartyPage kind="customers"/>},
  suppliers:{t:"Suppliers",c:()=> <PartyPage kind="suppliers"/>},
  warehouses:{t:"Warehouses",c:Warehouses}, quality:{t:"Quality & Finishing",c:Quality},
  assets:{t:"Fixed Assets & Depreciation",c:FixedAssets},
  payroll:{t:"Payroll",c:Payroll},
  approvals:{t:"Approvals (maker-checker)",c:Approvals},
  notifications:{t:"Notifications",c:Notifications},
  acctops:{t:"GST Adjustments & Year-End",c:AccountingOps},
  costing:{t:"Costing & Profitability",c:Costing}, devices:{t:"Devices & Labels",c:Devices},
  masters:{t:"Master Data",c:MastersManager}, coa:{t:"Chart of Accounts",c:ChartOfAccounts},
  vouchers:{t:"Vouchers",c:Vouchers}, admin:{t:"Administration",c:Admin},
  tenancy:{t:"Group & Tenancy",c:Tenancy},
  reports:{t:"Reports",c:Reports},
};
const NAV_ICON={group:"🏛️",owner:"📊",factory:"🏭",accounts:"💼",chemical:"🧪",bom:"🧵",prodcost:"💷",purchaseorders:"📝",purchase:"📥",
  knitting:"🧶",batching:"🧺",pipeline:"🎯",salesorders:"🛒",sales:"🧾",dyeing:"🎨",vouchers:"📒",
  inventoryops:"🔁",stock:"📦",stores:"🪡",jobwork:"🔄",masters:"🗃️",coa:"📚",customers:"👤",suppliers:"🏢",warehouses:"🏬",quality:"🔍",assets:"🏗️",
  payroll:"💰",costing:"📐",devices:"🖨️",reports:"📈",approvals:"✔️",notifications:"🔔",acctops:"🧮",admin:"⚙️",tenancy:"🏛️"};
function fyLabel(){ const d=new Date(); const y=d.getMonth()>=3?d.getFullYear():d.getFullYear()-1; return `FY ${y}–${(y+1)%100}`; }

// Financial year helpers: FY string "2026-2027" -> the as-of date to query.
function currentFY(){ const d=new Date(); const y=d.getMonth()>=3?d.getFullYear():d.getFullYear()-1; return `${y}-${y+1}`; }
function fyAsOf(fy){
  const parts=(fy||"").split("-").map(Number); if(parts.length!==2||isNaN(parts[1])) return today();
  const start=new Date(parts[0],3,1), end=new Date(parts[1],2,31), now=new Date();
  return (now>=start && now<=end) ? today() : `${parts[1]}-03-31`;   // current FY -> today, else year-end
}

function App(){
  const [page,setPage]=useState("owner"); const [me,setMe]=useState(null);
  const [notif,setNotif]=useState(0);
  // Financial-year context: options come from real accounting periods; the
  // selection drives the as-of date applied to dashboard/report queries.
  const [fys,setFys]=useState([]); const [fy,setFy]=useState(()=>{ try{return localStorage.getItem("erp_fy")||currentFY();}catch(e){return currentFY();} });
  const [refreshKey,setRefreshKey]=useState(0);
  // Company/factory scope: options come from the user's authorized memberships.
  const [ctx,setCtx]=useState(null);
  const [companyId,setCompanyId]=useState(()=>getScope(COMPANY_KEY));
  const [factoryId,setFactoryId]=useState(()=>getScope(FACTORY_KEY));
  const loadCtx=useCallback((recover)=>{ api("/api/me/context").then(c=>{ setCtx(c);
    if(c && c.active){ if(c.active.company_id) setCompanyId(c.active.company_id); }
  }).catch(()=>{ // stale scope (e.g. membership changed) -> clear once and retry
    if(recover!==false){ setScope(COMPANY_KEY,""); setScope(FACTORY_KEY,""); setCompanyId(""); setFactoryId("");
      api("/api/me/context").then(setCtx).catch(()=>{}); }
  }); },[]);
  useEffect(()=>{ loadCtx(); },[loadCtx]);
  useEffect(()=>{ api("/api/admin/periods").then(ps=>{
    const years=[...new Set((ps||[]).map(p=>p.fiscal_year).filter(Boolean))].sort().reverse();
    if(!years.includes(currentFY())) years.unshift(currentFY());
    setFys(years);
  }).catch(()=>setFys([currentFY()])); },[refreshKey]);
  const pickFy=v=>{ setFy(v); try{localStorage.setItem("erp_fy",v);}catch(e){} setRefreshKey(k=>k+1); };
  const pickCompany=v=>{ setScope(COMPANY_KEY,v); setCompanyId(v);
    setScope(FACTORY_KEY,""); setFactoryId("");   // factory belongs to a company
    setRefreshKey(k=>k+1); loadCtx(false); };
  const pickFactory=v=>{ setScope(FACTORY_KEY,v); setFactoryId(v); setRefreshKey(k=>k+1); };
  const activeCompany = (ctx && ctx.companies||[]).find(c=>c.id===(companyId||(ctx&&ctx.active&&ctx.active.company_id)));
  const factories = (activeCompany && activeCompany.factories) || [];
  const asOf=fyAsOf(fy);
  const loadNotif=useCallback(()=>api("/api/notifications/unread-count").then(r=>setNotif(r.count||0)).catch(()=>{}),[]);
  useEffect(()=>{ api("/api/auth/me").then(setMe).catch(()=>{}); loadNotif();
    const t=setInterval(loadNotif,30000); return ()=>clearInterval(t); },[loadNotif]);
  const [drawer,setDrawer]=useState(false);   // mobile nav drawer open
  const go=id=>{ setPage(id); setDrawer(false); };
  const Cur=PAGES[page].c;
  const navBtn=(id,label)=> <button className={page===id?"active":""} onClick={()=>go(id)}>
    <span className="nav-ico">{NAV_ICON[id]||"•"}</span>{label}</button>;
  return (<div className={"app"+(drawer?" drawer-open":"")}>
    <div className="scrim" onClick={()=>setDrawer(false)}></div>
    <aside className="sidebar">
      <div className="brand">◆ Textile ERP<small>Knit · Dye · Finish · Grow</small></div>
      <nav className="nav">
        <div className="group">Dashboards</div>
        {navBtn("group","Group")}{navBtn("owner","Owner")}{navBtn("factory","Factory")}
        {navBtn("accounts","Accounts")}{navBtn("chemical","Chemical")}
        <div className="group">Transactions</div>
        {navBtn("bom","Fabric BOM")}{navBtn("prodcost","Production Cost")}
        {navBtn("purchaseorders","Purchase Orders")}{navBtn("purchase","Purchase (GRN)")}{navBtn("knitting","Knitting")}
        {navBtn("batching","Batching")}{navBtn("pipeline","Sales Pipeline")}{navBtn("salesorders","Sales Orders")}{navBtn("sales","Sales Invoice")}{navBtn("dyeing","Dyeing & Dispensing")}{navBtn("jobwork","Job Work")}{navBtn("vouchers","Vouchers")}
        {navBtn("inventoryops","Inventory Ops")}{navBtn("stock","Stock on Hand")}{navBtn("stores","Stores & Spares")}
        <div className="group">Masters</div>
        {navBtn("masters","Master Data")}{navBtn("coa","Chart of Accounts")}{navBtn("customers","Customers")}{navBtn("suppliers","Suppliers")}{navBtn("warehouses","Warehouses")}
        <div className="group">Quality</div>{navBtn("quality","Quality")}
        <div className="group">Assets</div>{navBtn("assets","Fixed Assets")}
        <div className="group">Payroll</div>{navBtn("payroll","Payroll")}
        <div className="group">Costing</div>{navBtn("costing","Costing")}
        <div className="group">Devices</div>{navBtn("devices","Devices & Labels")}
        <div className="group">Reports</div>{navBtn("reports","Reports")}
        <div className="group">Admin</div>{navBtn("approvals","Approvals")}{navBtn("acctops","GST Adj / Year-End")}{navBtn("admin","Administration")}{navBtn("tenancy","Group & Tenancy")}
        <div className="group">Session</div>
        <button onClick={()=>{setToken(null); setScope(COMPANY_KEY,""); setScope(FACTORY_KEY,""); location.reload();}}>Log out</button>
      </nav>
    </aside>
    <div className="main">
      <div className="topbar">
        <button className="hamburger" aria-label="Menu" onClick={()=>setDrawer(v=>!v)}>☰</button>
        <h1>{PAGES[page].t}</h1>
        <div className="who-wrap">
          <button className="bell" title="Notifications" onClick={()=>{go("notifications"); loadNotif();}}>
            🔔{notif>0 && <span className="bell-dot">{notif>99?"99+":notif}</span>}</button>
          <button className="refresh-btn" title="Refresh data" onClick={()=>setRefreshKey(k=>k+1)}>⟳</button>
          {ctx && (ctx.companies||[]).length>1 &&
            <select className="fy-select" title="Company" value={companyId||(ctx.active&&ctx.active.company_id)||""}
                    onChange={e=>pickCompany(e.target.value)}>
              {ctx.companies.map(c=><option key={c.id} value={c.id}>{c.code}</option>)}
            </select>}
          {factories.length>0 &&
            <select className="fy-select" title="Factory" value={factoryId} onChange={e=>pickFactory(e.target.value)}>
              <option value="">All factories</option>
              {factories.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}
            </select>}
          <select className="fy-select" title="Financial year" value={fy} onChange={e=>pickFy(e.target.value)}>
            {fys.map(y=>{ const [a,b]=y.split("-"); return <option key={y} value={y}>FY {a}–{(b||"").slice(2)}</option>; })}
          </select>
          <div className="who">{me? (me.full_name||me.username) : ""}{me&&me.company?" · "+me.company:""}</div>
        </div></div>
      <div className="content">{
        page==="notifications"? <Notifications onChange={loadNotif}/>
        : page==="group"? <GroupDash refreshKey={refreshKey}/>
        : page==="owner"? <OwnerDash onNav={go} asOf={asOf} refreshKey={refreshKey}/>
        : page==="factory"? <FactoryDash onNav={go} asOf={asOf} refreshKey={refreshKey}/>
        : page==="accounts"? <AccountsDash onNav={go} asOf={asOf} refreshKey={refreshKey}/>
        : page==="chemical"? <ChemicalDash onNav={go} asOf={asOf} refreshKey={refreshKey}/>
        : <Cur/>}</div>
    </div>
  </div>);
}

function Root(){
  const [authed,setAuthed]=useState(!!getToken());
  return authed ? <App/> : <Login onLogin={()=>setAuthed(true)} />;
}
ReactDOM.createRoot(document.getElementById("root")).render(<Root/>);
