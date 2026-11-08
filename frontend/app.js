// AUTO-GENERATED from app.src.jsx by build-ui.sh — do not edit directly.
// Source JSX for the ERP SPA. Precompiled to app.js (see build-ui.sh).
const {
  useState,
  useEffect,
  useCallback
} = React;
const R = window.Recharts || null;

// ---- api helper ----------------------------------------------------------
const TOKEN_KEY = "erp_token";
function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch (e) {
    return null;
  }
}
function setToken(t) {
  try {
    t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY);
  } catch (e) {}
}
async function api(path, {
  method,
  body,
  form,
  blob
} = {}) {
  if (!method) method = form || body !== undefined ? "POST" : "GET";
  const headers = {};
  const tok = getToken();
  if (tok) headers["Authorization"] = "Bearer " + tok;
  let payload;
  if (form) {
    payload = new URLSearchParams(form);
    headers["Content-Type"] = "application/x-www-form-urlencoded";
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, {
    method,
    headers,
    body: payload
  });
  if (res.status === 401) {
    setToken(null);
    location.reload();
    throw new Error("Unauthorized");
  }
  if (blob) {
    if (!res.ok) throw new Error("Download failed");
    return await res.blob();
  }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(data && data.detail || "Error " + res.status);
  return data;
}
const inr = n => Number(n || 0).toLocaleString("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0
});
const inr2 = n => Number(n || 0).toLocaleString("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2
});

// ---- small components -----------------------------------------------------
function Kpi({
  label,
  value,
  cls
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "label"
  }, label), /*#__PURE__*/React.createElement("div", {
    className: "value " + (cls || "")
  }, value));
}
function Panel({
  title,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "panel"
  }, title && /*#__PURE__*/React.createElement("h3", null, title), children);
}

// ---- login ----------------------------------------------------------------
function Login({
  onLogin
}) {
  const [u, setU] = useState("admin");
  const [p, setP] = useState("admin123");
  const [err, setErr] = useState("");
  const submit = async e => {
    e.preventDefault();
    setErr("");
    try {
      const r = await api("/api/auth/login", {
        form: {
          username: u,
          password: p
        }
      });
      setToken(r.access_token);
      onLogin();
    } catch (ex) {
      setErr("Invalid username or password");
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "login"
  }, /*#__PURE__*/React.createElement("form", {
    className: "box",
    onSubmit: submit
  }, /*#__PURE__*/React.createElement("h2", null, "Textile ERP"), /*#__PURE__*/React.createElement("p", null, "Integrated Knitting & Dyeing ERP"), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Username"), /*#__PURE__*/React.createElement("input", {
    value: u,
    onChange: e => setU(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Password"), /*#__PURE__*/React.createElement("input", {
    type: "password",
    value: p,
    onChange: e => setP(e.target.value)
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    style: {
      width: "100%"
    }
  }, "Sign in"), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), /*#__PURE__*/React.createElement("p", {
    className: "muted",
    style: {
      marginTop: 16,
      fontSize: 12
    }
  }, "Demo: admin / admin123")));
}

// ---- data hook ------------------------------------------------------------
function useData(path) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let on = true;
    api(path).then(r => on && setD(r)).catch(e => on && setErr(e.message));
    return () => {
      on = false;
    };
  }, [path]);
  return [d, err];
}
// loads a lookup list once; returns [] until ready
function useList(path) {
  const [d, setD] = useState([]);
  useEffect(() => {
    let on = true;
    api(path).then(r => on && setD(r || [])).catch(() => {});
    return () => {
      on = false;
    };
  }, [path]);
  return d;
}
function Opts({
  items,
  value,
  onChange,
  labelKey = "label",
  valueKey = "id",
  placeholder = "Select…"
}) {
  return /*#__PURE__*/React.createElement("select", {
    value: value,
    onChange: onChange
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, placeholder), items.map(o => /*#__PURE__*/React.createElement("option", {
    key: o[valueKey],
    value: o[valueKey]
  }, o[labelKey] || o.name || o.code)));
}
function Result({
  r
}) {
  return r ? /*#__PURE__*/React.createElement("div", {
    className: "panel",
    style: {
      borderColor: "#bbf7d0",
      background: "#f0fdf4"
    }
  }, /*#__PURE__*/React.createElement("b", null, "Posted."), " ", Object.entries(r).map(([k, v]) => /*#__PURE__*/React.createElement("span", {
    key: k,
    style: {
      marginRight: 16
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, k, ":"), " ", String(v)))) : null;
}
const genNo = pfx => pfx + "-" + Date.now().toString().slice(-8);
function OwnerDash() {
  const [d, err] = useData("/api/dashboard/owner");
  if (err) return /*#__PURE__*/React.createElement(Panel, null, err);
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, "Loading\u2026");
  const np = Number(d.net_profit);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, /*#__PURE__*/React.createElement(Kpi, {
    label: "Revenue (YTD)",
    value: inr(d.revenue)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Gross Profit",
    value: inr(d.gross_profit)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Net Profit",
    value: inr(d.net_profit),
    cls: np >= 0 ? "pos" : "neg"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Cash & Bank",
    value: inr(d.cash_and_bank)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Receivables",
    value: inr(d.receivables)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Payables",
    value: inr(d.payables)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Inventory Value",
    value: inr(d.inventory_value)
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Sales trend (last 6 months)"
  }, R ? /*#__PURE__*/React.createElement("div", {
    className: "chartbox"
  }, /*#__PURE__*/React.createElement(R.ResponsiveContainer, null, /*#__PURE__*/React.createElement(R.LineChart, {
    data: (d.sales_trend || []).map(p => ({
      month: p.month,
      amount: Number(p.amount)
    })),
    margin: {
      top: 10,
      right: 20,
      left: 10,
      bottom: 0
    }
  }, /*#__PURE__*/React.createElement(R.CartesianGrid, {
    strokeDasharray: "3 3",
    stroke: "#eef2f7"
  }), /*#__PURE__*/React.createElement(R.XAxis, {
    dataKey: "month",
    fontSize: 12
  }), /*#__PURE__*/React.createElement(R.YAxis, {
    fontSize: 12,
    tickFormatter: v => v / 1000 + "k"
  }), /*#__PURE__*/React.createElement(R.Tooltip, {
    formatter: v => inr(v)
  }), /*#__PURE__*/React.createElement(R.Line, {
    type: "monotone",
    dataKey: "amount",
    stroke: "#2563eb",
    strokeWidth: 2,
    dot: {
      r: 3
    }
  })))) : /*#__PURE__*/React.createElement(SalesTable, {
    rows: d.sales_trend
  })));
}
function SalesTable({
  rows
}) {
  return /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Month"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Sales"))), /*#__PURE__*/React.createElement("tbody", null, (rows || []).map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, r.month), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.amount))))));
}
function FactoryDash() {
  const [d, err] = useData("/api/dashboard/factory");
  if (err) return /*#__PURE__*/React.createElement(Panel, null, err);
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, "Loading\u2026");
  return /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, /*#__PURE__*/React.createElement(Kpi, {
    label: "Knitting output (kg)",
    value: inr2(d.knitting_output_kg)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Dyeing output (kg)",
    value: inr2(d.dyeing_output_kg)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Active batches",
    value: d.active_batches
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Chemical consumption",
    value: inr(d.chemical_consumption_cost)
  }));
}
function AccountsDash() {
  const [d, err] = useData("/api/dashboard/accounts");
  if (err) return /*#__PURE__*/React.createElement(Panel, null, err);
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, "Loading\u2026");
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, /*#__PURE__*/React.createElement(Kpi, {
    label: "Today's receipts",
    value: inr(d.today_receipts)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Today's payments",
    value: inr(d.today_payments)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Debtors outstanding",
    value: inr(d.debtors_total)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Creditors outstanding",
    value: inr(d.creditors_total)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "GST net payable",
    value: inr(d.gst_net_payable)
  }), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "label"
  }, "Trial balance"), /*#__PURE__*/React.createElement("div", {
    className: "value"
  }, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (d.trial_balance_balanced ? "ok" : "bad")
  }, d.trial_balance_balanced ? "Balanced" : "Out of balance")))), /*#__PURE__*/React.createElement(Panel, {
    title: "Bank balances"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Account"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Balance"))), /*#__PURE__*/React.createElement("tbody", null, (d.bank_balances || []).map((b, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, b.name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(b.balance))))))));
}
function ChemicalDash() {
  const [d, err] = useData("/api/dashboard/chemical");
  if (err) return /*#__PURE__*/React.createElement(Panel, null, err);
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, "Loading\u2026");
  const bars = (d.top_consumption || []).map(r => ({
    material: r.material,
    cost: Number(r.cost)
  }));
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, /*#__PURE__*/React.createElement(Kpi, {
    label: "Dye cost",
    value: inr(d.dye_cost)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Chemical cost",
    value: inr(d.chemical_cost)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Total consumption",
    value: inr(d.total_consumption_cost)
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Dispensing errors",
    value: d.dispensing_errors,
    cls: d.dispensing_errors ? "neg" : ""
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Top consumption by material"
  }, R ? /*#__PURE__*/React.createElement("div", {
    className: "chartbox"
  }, /*#__PURE__*/React.createElement(R.ResponsiveContainer, null, /*#__PURE__*/React.createElement(R.BarChart, {
    data: bars,
    margin: {
      top: 10,
      right: 20,
      left: 10,
      bottom: 0
    }
  }, /*#__PURE__*/React.createElement(R.CartesianGrid, {
    strokeDasharray: "3 3",
    stroke: "#eef2f7"
  }), /*#__PURE__*/React.createElement(R.XAxis, {
    dataKey: "material",
    fontSize: 12
  }), /*#__PURE__*/React.createElement(R.YAxis, {
    fontSize: 12
  }), /*#__PURE__*/React.createElement(R.Tooltip, {
    formatter: v => inr(v)
  }), /*#__PURE__*/React.createElement(R.Bar, {
    dataKey: "cost",
    fill: "#2563eb",
    radius: [4, 4, 0, 0]
  })))) : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("tbody", null, bars.map((b, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, b.material), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(b.cost))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Stock alerts"
  }, (d.stock_alerts || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No chemicals below reorder level.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Name"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "On hand"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Reorder"))), /*#__PURE__*/React.createElement("tbody", null, d.stock_alerts.map((a, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, a.code), /*#__PURE__*/React.createElement("td", null, a.name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(a.on_hand)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(a.reorder_level))))))));
}

// ---- masters: warehouses --------------------------------------------------
function Warehouses() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  const [f, setF] = useState({
    code: "",
    name: "",
    kind: "STORE"
  });
  const load = useCallback(() => api("/api/masters/warehouses").then(setRows).catch(e => setErr(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);
  const add = async e => {
    e.preventDefault();
    setErr("");
    try {
      await api("/api/masters/warehouses", {
        method: "POST",
        body: f
      });
      setF({
        code: "",
        name: "",
        kind: "STORE"
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Add warehouse"
  }, /*#__PURE__*/React.createElement("form", {
    className: "row",
    onSubmit: add
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Code"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: f.code,
    onChange: e => setF({
      ...f,
      code: e.target.value
    }),
    placeholder: "WH-XYZ"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: f.name,
    onChange: e => setF({
      ...f,
      name: e.target.value
    }),
    placeholder: "Store name"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Kind"), /*#__PURE__*/React.createElement("select", {
    value: f.kind,
    onChange: e => setF({
      ...f,
      kind: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", null, "STORE"), /*#__PURE__*/React.createElement("option", null, "GREIGE"), /*#__PURE__*/React.createElement("option", null, "FG"), /*#__PURE__*/React.createElement("option", null, "DISPENSING_TANK"))), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Add warehouse")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Panel, {
    title: "Warehouses"
  }, !rows ? "Loading…" : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Name"), /*#__PURE__*/React.createElement("th", null, "Kind"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(w => /*#__PURE__*/React.createElement("tr", {
    key: w.id
  }, /*#__PURE__*/React.createElement("td", null, w.code), /*#__PURE__*/React.createElement("td", null, w.name), /*#__PURE__*/React.createElement("td", null, w.kind), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (w.is_active ? "ok" : "bad")
  }, w.is_active ? "Active" : "Inactive"))))))));
}

// ---- reports (authenticated downloads) ------------------------------------
const REPORTS = [["trial-balance", "Trial Balance"], ["profit-and-loss", "Profit & Loss"], ["balance-sheet", "Balance Sheet"], ["cash-flow", "Cash Flow"], ["debtors-ageing", "Debtors Ageing"], ["creditors-ageing", "Creditors Ageing"], ["gst-sales-register", "GST Sales Register"], ["gst-purchase-register", "GST Purchase Register"], ["gstr3b", "GSTR-3B"], ["stock-reconciliation", "Stock vs GL Reconciliation"]];
function Reports() {
  const [fmt, setFmt] = useState("xlsx");
  const [busy, setBusy] = useState("");
  const dl = async rep => {
    setBusy(rep);
    try {
      const blob = await api(`/api/reports/export/${rep}?format=${fmt}`, {
        blob: true
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${rep}.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy("");
    }
  };
  return /*#__PURE__*/React.createElement(Panel, {
    title: "Download reports"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Format"), /*#__PURE__*/React.createElement("select", {
    value: fmt,
    onChange: e => setFmt(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "xlsx"
  }, "Excel"), /*#__PURE__*/React.createElement("option", {
    value: "pdf"
  }, "PDF")))), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Report"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, REPORTS.map(([id, label]) => /*#__PURE__*/React.createElement("tr", {
    key: id
  }, /*#__PURE__*/React.createElement("td", null, label), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    disabled: busy === id,
    onClick: () => dl(id)
  }, busy === id ? "…" : "Download")))))));
}

// ---- quality --------------------------------------------------------------
const RESULT_CLS = {
  ACCEPT: "ok",
  REWORK: "warn",
  REJECT: "bad"
};
function Quality() {
  const [sum, setSum] = useState(null);
  const [insp, setInsp] = useState([]);
  const [comp, setComp] = useState([]);
  const [err, setErr] = useState("");
  const [f, setF] = useState({
    delta_e: "",
    shade_match: true,
    inspected_qty: "",
    rejection_qty: "",
    colour_fastness: ""
  });
  const load = useCallback(() => {
    api("/api/quality/summary").then(setSum).catch(e => setErr(e.message));
    api("/api/quality/inspections").then(setInsp).catch(() => {});
    api("/api/quality/complaints").then(setComp).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const add = async e => {
    e.preventDefault();
    setErr("");
    const body = {
      inspection_date: new Date().toISOString().slice(0, 10),
      delta_e: f.delta_e || null,
      shade_match: f.shade_match,
      inspected_qty: f.inspected_qty || null,
      rejection_qty: f.rejection_qty || null,
      colour_fastness: f.colour_fastness || null
    };
    try {
      await api("/api/quality/inspections", {
        method: "POST",
        body
      });
      setF({
        delta_e: "",
        shade_match: true,
        inspected_qty: "",
        rejection_qty: "",
        colour_fastness: ""
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, /*#__PURE__*/React.createElement(Kpi, {
    label: "Inspections",
    value: sum ? sum.inspections : "…"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Accepted",
    value: sum ? sum.accepted : "…",
    cls: "pos"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Rework",
    value: sum ? sum.rework : "…"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Rejected",
    value: sum ? sum.rejected : "…",
    cls: sum && sum.rejected ? "neg" : ""
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Rejection rate",
    value: sum ? Number(sum.rejection_rate_pct).toFixed(1) + "%" : "…"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Open complaints",
    value: sum ? sum.open_complaints : "…",
    cls: sum && sum.open_complaints ? "neg" : ""
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Record inspection"
  }, /*#__PURE__*/React.createElement("form", {
    className: "row",
    onSubmit: add
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Delta E"), /*#__PURE__*/React.createElement("input", {
    value: f.delta_e,
    onChange: e => setF({
      ...f,
      delta_e: e.target.value
    }),
    placeholder: "0.8"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Inspected qty"), /*#__PURE__*/React.createElement("input", {
    value: f.inspected_qty,
    onChange: e => setF({
      ...f,
      inspected_qty: e.target.value
    }),
    placeholder: "100"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Rejection qty"), /*#__PURE__*/React.createElement("input", {
    value: f.rejection_qty,
    onChange: e => setF({
      ...f,
      rejection_qty: e.target.value
    }),
    placeholder: "0"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fastness"), /*#__PURE__*/React.createElement("input", {
    value: f.colour_fastness,
    onChange: e => setF({
      ...f,
      colour_fastness: e.target.value
    }),
    placeholder: "4-5"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Shade match"), /*#__PURE__*/React.createElement("select", {
    value: f.shade_match ? "yes" : "no",
    onChange: e => setF({
      ...f,
      shade_match: e.target.value === "yes"
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "yes"
  }, "Yes"), /*#__PURE__*/React.createElement("option", {
    value: "no"
  }, "No"))), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Record (auto-grades result)")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent inspections"
  }, insp.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No inspections yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "No."), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Delta E"), /*#__PURE__*/React.createElement("th", null, "Fastness"), /*#__PURE__*/React.createElement("th", null, "Result"))), /*#__PURE__*/React.createElement("tbody", null, insp.slice(0, 15).map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, r.number), /*#__PURE__*/React.createElement("td", null, r.inspection_date), /*#__PURE__*/React.createElement("td", null, r.delta_e || "-"), /*#__PURE__*/React.createElement("td", null, r.colour_fastness || "-"), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (RESULT_CLS[r.result] || "")
  }, r.result))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Customer complaints"
  }, comp.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No complaints.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "No."), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Severity"), /*#__PURE__*/React.createElement("th", null, "Status"), /*#__PURE__*/React.createElement("th", null, "Description"))), /*#__PURE__*/React.createElement("tbody", null, comp.slice(0, 15).map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, r.number), /*#__PURE__*/React.createElement("td", null, r.complaint_date), /*#__PURE__*/React.createElement("td", null, r.severity), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (r.status === "OPEN" ? "warn" : "ok")
  }, r.status)), /*#__PURE__*/React.createElement("td", null, r.description)))))));
}

// ---- masters: parties -----------------------------------------------------
function PartyPage({
  kind
}) {
  // kind: "customers" | "suppliers"
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  const [f, setF] = useState({
    code: "",
    name: "",
    gstin: "",
    state_code: ""
  });
  const load = useCallback(() => api(`/api/masters/${kind}`).then(setRows).catch(e => setErr(e.message)), [kind]);
  useEffect(() => {
    load();
  }, [load]);
  const add = async e => {
    e.preventDefault();
    setErr("");
    try {
      await api(`/api/masters/${kind}`, {
        method: "POST",
        body: f
      });
      setF({
        code: "",
        name: "",
        gstin: "",
        state_code: ""
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const title = kind === "customers" ? "customer" : "supplier";
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Add " + title
  }, /*#__PURE__*/React.createElement("form", {
    className: "row",
    onSubmit: add
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Code"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: f.code,
    onChange: e => setF({
      ...f,
      code: e.target.value
    }),
    placeholder: kind === "customers" ? "CUST-01" : "SUP-01"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: f.name,
    onChange: e => setF({
      ...f,
      name: e.target.value
    }),
    placeholder: "Legal name"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "GSTIN"), /*#__PURE__*/React.createElement("input", {
    value: f.gstin,
    onChange: e => setF({
      ...f,
      gstin: e.target.value
    }),
    placeholder: "03ABCDE1234F1Z5"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "State code"), /*#__PURE__*/React.createElement("input", {
    value: f.state_code,
    onChange: e => setF({
      ...f,
      state_code: e.target.value
    }),
    placeholder: "03",
    style: {
      width: 70
    }
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Add")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Panel, {
    title: title.charAt(0).toUpperCase() + title.slice(1) + "s"
  }, !rows ? "Loading…" : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Name"), /*#__PURE__*/React.createElement("th", null, "GSTIN"), /*#__PURE__*/React.createElement("th", null, "State"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, r.code), /*#__PURE__*/React.createElement("td", null, r.name), /*#__PURE__*/React.createElement("td", null, r.gstin || "-"), /*#__PURE__*/React.createElement("td", null, r.state_code || "-")))))));
}

// ---- transactions: purchase (GRN) -----------------------------------------
function Purchase() {
  const suppliers = useList("/api/masters/suppliers");
  const chemicals = useList("/api/masters/chemicals");
  const warehouses = useList("/api/masters/warehouses");
  const taxes = useList("/api/masters/tax-codes");
  const [hdr, setHdr] = useState({
    supplier_id: "",
    number: genNo("GRN"),
    grn_date: new Date().toISOString().slice(0, 10)
  });
  const [lines, setLines] = useState([{
    chemical_id: "",
    warehouse_id: "",
    qty: "",
    unit_cost: "",
    tax_code_id: ""
  }]);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const setLine = (i, k, v) => setLines(ls => ls.map((l, j) => j === i ? {
    ...l,
    [k]: v
  } : l));
  const submit = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    try {
      const body = {
        ...hdr,
        lines: lines.map(l => ({
          chemical_id: l.chemical_id,
          warehouse_id: l.warehouse_id || null,
          qty: l.qty,
          unit_cost: l.unit_cost,
          tax_code_id: l.tax_code_id || null
        }))
      };
      const r = await api("/api/procurement/grn", {
        method: "POST",
        body
      });
      setRes(r);
      setHdr({
        ...hdr,
        number: genNo("GRN")
      });
      setLines([{
        chemical_id: "",
        warehouse_id: "",
        qty: "",
        unit_cost: "",
        tax_code_id: ""
      }]);
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement(Panel, {
    title: "Goods Receipt Note (chemical / dye purchase)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: submit
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Supplier"), /*#__PURE__*/React.createElement(Opts, {
    items: suppliers,
    value: hdr.supplier_id,
    onChange: e => setHdr({
      ...hdr,
      supplier_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "GRN number"), /*#__PURE__*/React.createElement("input", {
    value: hdr.number,
    onChange: e => setHdr({
      ...hdr,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Date"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: hdr.grn_date,
    onChange: e => setHdr({
      ...hdr,
      grn_date: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Chemical"), /*#__PURE__*/React.createElement("th", null, "Warehouse"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Qty"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Unit cost"), /*#__PURE__*/React.createElement("th", null, "Tax"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, lines.map((l, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(Opts, {
    items: chemicals,
    value: l.chemical_id,
    onChange: e => setLine(i, "chemical_id", e.target.value)
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(Opts, {
    items: warehouses,
    value: l.warehouse_id,
    onChange: e => setLine(i, "warehouse_id", e.target.value),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 90
    },
    value: l.qty,
    onChange: e => setLine(i, "qty", e.target.value)
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 100
    },
    value: l.unit_cost,
    onChange: e => setLine(i, "unit_cost", e.target.value)
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: l.tax_code_id,
    onChange: e => setLine(i, "tax_code_id", e.target.value),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("td", null, lines.length > 1 && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setLines(ls => ls.filter((_, j) => j !== i))
  }, "\u2715")))))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setLines(ls => [...ls, {
      chemical_id: "",
      warehouse_id: "",
      qty: "",
      unit_cost: "",
      tax_code_id: ""
    }])
  }, "+ Add line"), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post GRN")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  }));
}

// ---- transactions: knitting production ------------------------------------
function Knitting() {
  const machines = useList("/api/masters/machines?machine_type=KNITTING");
  const yarns = useList("/api/masters/yarns");
  const fabrics = useList("/api/masters/fabric-types");
  const warehouses = useList("/api/masters/warehouses");
  const orders = useList("/api/masters/sales-orders");
  const suppliers = useList("/api/masters/suppliers");
  const [lots, setLots] = useState([]);
  const loadLots = useCallback(() => api("/api/masters/yarn-lots").then(setLots).catch(() => {}), []);
  useEffect(() => {
    loadLots();
  }, [loadLots]);
  const [h, setH] = useState({
    number: genNo("KP"),
    machine_id: "",
    yarn_id: "",
    yarn_lot_id: "",
    fabric_type_id: "",
    sales_order_id: "",
    production_date: new Date().toISOString().slice(0, 10),
    produced_kg: "",
    yarn_consumed_kg: "",
    wastage_kg: "0"
  });
  const [rolls, setRolls] = useState([{
    roll_no: "",
    weight_kg: "",
    warehouse_id: ""
  }]);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [lf, setLf] = useState({
    yarn_id: "",
    internal_lot_no: "",
    qty: "",
    unit_cost: "",
    supplier_id: ""
  });
  const [lres, setLres] = useState(null);
  const [lerr, setLerr] = useState("");
  const setRoll = (i, k, v) => setRolls(rs => rs.map((r, j) => j === i ? {
    ...r,
    [k]: v
  } : r));
  const lotOpts = lots.filter(l => !h.yarn_id || l.yarn_id === h.yarn_id).map(l => ({
    id: l.id,
    label: l.label
  }));
  const receiveLot = async e => {
    e.preventDefault();
    setLerr("");
    setLres(null);
    try {
      const r = await api("/api/masters/yarn-lots", {
        method: "POST",
        body: {
          yarn_id: lf.yarn_id,
          internal_lot_no: lf.internal_lot_no,
          qty: lf.qty,
          unit_cost: lf.unit_cost,
          supplier_id: lf.supplier_id || null
        }
      });
      setLres(r);
      setLf({
        yarn_id: "",
        internal_lot_no: "",
        qty: "",
        unit_cost: "",
        supplier_id: ""
      });
      loadLots();
    } catch (ex) {
      setLerr(ex.message);
    }
  };
  const submit = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    try {
      const body = {
        ...h,
        sales_order_id: h.sales_order_id || null,
        fabric_type_id: h.fabric_type_id || null,
        yarn_lot_id: h.yarn_lot_id || null,
        rolls: rolls.map(r => ({
          roll_no: r.roll_no,
          weight_kg: r.weight_kg,
          warehouse_id: r.warehouse_id || null
        }))
      };
      const r = await api("/api/knitting/production", {
        method: "POST",
        body
      });
      setRes(r);
      setH({
        ...h,
        number: genNo("KP"),
        yarn_lot_id: ""
      });
      setRolls([{
        roll_no: "",
        weight_kg: "",
        warehouse_id: ""
      }]);
      loadLots();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Receive yarn lot"
  }, /*#__PURE__*/React.createElement("form", {
    className: "row",
    onSubmit: receiveLot
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Yarn"), /*#__PURE__*/React.createElement(Opts, {
    items: yarns,
    value: lf.yarn_id,
    onChange: e => setLf({
      ...lf,
      yarn_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Lot no."), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: lf.internal_lot_no,
    onChange: e => setLf({
      ...lf,
      internal_lot_no: e.target.value
    }),
    placeholder: "YLOT-001"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Supplier"), /*#__PURE__*/React.createElement(Opts, {
    items: suppliers,
    value: lf.supplier_id,
    onChange: e => setLf({
      ...lf,
      supplier_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty (kg)"), /*#__PURE__*/React.createElement("input", {
    style: {
      width: 90
    },
    value: lf.qty,
    onChange: e => setLf({
      ...lf,
      qty: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Unit cost"), /*#__PURE__*/React.createElement("input", {
    style: {
      width: 90
    },
    value: lf.unit_cost,
    onChange: e => setLf({
      ...lf,
      unit_cost: e.target.value
    })
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Receive")), lerr && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, lerr), /*#__PURE__*/React.createElement(Result, {
    r: lres
  }), lots.length > 0 && /*#__PURE__*/React.createElement("table", {
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Lot"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "On hand (kg)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Unit cost"))), /*#__PURE__*/React.createElement("tbody", null, lots.map(l => /*#__PURE__*/React.createElement("tr", {
    key: l.id
  }, /*#__PURE__*/React.createElement("td", null, l.internal_lot_no), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.on_hand)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.unit_cost))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Knitting production (issues yarn, creates greige rolls)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: submit
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Doc number"), /*#__PURE__*/React.createElement("input", {
    value: h.number,
    onChange: e => setH({
      ...h,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Machine"), /*#__PURE__*/React.createElement(Opts, {
    items: machines,
    value: h.machine_id,
    onChange: e => setH({
      ...h,
      machine_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Yarn"), /*#__PURE__*/React.createElement(Opts, {
    items: yarns,
    value: h.yarn_id,
    onChange: e => setH({
      ...h,
      yarn_id: e.target.value,
      yarn_lot_id: ""
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Yarn lot"), /*#__PURE__*/React.createElement(Opts, {
    items: lotOpts,
    value: h.yarn_lot_id,
    onChange: e => setH({
      ...h,
      yarn_lot_id: e.target.value
    }),
    placeholder: "FIFO (any lot)"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fabric type"), /*#__PURE__*/React.createElement(Opts, {
    items: fabrics,
    value: h.fabric_type_id,
    onChange: e => setH({
      ...h,
      fabric_type_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Sales order"), /*#__PURE__*/React.createElement(Opts, {
    items: orders,
    value: h.sales_order_id,
    onChange: e => setH({
      ...h,
      sales_order_id: e.target.value
    }),
    placeholder: "\u2014"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Date"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: h.production_date,
    onChange: e => setH({
      ...h,
      production_date: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Produced (kg)"), /*#__PURE__*/React.createElement("input", {
    value: h.produced_kg,
    onChange: e => setH({
      ...h,
      produced_kg: e.target.value
    }),
    placeholder: "250"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Yarn consumed (kg)"), /*#__PURE__*/React.createElement("input", {
    value: h.yarn_consumed_kg,
    onChange: e => setH({
      ...h,
      yarn_consumed_kg: e.target.value
    }),
    placeholder: "255"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Wastage (kg)"), /*#__PURE__*/React.createElement("input", {
    value: h.wastage_kg,
    onChange: e => setH({
      ...h,
      wastage_kg: e.target.value
    }),
    placeholder: "5"
  }))), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Roll no."), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Weight (kg)"), /*#__PURE__*/React.createElement("th", null, "Warehouse"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, rolls.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("input", {
    value: r.roll_no,
    onChange: e => setRoll(i, "roll_no", e.target.value),
    placeholder: "(auto)"
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 100
    },
    value: r.weight_kg,
    onChange: e => setRoll(i, "weight_kg", e.target.value)
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(Opts, {
    items: warehouses,
    value: r.warehouse_id,
    onChange: e => setRoll(i, "warehouse_id", e.target.value),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("td", null, rolls.length > 1 && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setRolls(rs => rs.filter((_, j) => j !== i))
  }, "\u2715")))))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setRolls(rs => [...rs, {
      roll_no: "",
      weight_kg: "",
      warehouse_id: ""
    }])
  }, "+ Add roll"), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Record production")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  })));
}
// ---- transactions: sales invoice ------------------------------------------
function Sales() {
  const customers = useList("/api/masters/customers");
  const taxes = useList("/api/masters/tax-codes");
  const [hdr, setHdr] = useState({
    customer_id: "",
    number: genNo("INV"),
    invoice_date: new Date().toISOString().slice(0, 10)
  });
  const [lines, setLines] = useState([{
    description: "",
    qty: "",
    rate: "",
    tax_code_id: ""
  }]);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const setLine = (i, k, v) => setLines(ls => ls.map((l, j) => j === i ? {
    ...l,
    [k]: v
  } : l));
  const submit = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    try {
      const body = {
        ...hdr,
        lines: lines.map(l => ({
          description: l.description,
          qty: l.qty,
          rate: l.rate,
          tax_code_id: l.tax_code_id || null
        }))
      };
      const r = await api("/api/sales/invoices", {
        method: "POST",
        body
      });
      setRes(r);
      setHdr({
        ...hdr,
        number: genNo("INV")
      });
      setLines([{
        description: "",
        qty: "",
        rate: "",
        tax_code_id: ""
      }]);
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement(Panel, {
    title: "Sales invoice (with GST)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: submit
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: hdr.customer_id,
    onChange: e => setHdr({
      ...hdr,
      customer_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Invoice number"), /*#__PURE__*/React.createElement("input", {
    value: hdr.number,
    onChange: e => setHdr({
      ...hdr,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Date"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: hdr.invoice_date,
    onChange: e => setHdr({
      ...hdr,
      invoice_date: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Description"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Qty"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Rate"), /*#__PURE__*/React.createElement("th", null, "Tax"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, lines.map((l, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 260
    },
    value: l.description,
    onChange: e => setLine(i, "description", e.target.value),
    placeholder: "Dyed fabric"
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 90
    },
    value: l.qty,
    onChange: e => setLine(i, "qty", e.target.value)
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 90
    },
    value: l.rate,
    onChange: e => setLine(i, "rate", e.target.value)
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: l.tax_code_id,
    onChange: e => setLine(i, "tax_code_id", e.target.value),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("td", null, lines.length > 1 && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setLines(ls => ls.filter((_, j) => j !== i))
  }, "\u2715")))))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setLines(ls => [...ls, {
      description: "",
      qty: "",
      rate: "",
      tax_code_id: ""
    }])
  }, "+ Add line"), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post invoice")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  }));
}

// ---- transactions: batching (assign greige rolls to a dye batch/lot) -------
function Batching() {
  const shades = useList("/api/masters/shades");
  const orders = useList("/api/masters/sales-orders");
  const [rolls, setRolls] = useState([]);
  const [batches, setBatches] = useState([]);
  const [sel, setSel] = useState({});
  const [f, setF] = useState({
    batch_code: genNo("DY"),
    shade_id: "",
    sales_order_id: ""
  });
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api("/api/rolls?kind=GREIGE&unbatched=true").then(setRolls).catch(() => {});
    api("/api/dyeing/batches").then(setBatches).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const chosen = rolls.filter(r => sel[r.id]);
  const totalKg = chosen.reduce((s, r) => s + Number(r.weight_kg || 0), 0);
  const toggle = id => setSel(s => ({
    ...s,
    [id]: !s[id]
  }));
  const create = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    if (chosen.length === 0) {
      setErr("Select at least one greige roll");
      return;
    }
    try {
      const r = await api("/api/dyeing/batches", {
        method: "POST",
        body: {
          batch_code: f.batch_code,
          shade_id: f.shade_id || null,
          sales_order_id: f.sales_order_id || null,
          roll_ids: chosen.map(r => r.id)
        }
      });
      setRes(r);
      setF({
        ...f,
        batch_code: genNo("DY")
      });
      setSel({});
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Create dye batch from greige rolls"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: create
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Batch code"), /*#__PURE__*/React.createElement("input", {
    value: f.batch_code,
    onChange: e => setF({
      ...f,
      batch_code: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Shade"), /*#__PURE__*/React.createElement(Opts, {
    items: shades,
    value: f.shade_id,
    onChange: e => setF({
      ...f,
      shade_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Sales order"), /*#__PURE__*/React.createElement(Opts, {
    items: orders,
    value: f.sales_order_id,
    onChange: e => setF({
      ...f,
      sales_order_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Selected"), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "9px 0",
      fontWeight: 700
    }
  }, chosen.length, " rolls \xB7 ", inr2(totalKg), " kg"))), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    style: {
      width: 40
    }
  }), /*#__PURE__*/React.createElement("th", null, "Roll no."), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Weight (kg)"), /*#__PURE__*/React.createElement("th", null, "Quality"))), /*#__PURE__*/React.createElement("tbody", null, rolls.length === 0 ? /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "4",
    className: "muted"
  }, "No unbatched greige rolls \u2014 record a knitting production first.")) : rolls.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!sel[r.id],
    onChange: () => toggle(r.id)
  })), /*#__PURE__*/React.createElement("td", null, r.roll_no), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.weight_kg)), /*#__PURE__*/React.createElement("td", null, r.quality_status))))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Create batch (", chosen.length, " rolls)")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent batches"
  }, batches.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "None yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Batch"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Fabric (kg)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Rolls"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, batches.map(b => /*#__PURE__*/React.createElement("tr", {
    key: b.id
  }, /*#__PURE__*/React.createElement("td", null, b.batch_code), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(b.fabric_kg)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, b.rolls), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge ok"
  }, b.status))))))));
}

// ---- transactions: dyeing (batch -> bath -> dispense -> register) ----------
function Dyeing() {
  const shades = useList("/api/masters/shades");
  const machines = useList("/api/masters/machines?machine_type=DYEING");
  const versions = useList("/api/masters/recipe-versions");
  const batches = useList("/api/dyeing/batches");
  const [batch, setBatch] = useState(null);
  const [bath, setBath] = useState(null);
  const [order, setOrder] = useState(null);
  const [disp, setDisp] = useState(null);
  const [reg, setReg] = useState(null);
  const [err, setErr] = useState("");
  const [bf, setBf] = useState({
    batch_code: genNo("DY"),
    fabric_kg: "250",
    shade_id: ""
  });
  const [hf, setHf] = useState({
    bath_code: "",
    machine_id: "",
    recipe_version_id: "",
    fabric_kg: "250"
  });
  const go = async fn => {
    setErr("");
    try {
      await fn();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const createBatch = () => go(async () => {
    const r = await api("/api/dyeing/batches", {
      method: "POST",
      body: bf
    });
    setBatch(r);
    setHf({
      ...hf,
      bath_code: r.batch_code + "-B01",
      fabric_kg: bf.fabric_kg
    });
  });
  const createBath = () => go(async () => {
    const r = await api("/api/dyeing/baths", {
      method: "POST",
      body: {
        ...hf,
        batch_id: batch.batch_id
      }
    });
    setBath(r);
    setOrder(null);
    setDisp(null);
    setReg(null);
  });
  const preview = () => go(async () => setOrder(await api(`/api/dyeing/baths/${bath.bath_id}/dose-order`)));
  const dispense = () => go(async () => {
    setDisp(await api(`/api/dyeing/baths/${bath.bath_id}/simulate-dispense`, {
      method: "POST"
    }));
    setReg(await api(`/api/dyeing/baths/${bath.bath_id}/consumption-register`));
  });
  const pickBatch = id => {
    const b = batches.find(x => x.id === id);
    if (!b) {
      setBatch(null);
      return;
    }
    setBatch({
      batch_id: b.id,
      batch_code: b.batch_code
    });
    setHf({
      ...hf,
      bath_code: b.batch_code + "-B01",
      fabric_kg: b.fabric_kg
    });
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "1 \xB7 Create or pick a dye batch"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Use an existing (batched) batch"), /*#__PURE__*/React.createElement(Opts, {
    items: batches,
    value: batch ? batch.batch_id : "",
    onChange: e => pickBatch(e.target.value),
    placeholder: "\u2014 new batch below \u2014"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Batch code"), /*#__PURE__*/React.createElement("input", {
    value: bf.batch_code,
    onChange: e => setBf({
      ...bf,
      batch_code: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fabric (kg)"), /*#__PURE__*/React.createElement("input", {
    value: bf.fabric_kg,
    onChange: e => setBf({
      ...bf,
      fabric_kg: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Shade"), /*#__PURE__*/React.createElement(Opts, {
    items: shades,
    value: bf.shade_id,
    onChange: e => setBf({
      ...bf,
      shade_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: createBatch
  }, "Create batch")), batch && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 10
    }
  }, "Batch ", batch.batch_code, " created \u2713")), batch && /*#__PURE__*/React.createElement(Panel, {
    title: "2 \xB7 Add bath (approved recipe)"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Bath code"), /*#__PURE__*/React.createElement("input", {
    value: hf.bath_code,
    onChange: e => setHf({
      ...hf,
      bath_code: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Machine"), /*#__PURE__*/React.createElement(Opts, {
    items: machines,
    value: hf.machine_id,
    onChange: e => setHf({
      ...hf,
      machine_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Recipe version"), /*#__PURE__*/React.createElement(Opts, {
    items: versions,
    value: hf.recipe_version_id,
    onChange: e => setHf({
      ...hf,
      recipe_version_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fabric (kg)"), /*#__PURE__*/React.createElement("input", {
    value: hf.fabric_kg,
    onChange: e => setHf({
      ...hf,
      fabric_kg: e.target.value
    })
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: createBath
  }, "Create bath")), bath && /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      alignSelf: "center"
    }
  }, "Bath ", bath.bath_code, " \u2713"), /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    onClick: preview
  }, "Preview dose order"), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: dispense
  }, "Simulate dispensing"))), order && /*#__PURE__*/React.createElement(Panel, {
    title: "Scaled dose order (ERP \u2192 Color Service)"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Seq"), /*#__PURE__*/React.createElement("th", null, "Material"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Target (kg)"), /*#__PURE__*/React.createElement("th", null, "Dye?"))), /*#__PURE__*/React.createElement("tbody", null, order.materials.map((m, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, m.sequence), /*#__PURE__*/React.createElement("td", null, m.material_code), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, m.target_qty), /*#__PURE__*/React.createElement("td", null, m.is_dye ? "Dye" : "Aux")))))), disp && /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, /*#__PURE__*/React.createElement(Kpi, {
    label: "Dispensed lines",
    value: disp.processed
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Skipped (replay)",
    value: disp.skipped
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Consumption cost",
    value: inr(disp.total_cost)
  })), reg && /*#__PURE__*/React.createElement(Panel, {
    title: "Bath consumption register (recipe vs actual)"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Material"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Recipe"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Actual"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Variance"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Cost"))), /*#__PURE__*/React.createElement("tbody", null, reg.rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, r.material_code), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.recipe_target_kg), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.actual_kg), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.variance_kg), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.cost))))), /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Total"), /*#__PURE__*/React.createElement("th", null), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, reg.total_actual_kg), /*#__PURE__*/React.createElement("th", null), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(reg.total_cost)))))), err && /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)));
}

// ---- costing --------------------------------------------------------------
function Costing() {
  const [cpk, errc] = useData("/api/costing/cost-per-kg");
  const custs = useList("/api/costing/customers");
  const orders = useList("/api/costing/orders");
  const machines = useList("/api/costing/machines");
  const pct = v => Number(v || 0).toFixed(1) + "%";
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, /*#__PURE__*/React.createElement(Kpi, {
    label: "Knitted cost / kg",
    value: cpk ? inr(cpk.knitted_cost_per_kg) : "…"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Dyeing cost / kg",
    value: cpk ? inr(cpk.dyeing_cost_per_kg) : "…"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Knitted output (kg)",
    value: cpk ? inr2(cpk.knitted_kg) : "…"
  }), /*#__PURE__*/React.createElement(Kpi, {
    label: "Dyed output (kg)",
    value: cpk ? inr2(cpk.dyed_kg) : "…"
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Customer profitability"
  }, custs.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No billed customers yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Customer"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Revenue"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Cost"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Gross profit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Margin"))), /*#__PURE__*/React.createElement("tbody", null, custs.map((c, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, c.customer), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(c.revenue)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(c.cost)), /*#__PURE__*/React.createElement("td", {
    className: "num",
    style: {
      color: Number(c.gross_profit) >= 0 ? "#16a34a" : "#dc2626"
    }
  }, inr2(c.gross_profit)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, pct(c.margin_pct))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Order profitability"
  }, orders.filter(o => Number(o.revenue) || Number(o.total_cost)).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No orders with activity yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Order"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Revenue"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Yarn"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Dyeing"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Total cost"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Gross profit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Margin"))), /*#__PURE__*/React.createElement("tbody", null, orders.filter(o => Number(o.revenue) || Number(o.total_cost)).map((o, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, o.order_number), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(o.revenue)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(o.yarn_cost)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(o.dyeing_cost)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(o.total_cost)), /*#__PURE__*/React.createElement("td", {
    className: "num",
    style: {
      color: Number(o.gross_profit) >= 0 ? "#16a34a" : "#dc2626"
    }
  }, inr2(o.gross_profit)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, pct(o.margin_pct))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Machine-wise cost"
  }, machines.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No machine cost yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Machine"), /*#__PURE__*/React.createElement("th", null, "Type"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Cost"))), /*#__PURE__*/React.createElement("tbody", null, machines.map((m, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, m.machine), /*#__PURE__*/React.createElement("td", null, m.machine_type), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(m.cost))))))));
}

// ---- devices & labels -----------------------------------------------------
function Devices() {
  const [summary, setSummary] = useState([]);
  const [readings, setReadings] = useState([]);
  const [rolls, setRolls] = useState([]);
  const [machines, setMachines] = useState([]);
  const [scales, setScales] = useState([]);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    api("/api/hardware/knitting-summary").then(setSummary).catch(() => {});
    api("/api/hardware/scale-readings").then(setReadings).catch(() => {});
    api("/api/rolls").then(setRolls).catch(() => {});
    api("/api/masters/machines?machine_type=KNITTING").then(setMachines).catch(() => {});
    api("/api/hardware/scales").then(setScales).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const simKnit = async code => {
    setMsg("");
    try {
      const r = await api("/api/hardware/simulate/knitting", {
        method: "POST",
        body: {
          machine_code: code,
          samples: 4
        }
      });
      setMsg(`Ingested ${r.created} samples from ${code}`);
      load();
    } catch (e) {
      setMsg(e.message);
    }
  };
  const simWeigh = async code => {
    setMsg("");
    try {
      const r = await api("/api/hardware/simulate/scale", {
        method: "POST",
        body: {
          scale_code: code,
          weight: (120 + Math.floor(Math.random() * 20)).toString(),
          reference_type: "ROLL",
          seq: Date.now() % 100000
        }
      });
      setMsg(`Captured weight on ${code}`);
      load();
    } catch (e) {
      setMsg(e.message);
    }
  };
  const label = async id => {
    try {
      const blob = await api(`/api/rolls/${id}/label`, {
        blob: true
      });
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) {
      alert(e.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Knitting machine monitoring"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, machines.map(m => /*#__PURE__*/React.createElement("button", {
    key: m.id,
    className: "btn sec",
    onClick: () => simKnit(m.code)
  }, "Simulate ", m.code))), summary.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No machine logs yet \u2014 run a simulation.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Machine"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Produced (kg)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Run (min)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Downtime (min)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Stops"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Efficiency"))), /*#__PURE__*/React.createElement("tbody", null, summary.map((s, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, s.machine), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(s.produced_kg)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.run_minutes), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.downtime_minutes), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.stops), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, Number(s.efficiency_pct).toFixed(1), "%")))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Weighing scale readings"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, scales.map(s => /*#__PURE__*/React.createElement("button", {
    key: s.id,
    className: "btn sec",
    onClick: () => simWeigh(s.code)
  }, "Capture weight \xB7 ", s.code))), readings.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No readings yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Reading id"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Weight"), /*#__PURE__*/React.createElement("th", null, "Ref"), /*#__PURE__*/React.createElement("th", null, "Stable"), /*#__PURE__*/React.createElement("th", null, "At"))), /*#__PURE__*/React.createElement("tbody", null, readings.slice(0, 10).map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, r.external_reading_id), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.weight), " ", r.unit), /*#__PURE__*/React.createElement("td", null, r.reference_type || "-"), /*#__PURE__*/React.createElement("td", null, r.stable ? "Yes" : "No"), /*#__PURE__*/React.createElement("td", null, (r.captured_at || "").slice(0, 19).replace("T", " ")))))), msg && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 10
    }
  }, msg)), /*#__PURE__*/React.createElement(Panel, {
    title: "Fabric roll labels (QR)"
  }, rolls.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No rolls yet \u2014 record a knitting production.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Roll no."), /*#__PURE__*/React.createElement("th", null, "Kind"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Weight"), /*#__PURE__*/React.createElement("th", null, "Quality"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, rolls.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, r.roll_no), /*#__PURE__*/React.createElement("td", null, r.kind), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.weight_kg), " kg"), /*#__PURE__*/React.createElement("td", null, r.quality_status), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    onClick: () => label(r.id)
  }, "Print label"))))))));
}

// ---- masters manager (generic) --------------------------------------------
const MASTER_ENTITIES = {
  "tax-codes": {
    label: "Tax codes",
    fields: [{
      n: "code",
      l: "Code"
    }, {
      n: "name",
      l: "Name"
    }, {
      n: "cgst_rate",
      l: "CGST %",
      t: "num"
    }, {
      n: "sgst_rate",
      l: "SGST %",
      t: "num"
    }, {
      n: "igst_rate",
      l: "IGST %",
      t: "num"
    }, {
      n: "hsn_sac",
      l: "HSN/SAC"
    }]
  },
  "chemicals": {
    label: "Chemicals & dyes",
    fields: [{
      n: "code",
      l: "Code"
    }, {
      n: "name",
      l: "Name"
    }, {
      n: "is_dye",
      l: "Is dye?",
      t: "bool"
    }, {
      n: "dye_class",
      l: "Dye class"
    }, {
      n: "standard_cost",
      l: "Std cost",
      t: "num"
    }, {
      n: "reorder_level",
      l: "Reorder",
      t: "num"
    }]
  },
  "machines": {
    label: "Machines",
    fields: [{
      n: "machine_type",
      l: "Type",
      t: "select",
      opts: ["KNITTING", "DYEING", "FINISHING"]
    }, {
      n: "code",
      l: "Code"
    }, {
      n: "name",
      l: "Name"
    }, {
      n: "capacity_kg",
      l: "Capacity kg",
      t: "num"
    }]
  },
  "shades": {
    label: "Shades",
    fields: [{
      n: "code",
      l: "Code"
    }, {
      n: "name",
      l: "Name"
    }]
  },
  "fabric-types": {
    label: "Fabric types",
    fields: [{
      n: "code",
      l: "Code"
    }, {
      n: "name",
      l: "Name"
    }, {
      n: "construction",
      l: "Construction"
    }, {
      n: "composition",
      l: "Composition"
    }, {
      n: "gsm",
      l: "GSM",
      t: "num"
    }, {
      n: "width_cm",
      l: "Width cm",
      t: "num"
    }]
  },
  "yarns": {
    label: "Yarns",
    fields: [{
      n: "code",
      l: "Code"
    }, {
      n: "name",
      l: "Name"
    }, {
      n: "yarn_type",
      l: "Type"
    }, {
      n: "count",
      l: "Count"
    }]
  }
};
function MastersManager() {
  const [ent, setEnt] = useState("tax-codes");
  const cfg = MASTER_ENTITIES[ent];
  const [rows, setRows] = useState([]);
  const [f, setF] = useState({});
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const load = useCallback(() => api(`/api/masters/${ent}`).then(setRows).catch(e => setErr(e.message)), [ent]);
  useEffect(() => {
    setF({});
    setErr("");
    setOk("");
    load();
  }, [ent, load]);
  const submit = async e => {
    e.preventDefault();
    setErr("");
    setOk("");
    const body = {};
    cfg.fields.forEach(fl => {
      let v = f[fl.n];
      if (fl.t === "bool") {
        body[fl.n] = !!v;
        return;
      }
      if (v !== undefined && v !== "") body[fl.n] = v;
    });
    try {
      const r = await api(`/api/masters/${ent}`, {
        method: "POST",
        body
      });
      setOk(`Created ${r.code}`);
      setF({});
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Manage master data"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Entity"), /*#__PURE__*/React.createElement("select", {
    value: ent,
    onChange: e => setEnt(e.target.value)
  }, Object.entries(MASTER_ENTITIES).map(([k, v]) => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, v.label))))), /*#__PURE__*/React.createElement("form", {
    className: "row",
    onSubmit: submit
  }, cfg.fields.map(fl => /*#__PURE__*/React.createElement("div", {
    className: "field",
    key: fl.n
  }, /*#__PURE__*/React.createElement("label", null, fl.l), fl.t === "select" ? /*#__PURE__*/React.createElement("select", {
    value: f[fl.n] || "",
    onChange: e => setF({
      ...f,
      [fl.n]: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014"), fl.opts.map(o => /*#__PURE__*/React.createElement("option", {
    key: o
  }, o))) : fl.t === "bool" ? /*#__PURE__*/React.createElement("select", {
    value: f[fl.n] ? "yes" : "no",
    onChange: e => setF({
      ...f,
      [fl.n]: e.target.value === "yes"
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "no"
  }, "No"), /*#__PURE__*/React.createElement("option", {
    value: "yes"
  }, "Yes")) : /*#__PURE__*/React.createElement("input", {
    style: {
      width: fl.t === "num" ? 90 : 150
    },
    value: f[fl.n] || "",
    onChange: e => setF({
      ...f,
      [fl.n]: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Add")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), ok && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      color: "#16a34a",
      fontSize: 12.5
    }
  }, ok)), /*#__PURE__*/React.createElement(Panel, {
    title: cfg.label
  }, rows.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "None yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Name"))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: r.id || i
  }, /*#__PURE__*/React.createElement("td", null, r.code), /*#__PURE__*/React.createElement("td", null, r.name)))))));
}

// ---- manual vouchers ------------------------------------------------------
const VTYPES = [["JV", "Journal"], ["PV", "Payment"], ["RV", "Receipt"], ["CV", "Contra"], ["PI", "Purchase"], ["SI", "Sales"], ["DN", "Debit Note"], ["CN", "Credit Note"], ["SJ", "Stock Journal"], ["PJ", "Production Journal"]];
function Vouchers() {
  const accounts = useList("/api/accounts");
  const acctOpts = accounts.filter(a => a.allow_posting).map(a => ({
    id: a.code,
    label: `${a.code} · ${a.name}`
  }));
  const [h, setH] = useState({
    voucher_type_code: "JV",
    voucher_date: new Date().toISOString().slice(0, 10),
    posting_reference: genNo("JV"),
    narration: ""
  });
  const [lines, setLines] = useState([{
    account_code: "",
    debit: "",
    credit: "",
    narration: ""
  }, {
    account_code: "",
    debit: "",
    credit: "",
    narration: ""
  }]);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [recent, setRecent] = useState([]);
  const loadRecent = useCallback(() => api("/api/vouchers").then(setRecent).catch(() => {}), []);
  useEffect(() => {
    loadRecent();
  }, [loadRecent]);
  const setLine = (i, k, v) => setLines(ls => ls.map((l, j) => j === i ? {
    ...l,
    [k]: v
  } : l));
  const num = v => Number(v || 0);
  const totDr = lines.reduce((s, l) => s + num(l.debit), 0),
    totCr = lines.reduce((s, l) => s + num(l.credit), 0);
  const balanced = Math.abs(totDr - totCr) < 0.005 && totDr > 0;
  const submit = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    try {
      const body = {
        ...h,
        lines: lines.filter(l => l.account_code && (num(l.debit) || num(l.credit))).map(l => ({
          account_code: l.account_code,
          debit: l.debit || "0",
          credit: l.credit || "0",
          narration: l.narration || null
        }))
      };
      const r = await api("/api/vouchers", {
        method: "POST",
        body
      });
      setRes({
        voucher: r.number,
        status: r.status,
        total: r.total_debit
      });
      setH({
        ...h,
        posting_reference: genNo("JV")
      });
      setLines([{
        account_code: "",
        debit: "",
        credit: "",
        narration: ""
      }, {
        account_code: "",
        debit: "",
        credit: "",
        narration: ""
      }]);
      loadRecent();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Post a voucher"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: submit
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Type"), /*#__PURE__*/React.createElement("select", {
    value: h.voucher_type_code,
    onChange: e => setH({
      ...h,
      voucher_type_code: e.target.value
    })
  }, VTYPES.map(([c, l]) => /*#__PURE__*/React.createElement("option", {
    key: c,
    value: c
  }, c, " \xB7 ", l)))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Date"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: h.voucher_date,
    onChange: e => setH({
      ...h,
      voucher_date: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Reference"), /*#__PURE__*/React.createElement("input", {
    value: h.posting_reference,
    onChange: e => setH({
      ...h,
      posting_reference: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", null, "Narration"), /*#__PURE__*/React.createElement("input", {
    value: h.narration,
    onChange: e => setH({
      ...h,
      narration: e.target.value
    }),
    placeholder: "Description"
  }))), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Account"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Debit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Credit"), /*#__PURE__*/React.createElement("th", null, "Narration"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, lines.map((l, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(Opts, {
    items: acctOpts,
    value: l.account_code,
    onChange: e => setLine(i, "account_code", e.target.value)
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 100
    },
    value: l.debit,
    onChange: e => setLine(i, "debit", e.target.value)
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 100
    },
    value: l.credit,
    onChange: e => setLine(i, "credit", e.target.value)
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("input", {
    style: {
      width: 150
    },
    value: l.narration,
    onChange: e => setLine(i, "narration", e.target.value)
  })), /*#__PURE__*/React.createElement("td", null, lines.length > 2 && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setLines(ls => ls.filter((_, j) => j !== i))
  }, "\u2715"))))), /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Totals"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(totDr)), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(totCr)), /*#__PURE__*/React.createElement("th", {
    colSpan: "2"
  }, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (balanced ? "ok" : "bad")
  }, balanced ? "Balanced" : "Dr ≠ Cr"))))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setLines(ls => [...ls, {
      account_code: "",
      debit: "",
      credit: "",
      narration: ""
    }])
  }, "+ Add line"), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post voucher")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent vouchers"
  }, recent.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "None yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Number"), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Status"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Debit"), /*#__PURE__*/React.createElement("th", null, "Narration"))), /*#__PURE__*/React.createElement("tbody", null, recent.map(v => /*#__PURE__*/React.createElement("tr", {
    key: v.id
  }, /*#__PURE__*/React.createElement("td", null, v.number), /*#__PURE__*/React.createElement("td", null, v.voucher_date), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (v.status === "POSTED" ? "ok" : v.status === "REVERSED" ? "warn" : "")
  }, v.status)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(v.total_debit)), /*#__PURE__*/React.createElement("td", null, v.narration || "-")))))));
}

// ---- administration -------------------------------------------------------
function Admin() {
  const [periods, setPeriods] = useState([]);
  const [users, setUsers] = useState([]);
  const roles = useList("/api/admin/roles");
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [nu, setNu] = useState({
    username: "",
    email: "",
    full_name: "",
    password: "",
    role_codes: []
  });
  const load = useCallback(() => {
    api("/api/admin/periods").then(setPeriods).catch(e => setErr(e.message));
    api("/api/admin/users").then(setUsers).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const setPeriod = async (id, action) => {
    setErr("");
    try {
      await api(`/api/admin/periods/${id}/${action}`, {
        method: "POST"
      });
      load();
    } catch (e) {
      setErr(e.message);
    }
  };
  const toggleRole = code => setNu(n => ({
    ...n,
    role_codes: n.role_codes.includes(code) ? n.role_codes.filter(c => c !== code) : [...n.role_codes, code]
  }));
  const addUser = async e => {
    e.preventDefault();
    setErr("");
    setOk("");
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: nu
      });
      setOk(`User ${nu.username} created`);
      setNu({
        username: "",
        email: "",
        full_name: "",
        password: "",
        role_codes: []
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Accounting periods"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Name"), /*#__PURE__*/React.createElement("th", null, "Status"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, periods.map(p => /*#__PURE__*/React.createElement("tr", {
    key: p.id
  }, /*#__PURE__*/React.createElement("td", null, p.code), /*#__PURE__*/React.createElement("td", null, p.name), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (p.status === "OPEN" ? "ok" : "bad")
  }, p.status)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, p.status === "OPEN" ? /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    onClick: () => setPeriod(p.id, "close")
  }, "Close") : /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    onClick: () => setPeriod(p.id, "reopen")
  }, "Reopen"))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Add user"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: addUser
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Username"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: nu.username,
    onChange: e => setNu({
      ...nu,
      username: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Email"), /*#__PURE__*/React.createElement("input", {
    value: nu.email,
    onChange: e => setNu({
      ...nu,
      email: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Full name"), /*#__PURE__*/React.createElement("input", {
    value: nu.full_name,
    onChange: e => setNu({
      ...nu,
      full_name: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Password"), /*#__PURE__*/React.createElement("input", {
    type: "password",
    value: nu.password,
    onChange: e => setNu({
      ...nu,
      password: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "field mb"
  }, /*#__PURE__*/React.createElement("label", null, "Roles"), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, roles.map(r => /*#__PURE__*/React.createElement("label", {
    key: r.code,
    style: {
      display: "flex",
      gap: 5,
      alignItems: "center",
      fontSize: 12.5
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: nu.role_codes.includes(r.code),
    onChange: () => toggleRole(r.code)
  }), " ", r.code)))), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Create user")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), ok && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      color: "#16a34a",
      fontSize: 12.5
    }
  }, ok)), /*#__PURE__*/React.createElement(Panel, {
    title: "Users"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Username"), /*#__PURE__*/React.createElement("th", null, "Email"), /*#__PURE__*/React.createElement("th", null, "Roles"), /*#__PURE__*/React.createElement("th", null, "Active"))), /*#__PURE__*/React.createElement("tbody", null, users.map(u => /*#__PURE__*/React.createElement("tr", {
    key: u.id
  }, /*#__PURE__*/React.createElement("td", null, u.username), /*#__PURE__*/React.createElement("td", null, u.email), /*#__PURE__*/React.createElement("td", null, u.roles.join(", ") || (u.is_superuser ? "(superuser)" : "-")), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge " + (u.is_active ? "ok" : "bad")
  }, u.is_active ? "Yes" : "No"))))))));
}

// ---- shell ----------------------------------------------------------------
// ---- transactions: purchase orders (PO -> GRN roll-up) ---------------------
const stBadge = s => /*#__PURE__*/React.createElement("span", {
  className: "badge " + (s === "COMPLETED" ? "ok" : s === "CANCELLED" ? "err" : "")
}, s);
const today = () => new Date().toISOString().slice(0, 10);
function PurchaseOrders() {
  const suppliers = useList("/api/masters/suppliers");
  const chemicals = useList("/api/masters/chemicals");
  const taxes = useList("/api/masters/tax-codes");
  const [pos, setPos] = useState([]);
  const [open, setOpen] = useState(null);
  const [f, setF] = useState({
    supplier_id: "",
    number: genNo("PO"),
    order_date: today(),
    chemical_id: "",
    qty: "100",
    rate: "1200",
    tax_code_id: ""
  });
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api("/api/procurement/purchase-orders").then(setPos).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const create = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    try {
      const r = await api("/api/procurement/purchase-orders", {
        method: "POST",
        body: {
          supplier_id: f.supplier_id,
          number: f.number,
          order_date: f.order_date,
          lines: [{
            chemical_id: f.chemical_id || null,
            qty: f.qty,
            rate: f.rate,
            tax_code_id: f.tax_code_id || null
          }]
        }
      });
      setRes({
        po: r.number,
        status: r.status
      });
      setF({
        ...f,
        number: genNo("PO")
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const confirm = async id => {
    try {
      await api(`/api/procurement/purchase-orders/${id}/confirm`, {
        method: "POST"
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const view = async id => {
    if (open && open.id === id) {
      setOpen(null);
      return;
    }
    try {
      setOpen(await api(`/api/procurement/purchase-orders/${id}`));
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Raise a purchase order"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: create
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Supplier"), /*#__PURE__*/React.createElement(Opts, {
    items: suppliers,
    value: f.supplier_id,
    onChange: e => setF({
      ...f,
      supplier_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "PO number"), /*#__PURE__*/React.createElement("input", {
    value: f.number,
    onChange: e => setF({
      ...f,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Order date"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: f.order_date,
    onChange: e => setF({
      ...f,
      order_date: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Chemical"), /*#__PURE__*/React.createElement(Opts, {
    items: chemicals,
    value: f.chemical_id,
    onChange: e => setF({
      ...f,
      chemical_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty"), /*#__PURE__*/React.createElement("input", {
    value: f.qty,
    onChange: e => setF({
      ...f,
      qty: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Rate"), /*#__PURE__*/React.createElement("input", {
    value: f.rate,
    onChange: e => setF({
      ...f,
      rate: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Tax"), /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: f.tax_code_id,
    onChange: e => setF({
      ...f,
      tax_code_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Create PO"))), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Purchase orders"
  }, pos.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "None yet. GRNs raised against a confirmed PO roll it to PARTIAL / COMPLETED.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Number"), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Lines"), /*#__PURE__*/React.createElement("th", null, "Status"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, pos.map(p => [/*#__PURE__*/React.createElement("tr", {
    key: p.id
  }, /*#__PURE__*/React.createElement("td", null, p.number), /*#__PURE__*/React.createElement("td", null, p.order_date), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, p.lines), /*#__PURE__*/React.createElement("td", null, stBadge(p.status)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    onClick: () => view(p.id)
  }, "View"), " ", p.status === "DRAFT" && /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: () => confirm(p.id)
  }, "Confirm"))), open && open.id === p.id ? /*#__PURE__*/React.createElement("tr", {
    key: p.id + "x"
  }, /*#__PURE__*/React.createElement("td", {
    colSpan: "5"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "#"), /*#__PURE__*/React.createElement("th", null, "Item"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Ordered"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Received"))), /*#__PURE__*/React.createElement("tbody", null, open.lines.map(l => /*#__PURE__*/React.createElement("tr", {
    key: l.id
  }, /*#__PURE__*/React.createElement("td", null, l.line_no), /*#__PURE__*/React.createElement("td", null, l.description || l.chemical_id || "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.qty)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.received_qty)))))))) : null])))));
}

// ---- transactions: sales orders + dispatch (SO -> delivery -> COGS) --------
function SalesOrders() {
  const customers = useList("/api/masters/customers");
  const fabrics = useList("/api/masters/fabric-types");
  const taxes = useList("/api/masters/tax-codes");
  const [sos, setSos] = useState([]);
  const [rolls, setRolls] = useState([]);
  const [sel, setSel] = useState({});
  const [f, setF] = useState({
    customer_id: "",
    number: genNo("SO"),
    order_date: today(),
    fabric_type_id: "",
    qty_kg: "250",
    rate: "300",
    tax_code_id: ""
  });
  const [dsp, setDsp] = useState({
    customer_id: "",
    sales_order_id: ""
  });
  const [res, setRes] = useState(null);
  const [dres, setDres] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api("/api/sales/orders").then(setSos).catch(() => {});
    api("/api/rolls?undelivered=true").then(setRolls).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const chosen = rolls.filter(r => sel[r.id]);
  const totalKg = chosen.reduce((s, r) => s + Number(r.weight_kg || 0), 0);
  const create = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    try {
      const r = await api("/api/sales/orders", {
        method: "POST",
        body: {
          customer_id: f.customer_id,
          number: f.number,
          order_date: f.order_date,
          lines: [{
            fabric_type_id: f.fabric_type_id || null,
            qty_kg: f.qty_kg,
            rate: f.rate,
            tax_code_id: f.tax_code_id || null
          }]
        }
      });
      setRes({
        so: r.number,
        status: r.status,
        ordered_kg: r.ordered_kg
      });
      setF({
        ...f,
        number: genNo("SO")
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const confirm = async id => {
    try {
      await api(`/api/sales/orders/${id}/confirm`, {
        method: "POST"
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const dispatch = async e => {
    e.preventDefault();
    setErr("");
    setDres(null);
    if (chosen.length === 0) {
      setErr("Select at least one undelivered roll");
      return;
    }
    if (!dsp.customer_id) {
      setErr("Pick the customer to dispatch to");
      return;
    }
    try {
      const r = await api("/api/sales/deliveries", {
        method: "POST",
        body: {
          customer_id: dsp.customer_id,
          sales_order_id: dsp.sales_order_id || null,
          delivery_date: today(),
          roll_ids: chosen.map(r => r.id)
        }
      });
      setDres(r);
      setSel({});
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Create a sales order"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: create
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: f.customer_id,
    onChange: e => setF({
      ...f,
      customer_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "SO number"), /*#__PURE__*/React.createElement("input", {
    value: f.number,
    onChange: e => setF({
      ...f,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Order date"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: f.order_date,
    onChange: e => setF({
      ...f,
      order_date: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fabric"), /*#__PURE__*/React.createElement(Opts, {
    items: fabrics,
    value: f.fabric_type_id,
    onChange: e => setF({
      ...f,
      fabric_type_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty (kg)"), /*#__PURE__*/React.createElement("input", {
    value: f.qty_kg,
    onChange: e => setF({
      ...f,
      qty_kg: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Rate"), /*#__PURE__*/React.createElement("input", {
    value: f.rate,
    onChange: e => setF({
      ...f,
      rate: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Tax"), /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: f.tax_code_id,
    onChange: e => setF({
      ...f,
      tax_code_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Create SO"))), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Sales orders"
  }, sos.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "None yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Number"), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Ordered kg"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Delivered kg"), /*#__PURE__*/React.createElement("th", null, "Status"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, sos.map(s => /*#__PURE__*/React.createElement("tr", {
    key: s.id
  }, /*#__PURE__*/React.createElement("td", null, s.number), /*#__PURE__*/React.createElement("td", null, s.order_date), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.ordered_kg ? inr2(s.ordered_kg) : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.delivered_kg ? inr2(s.delivered_kg) : "—"), /*#__PURE__*/React.createElement("td", null, stBadge(s.status)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.status === "DRAFT" && /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: () => confirm(s.id)
  }, "Confirm"))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Dispatch finished rolls (delivery challan \u2192 COGS)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: dispatch
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: dsp.customer_id,
    onChange: e => setDsp({
      ...dsp,
      customer_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Against sales order"), /*#__PURE__*/React.createElement(Opts, {
    items: sos.map(s => ({
      id: s.id,
      name: s.number
    })),
    value: dsp.sales_order_id,
    onChange: e => setDsp({
      ...dsp,
      sales_order_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Selected"), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "9px 0",
      fontWeight: 700
    }
  }, chosen.length, " rolls \xB7 ", inr2(totalKg), " kg"))), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    style: {
      width: 40
    }
  }), /*#__PURE__*/React.createElement("th", null, "Roll no."), /*#__PURE__*/React.createElement("th", null, "Fabric"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Weight (kg)"))), /*#__PURE__*/React.createElement("tbody", null, rolls.length === 0 ? /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "4",
    className: "muted"
  }, "No undelivered rolls \u2014 record a knitting production first.")) : rolls.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!sel[r.id],
    onChange: () => setSel(s => ({
      ...s,
      [r.id]: !s[r.id]
    }))
  })), /*#__PURE__*/React.createElement("td", null, r.roll_no), /*#__PURE__*/React.createElement("td", null, r.fabric || "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.weight_kg)))))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Dispatch (", chosen.length, " rolls)"))), /*#__PURE__*/React.createElement(Result, {
    r: dres
  })));
}
const PAGES = {
  owner: {
    t: "Owner Dashboard",
    c: OwnerDash
  },
  factory: {
    t: "Factory Dashboard",
    c: FactoryDash
  },
  accounts: {
    t: "Accounts Dashboard",
    c: AccountsDash
  },
  chemical: {
    t: "Chemical Dashboard",
    c: ChemicalDash
  },
  purchaseorders: {
    t: "Purchase Orders",
    c: PurchaseOrders
  },
  purchase: {
    t: "Purchase (GRN)",
    c: Purchase
  },
  knitting: {
    t: "Knitting Production",
    c: Knitting
  },
  batching: {
    t: "Batching",
    c: Batching
  },
  salesorders: {
    t: "Sales Orders & Dispatch",
    c: SalesOrders
  },
  sales: {
    t: "Sales Invoice",
    c: Sales
  },
  dyeing: {
    t: "Dyeing & Dispensing",
    c: Dyeing
  },
  customers: {
    t: "Customers",
    c: () => /*#__PURE__*/React.createElement(PartyPage, {
      kind: "customers"
    })
  },
  suppliers: {
    t: "Suppliers",
    c: () => /*#__PURE__*/React.createElement(PartyPage, {
      kind: "suppliers"
    })
  },
  warehouses: {
    t: "Warehouses",
    c: Warehouses
  },
  quality: {
    t: "Quality & Finishing",
    c: Quality
  },
  costing: {
    t: "Costing & Profitability",
    c: Costing
  },
  devices: {
    t: "Devices & Labels",
    c: Devices
  },
  masters: {
    t: "Master Data",
    c: MastersManager
  },
  vouchers: {
    t: "Vouchers",
    c: Vouchers
  },
  admin: {
    t: "Administration",
    c: Admin
  },
  reports: {
    t: "Reports",
    c: Reports
  }
};
function App() {
  const [page, setPage] = useState("owner");
  const [me, setMe] = useState(null);
  useEffect(() => {
    api("/api/auth/me").then(setMe).catch(() => {});
  }, []);
  const Cur = PAGES[page].c;
  const navBtn = (id, label) => /*#__PURE__*/React.createElement("button", {
    className: page === id ? "active" : "",
    onClick: () => setPage(id)
  }, label);
  return /*#__PURE__*/React.createElement("div", {
    className: "app"
  }, /*#__PURE__*/React.createElement("aside", {
    className: "sidebar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "brand"
  }, "Textile ERP", /*#__PURE__*/React.createElement("small", null, "Knitting & Dyeing")), /*#__PURE__*/React.createElement("nav", {
    className: "nav"
  }, /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Dashboards"), navBtn("owner", "Owner"), navBtn("factory", "Factory"), navBtn("accounts", "Accounts"), navBtn("chemical", "Chemical"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Transactions"), navBtn("purchaseorders", "Purchase Orders"), navBtn("purchase", "Purchase (GRN)"), navBtn("knitting", "Knitting"), navBtn("batching", "Batching"), navBtn("salesorders", "Sales Orders"), navBtn("sales", "Sales Invoice"), navBtn("dyeing", "Dyeing & Dispensing"), navBtn("vouchers", "Vouchers"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Masters"), navBtn("masters", "Master Data"), navBtn("customers", "Customers"), navBtn("suppliers", "Suppliers"), navBtn("warehouses", "Warehouses"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Quality"), navBtn("quality", "Quality"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Costing"), navBtn("costing", "Costing"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Devices"), navBtn("devices", "Devices & Labels"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Reports"), navBtn("reports", "Reports"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Admin"), navBtn("admin", "Administration"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Session"), /*#__PURE__*/React.createElement("button", {
    onClick: () => {
      setToken(null);
      location.reload();
    }
  }, "Log out"))), /*#__PURE__*/React.createElement("div", {
    className: "main"
  }, /*#__PURE__*/React.createElement("div", {
    className: "topbar"
  }, /*#__PURE__*/React.createElement("h1", null, PAGES[page].t), /*#__PURE__*/React.createElement("div", {
    className: "who"
  }, me ? me.full_name || me.username : "", " \xB7 ", new Date().toLocaleDateString("en-IN"))), /*#__PURE__*/React.createElement("div", {
    className: "content"
  }, /*#__PURE__*/React.createElement(Cur, null))));
}
function Root() {
  const [authed, setAuthed] = useState(!!getToken());
  return authed ? /*#__PURE__*/React.createElement(App, null) : /*#__PURE__*/React.createElement(Login, {
    onLogin: () => setAuthed(true)
  });
}
ReactDOM.createRoot(document.getElementById("root")).render(/*#__PURE__*/React.createElement(Root, null));