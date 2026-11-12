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
const COMPANY_KEY = "erp_company_id",
  FACTORY_KEY = "erp_factory_id";
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
// Active company/factory scope — sent as request headers so switching actually
// changes the data the backend returns (authorization is validated server-side).
function getScope(k) {
  try {
    return localStorage.getItem(k) || "";
  } catch (e) {
    return "";
  }
}
function setScope(k, v) {
  try {
    v ? localStorage.setItem(k, v) : localStorage.removeItem(k);
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
  const cid = getScope(COMPANY_KEY);
  if (cid) headers["X-Company-Id"] = cid;
  const fid = getScope(FACTORY_KEY);
  if (fid) headers["X-Factory-Id"] = fid;
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

// Richer KPI card: coloured icon chip + value + optional % delta. Clickable when
// onClick is given (shows a hover affordance + drills into the module).
function Stat({
  icon,
  label,
  value,
  sub,
  delta,
  tone,
  onClick
}) {
  const d = delta === undefined || delta === null ? null : Number(delta);
  return /*#__PURE__*/React.createElement("div", {
    className: "stat " + (tone || "") + (onClick ? " clickable" : ""),
    onClick: onClick,
    role: onClick ? "button" : undefined,
    tabIndex: onClick ? 0 : undefined,
    onKeyDown: onClick ? e => {
      (e.key === "Enter" || e.key === " ") && onClick();
    } : undefined
  }, /*#__PURE__*/React.createElement("div", {
    className: "stat-icon"
  }, icon), /*#__PURE__*/React.createElement("div", {
    className: "stat-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "stat-label"
  }, label), /*#__PURE__*/React.createElement("div", {
    className: "stat-value"
  }, value), sub && /*#__PURE__*/React.createElement("div", {
    className: "stat-sub"
  }, sub), d !== null && /*#__PURE__*/React.createElement("div", {
    className: "stat-delta " + (d >= 0 ? "up" : "down")
  }, d >= 0 ? "▲" : "▼", " ", Math.abs(d).toFixed(1), "% vs last month")));
}
function Panel({
  title,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "panel"
  }, title && /*#__PURE__*/React.createElement("h3", null, title), children);
}

// Reusable enterprise table: client-side search, sortable headers, pagination.
// columns: [{key, label, num?, sortable?, render?(row)}]. searchKeys defaults to
// all column keys. Row actions = a column with a render returning buttons.
function DataTable({
  columns,
  rows,
  searchKeys,
  pageSize = 10,
  empty = "Nothing to show.",
  initialSort,
  right,
  rowClass,
  expand
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState(initialSort || null);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState(null); // id of the expanded row
  const sk = searchKeys || columns.map(c => c.key);
  let data = rows || [];
  if (q) {
    const qq = q.toLowerCase();
    data = data.filter(r => sk.some(k => String(r[k] ?? "").toLowerCase().includes(qq)));
  }
  if (sort) {
    const {
      key,
      dir
    } = sort;
    data = [...data].sort((a, b) => {
      let x = a[key],
        y = b[key];
      const nx = Number(x),
        ny = Number(y);
      if (x !== "" && y !== "" && x != null && y != null && !isNaN(nx) && !isNaN(ny)) {
        x = nx;
        y = ny;
      } else {
        x = String(x ?? "").toLowerCase();
        y = String(y ?? "").toLowerCase();
      }
      return (x < y ? -1 : x > y ? 1 : 0) * (dir === "asc" ? 1 : -1);
    });
  }
  const total = data.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const pg = Math.min(page, pages - 1);
  const slice = data.slice(pg * pageSize, pg * pageSize + pageSize);
  const toggle = key => setSort(s => s && s.key === key ? {
    key,
    dir: s.dir === "asc" ? "desc" : "asc"
  } : {
    key,
    dir: "asc"
  });
  const arrow = key => sort && sort.key === key ? sort.dir === "asc" ? " ▲" : " ▼" : "";
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "dt-toolbar"
  }, /*#__PURE__*/React.createElement("input", {
    className: "dt-search",
    placeholder: "Search\u2026",
    value: q,
    onChange: e => {
      setQ(e.target.value);
      setPage(0);
    }
  }), right, /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "dt-count"
  }, total, " row", total !== 1 ? "s" : "")), total === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, empty) : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, expand && /*#__PURE__*/React.createElement("th", {
    className: "dt-expcol"
  }), columns.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.key,
    className: (c.num ? "num " : "") + (c.sortable !== false ? "dt-th" : ""),
    onClick: () => c.sortable !== false && toggle(c.key)
  }, c.label, c.sortable !== false ? arrow(c.key) : "")))), /*#__PURE__*/React.createElement("tbody", null, slice.map((r, i) => {
    const id = r.id || i;
    const isOpen = expand && open === id;
    return /*#__PURE__*/React.createElement(React.Fragment, {
      key: id
    }, /*#__PURE__*/React.createElement("tr", {
      className: rowClass ? rowClass(r) : undefined
    }, expand && /*#__PURE__*/React.createElement("td", {
      className: "dt-expcol"
    }, /*#__PURE__*/React.createElement("button", {
      className: "dt-exp",
      "aria-label": "expand",
      onClick: () => setOpen(isOpen ? null : id)
    }, isOpen ? "▾" : "▸")), columns.map(c => /*#__PURE__*/React.createElement("td", {
      key: c.key,
      className: c.num ? "num" : ""
    }, c.render ? c.render(r) : r[c.key] ?? ""))), isOpen && /*#__PURE__*/React.createElement("tr", {
      className: "dt-detail"
    }, /*#__PURE__*/React.createElement("td", {
      colSpan: columns.length + 1
    }, expand(r))));
  }))), pages > 1 && /*#__PURE__*/React.createElement("div", {
    className: "dt-pager"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    disabled: pg === 0,
    onClick: () => setPage(pg - 1)
  }, "\u2039 Prev"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "Page ", pg + 1, " of ", pages), /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    disabled: pg >= pages - 1,
    onClick: () => setPage(pg + 1)
  }, "Next \u203A")));
}

// Reusable line-items editor for transaction forms: configurable input columns,
// add/remove rows, an optional computed per-row amount and a live subtotal.
// NOTE: statutory GST/total are posted by the backend — the subtotal here is the
// taxable (qty x rate) figure only, shown for data entry, never as the tax source.
function LineItems({
  columns,
  rows,
  setRows,
  newRow,
  amount,
  amountLabel = "Taxable subtotal",
  note,
  minRows = 1,
  footer
}) {
  const set = (i, k, v) => setRows(rs => rs.map((r, j) => j === i ? {
    ...r,
    [k]: v
  } : r));
  const del = i => setRows(rs => rs.length > minRows ? rs.filter((_, j) => j !== i) : rs);
  const subtotal = amount ? rows.reduce((s, r) => s + Number(amount(r) || 0), 0) : null;
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, columns.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.key,
    className: c.num ? "num" : ""
  }, c.label)), amount && /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Amount"), /*#__PURE__*/React.createElement("th", {
    style: {
      width: 36
    }
  }))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, columns.map(c => /*#__PURE__*/React.createElement("td", {
    key: c.key,
    className: c.num ? "num" : ""
  }, c.type === "opts" ? /*#__PURE__*/React.createElement(Opts, {
    items: c.items || [],
    value: r[c.key],
    onChange: e => set(i, c.key, e.target.value),
    placeholder: c.placeholder || "—"
  }) : c.type === "select" ? /*#__PURE__*/React.createElement("select", {
    value: r[c.key],
    onChange: e => set(i, c.key, e.target.value)
  }, (c.options || []).map(o => /*#__PURE__*/React.createElement("option", {
    key: o[0],
    value: o[0]
  }, o[1]))) : /*#__PURE__*/React.createElement("input", {
    style: {
      width: c.width || 120
    },
    value: r[c.key],
    onChange: e => set(i, c.key, e.target.value),
    placeholder: c.placeholder
  }))), amount && /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(amount(r))), /*#__PURE__*/React.createElement("td", null, rows.length > minRows && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    title: "Remove",
    onClick: () => del(i)
  }, "\u2715"))))), footer ? /*#__PURE__*/React.createElement("tfoot", null, footer(rows)) : amount ? /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    colSpan: columns.length
  }, amountLabel), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(subtotal)), /*#__PURE__*/React.createElement("th", null))) : null), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 10,
      alignItems: "center",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    onClick: () => setRows(rs => [...rs, newRow()])
  }, "+ Add line"), note && /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, note)));
}

// Lazy line-item detail panels for expandable order rows.
function PODetail({
  id,
  names
}) {
  const [d] = useData("/api/procurement/purchase-orders/" + id);
  if (!d) return /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Loading\u2026");
  const nm = l => l.description || names && names[l.chemical_id] || (l.chemical_id ? "Chemical" : "—");
  return /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "#"), /*#__PURE__*/React.createElement("th", null, "Item"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Ordered"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Received"))), /*#__PURE__*/React.createElement("tbody", null, (d.lines || []).map(l => /*#__PURE__*/React.createElement("tr", {
    key: l.id
  }, /*#__PURE__*/React.createElement("td", null, l.line_no), /*#__PURE__*/React.createElement("td", null, nm(l)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.qty)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.received_qty))))));
}
function SODetail({
  id
}) {
  const [d] = useData("/api/sales/orders/" + id);
  if (!d) return /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Loading\u2026");
  return /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "#"), /*#__PURE__*/React.createElement("th", null, "Fabric"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Qty (kg)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Rate"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Amount"))), /*#__PURE__*/React.createElement("tbody", null, (d.lines || []).map(l => /*#__PURE__*/React.createElement("tr", {
    key: l.id
  }, /*#__PURE__*/React.createElement("td", null, l.line_no), /*#__PURE__*/React.createElement("td", null, l.description || l.fabric_type_id || "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.qty_kg)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(l.rate)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(Number(l.qty_kg) * Number(l.rate)))))));
}

// ---- login ----------------------------------------------------------------
function Login({
  onLogin
}) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async e => {
    e.preventDefault();
    setErr("");
    setBusy(true);
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
    } finally {
      setBusy(false);
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
    autoComplete: "username",
    autoFocus: true,
    onChange: e => setU(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Password"), /*#__PURE__*/React.createElement("input", {
    type: "password",
    autoComplete: "current-password",
    value: p,
    onChange: e => setP(e.target.value)
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    style: {
      width: "100%"
    },
    disabled: busy || !u || !p
  }, busy ? "Signing in…" : "Sign in"), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)));
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
const DONUT = ["#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0d9488", "#e11d48", "#64748b"];
const FLOW_NAV = {
  "Yarn Issue": "knitting",
  "Knitting": "knitting",
  "Greige": "stock",
  "Dyeing": "dyeing",
  "Finishing": "quality",
  "Finished Goods": "stock"
};
function OwnerDash({
  onNav,
  asOf,
  refreshKey
}) {
  const nav = onNav || (() => {});
  const q = p => p + (p.includes("?") ? "&" : "?") + (asOf ? "as_of=" + asOf : "") + "&_r=" + (refreshKey || 0);
  const [d, err] = useData(q("/api/dashboard/owner"));
  const trend = useList(q("/api/dashboard/production-trend"));
  const chem = useData(q("/api/dashboard/chemical"))[0];
  const util = useList(q("/api/hardware/knitting-summary"));
  const batches = useList(q("/api/dyeing/batches"));
  const acts = useList(q("/api/vouchers"));
  if (err) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Couldn't load dashboard data \u2014 ", String(err)), /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    style: {
      marginTop: 10
    },
    onClick: () => location.reload()
  }, "Retry"));
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Loading dashboard\u2026"));
  const dl = d.deltas || {};
  const k = d.kpis || {};
  const mix = (chem && chem.top_consumption || []).map((m, i) => ({
    name: m.material,
    value: Number(m.cost),
    c: DONUT[i % DONUT.length]
  }));
  const mixTotal = mix.reduce((s, m) => s + m.value, 0);
  const alerts = d.alerts || [];
  const dayShort = s => s ? s.slice(5) : "";
  const sevCls = {
    bad: "bad",
    warn: "warn",
    info: "info"
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kpi-grid"
  }, /*#__PURE__*/React.createElement(Stat, {
    tone: "",
    icon: "\uD83E\uDDF6",
    label: "Today's Production",
    value: inr2(k.production_today_kg) + " kg",
    onClick: () => nav("knitting")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "teal",
    icon: "\uD83D\uDCC5",
    label: "Monthly Production",
    value: inr2(k.production_month_kg) + " kg",
    onClick: () => nav("knitting")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "green",
    icon: "\uD83D\uDED2",
    label: "Active Orders",
    value: k.active_orders,
    onClick: () => nav("salesorders")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "amber",
    icon: "\u23F3",
    label: "Pending Orders",
    value: k.pending_orders,
    onClick: () => nav("salesorders")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "violet",
    icon: "\uD83D\uDCE6",
    label: "Finished Goods",
    value: inr2(k.finished_goods_kg) + " kg",
    sub: inr(k.finished_goods_value),
    onClick: () => nav("stock")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "",
    icon: "\uD83E\uDDF5",
    label: "Greige Stock",
    value: inr2(k.greige_kg) + " kg",
    sub: inr(k.greige_value),
    onClick: () => nav("stock")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "amber",
    icon: "\uD83D\uDCE5",
    label: "Receivables",
    value: inr(k.receivables),
    sub: Number(d.overdue_receivables) > 0 ? "overdue " + inr(d.overdue_receivables) : "",
    onClick: () => nav("customers")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "rose",
    icon: "\uD83D\uDCE4",
    label: "Payables",
    value: inr(k.payables),
    sub: Number(d.overdue_payables) > 0 ? "overdue " + inr(d.overdue_payables) : "",
    onClick: () => nav("suppliers")
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Manufacturing flow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "flow"
  }, (d.manufacturing_flow || []).map((s, i, arr) => /*#__PURE__*/React.createElement(React.Fragment, {
    key: s.stage
  }, /*#__PURE__*/React.createElement("div", {
    className: "flow-stage",
    onClick: () => nav(FLOW_NAV[s.stage] || "owner"),
    role: "button",
    tabIndex: 0
  }, /*#__PURE__*/React.createElement("div", {
    className: "flow-stage-name"
  }, s.stage), /*#__PURE__*/React.createElement("div", {
    className: "flow-stage-val"
  }, isNaN(Number(s.value)) ? s.value : inr2(s.value)), /*#__PURE__*/React.createElement("div", {
    className: "flow-stage-metric"
  }, s.metric)), i < arr.length - 1 && /*#__PURE__*/React.createElement("div", {
    className: "flow-arrow"
  }, "\u2192"))))), alerts.length > 0 && /*#__PURE__*/React.createElement(Panel, {
    title: "Alerts & approvals"
  }, /*#__PURE__*/React.createElement("div", {
    className: "alert-list"
  }, alerts.map((a, i) => /*#__PURE__*/React.createElement("div", {
    className: "alert-row " + (sevCls[a.severity] || ""),
    key: i,
    onClick: () => nav(a.page),
    role: "button",
    tabIndex: 0
  }, /*#__PURE__*/React.createElement("span", {
    className: "alert-dot"
  }), /*#__PURE__*/React.createElement("span", {
    className: "alert-kind"
  }, a.kind), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, a.text), /*#__PURE__*/React.createElement("span", {
    className: "alert-go"
  }, "View \u2192"))))), /*#__PURE__*/React.createElement("div", {
    className: "section-head"
  }, "Financial overview"), /*#__PURE__*/React.createElement("div", {
    className: "stats"
  }, /*#__PURE__*/React.createElement(Stat, {
    tone: "",
    icon: "\u20B9",
    label: "Revenue (YTD)",
    value: inr(d.revenue),
    delta: dl.revenue
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "green",
    icon: "\uD83D\uDCC8",
    label: "Gross Profit",
    value: inr(d.gross_profit),
    delta: dl.gross_profit
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "violet",
    icon: "\uFF05",
    label: "Net Profit",
    value: inr(d.net_profit),
    delta: dl.net_profit
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "teal",
    icon: "\uD83C\uDFE6",
    label: "Cash & Bank",
    value: inr(d.cash_and_bank)
  })), /*#__PURE__*/React.createElement("div", {
    className: "grid2"
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Production vs dyeing output (last 14 days)"
  }, R ? /*#__PURE__*/React.createElement("div", {
    className: "chartbox"
  }, /*#__PURE__*/React.createElement(R.ResponsiveContainer, null, /*#__PURE__*/React.createElement(R.BarChart, {
    data: (trend || []).map(p => ({
      d: dayShort(p.date),
      Knitting: Number(p.knitting_kg),
      Dyeing: Number(p.dyeing_kg)
    })),
    margin: {
      top: 10,
      right: 16,
      left: 0,
      bottom: 0
    }
  }, /*#__PURE__*/React.createElement(R.CartesianGrid, {
    strokeDasharray: "3 3",
    stroke: "#eef2f7"
  }), /*#__PURE__*/React.createElement(R.XAxis, {
    dataKey: "d",
    fontSize: 11
  }), /*#__PURE__*/React.createElement(R.YAxis, {
    fontSize: 11
  }), /*#__PURE__*/React.createElement(R.Tooltip, {
    formatter: v => inr2(v) + " kg"
  }), /*#__PURE__*/React.createElement(R.Legend, null), /*#__PURE__*/React.createElement(R.Bar, {
    dataKey: "Knitting",
    stackId: "a",
    fill: "#2563eb",
    radius: [0, 0, 0, 0]
  }), /*#__PURE__*/React.createElement(R.Bar, {
    dataKey: "Dyeing",
    stackId: "a",
    fill: "#16a34a",
    radius: [4, 4, 0, 0]
  })))) : /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Chart unavailable")), /*#__PURE__*/React.createElement(Panel, {
    title: "Chemical consumption mix"
  }, mix.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No consumption yet.") : /*#__PURE__*/React.createElement("div", null, R && /*#__PURE__*/React.createElement("div", {
    style: {
      height: 170
    }
  }, /*#__PURE__*/React.createElement(R.ResponsiveContainer, null, /*#__PURE__*/React.createElement(R.PieChart, null, /*#__PURE__*/React.createElement(R.Pie, {
    data: mix,
    dataKey: "value",
    nameKey: "name",
    innerRadius: 48,
    outerRadius: 70,
    paddingAngle: 2
  }, mix.map((m, i) => /*#__PURE__*/React.createElement(R.Cell, {
    key: i,
    fill: m.c
  }))), /*#__PURE__*/React.createElement(R.Tooltip, {
    formatter: v => inr(v)
  })))), /*#__PURE__*/React.createElement("div", {
    className: "legend"
  }, mix.map((m, i) => /*#__PURE__*/React.createElement("div", {
    className: "lr",
    key: i
  }, /*#__PURE__*/React.createElement("span", {
    className: "dot",
    style: {
      background: m.c
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, m.name), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, mixTotal ? (m.value / mixTotal * 100).toFixed(0) : 0, "%"))))))), /*#__PURE__*/React.createElement("div", {
    className: "grid-half"
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Active dyeing batches"
  }, (batches || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No batches yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Batch"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Fabric (kg)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Rolls"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, batches.slice(0, 6).map(b => /*#__PURE__*/React.createElement("tr", {
    key: b.id
  }, /*#__PURE__*/React.createElement("td", null, b.batch_code), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(b.fabric_kg)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, b.rolls), /*#__PURE__*/React.createElement("td", null, stBadge(b.status))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Machine utilization"
  }, (util || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No machine telemetry yet \u2014 simulate on Devices & Labels.") : util.map((m, i) => {
    const pc = Math.min(100, Number(m.efficiency_pct || 0));
    return /*#__PURE__*/React.createElement("div", {
      className: "util-row",
      key: i
    }, /*#__PURE__*/React.createElement("span", {
      className: "nm"
    }, m.machine), /*#__PURE__*/React.createElement("span", {
      className: "util-bar"
    }, /*#__PURE__*/React.createElement("span", {
      className: "util-fill",
      style: {
        width: pc + "%",
        background: pc >= 80 ? "#16a34a" : pc >= 50 ? "#2563eb" : "#f59e0b"
      }
    })), /*#__PURE__*/React.createElement("span", {
      className: "pc"
    }, pc.toFixed(0), "%"));
  }))), /*#__PURE__*/React.createElement("div", {
    className: "grid-half"
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Inventory alerts"
  }, alerts.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "All stock above reorder level.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Item"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "On hand"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Reorder"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, alerts.map((a, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, a.name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(a.on_hand)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(a.reorder_level)), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: "badge bad"
  }, "Low stock"))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent activity"
  }, (acts || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No postings yet.") : acts.slice(0, 8).map((a, i) => /*#__PURE__*/React.createElement("div", {
    className: "act",
    key: i
  }, /*#__PURE__*/React.createElement("span", {
    className: "tt"
  }, a.voucher_date ? a.voucher_date.slice(5) : ""), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("b", null, a.number), " \u2014 ", a.narration || "—"), /*#__PURE__*/React.createElement("span", {
    className: "num",
    style: {
      width: 90
    }
  }, inr2(a.total_debit)), /*#__PURE__*/React.createElement("span", {
    className: "badge info"
  }, a.status))))));
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
function FactoryDash({
  onNav,
  asOf,
  refreshKey
}) {
  const nav = onNav || (() => {});
  const q = p => p + (p.includes("?") ? "&" : "?") + (asOf ? "as_of=" + asOf : "") + "&_r=" + (refreshKey || 0);
  const [d, err] = useData(q("/api/dashboard/factory"));
  const trend = useList(q("/api/dashboard/production-trend"));
  const util = useList(q("/api/hardware/knitting-summary"));
  const batches = useList(q("/api/dyeing/batches"));
  if (err) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Couldn't load \u2014 ", String(err)));
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Loading\u2026"));
  const dayShort = s => s ? s.slice(5) : "";
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kpi-grid"
  }, /*#__PURE__*/React.createElement(Stat, {
    tone: "",
    icon: "\uD83E\uDDF6",
    label: "Knitting output (kg)",
    value: inr2(d.knitting_output_kg),
    onClick: () => nav("knitting")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "violet",
    icon: "\uD83C\uDFA8",
    label: "Dyeing output (kg)",
    value: inr2(d.dyeing_output_kg),
    onClick: () => nav("dyeing")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "amber",
    icon: "\u2699\uFE0F",
    label: "Active batches",
    value: d.active_batches,
    onClick: () => nav("batching")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "teal",
    icon: "\uD83E\uDDEA",
    label: "Chemical consumption",
    value: inr(d.chemical_consumption_cost),
    onClick: () => nav("chemical")
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Production vs dyeing output (last 14 days)"
  }, R ? /*#__PURE__*/React.createElement("div", {
    className: "chartbox"
  }, /*#__PURE__*/React.createElement(R.ResponsiveContainer, null, /*#__PURE__*/React.createElement(R.BarChart, {
    data: (trend || []).map(p => ({
      d: dayShort(p.date),
      Knitting: Number(p.knitting_kg),
      Dyeing: Number(p.dyeing_kg)
    })),
    margin: {
      top: 10,
      right: 16,
      left: 0,
      bottom: 0
    }
  }, /*#__PURE__*/React.createElement(R.CartesianGrid, {
    strokeDasharray: "3 3",
    stroke: "#eef2f7"
  }), /*#__PURE__*/React.createElement(R.XAxis, {
    dataKey: "d",
    fontSize: 11
  }), /*#__PURE__*/React.createElement(R.YAxis, {
    fontSize: 11
  }), /*#__PURE__*/React.createElement(R.Tooltip, {
    formatter: v => inr2(v) + " kg"
  }), /*#__PURE__*/React.createElement(R.Legend, null), /*#__PURE__*/React.createElement(R.Bar, {
    dataKey: "Knitting",
    stackId: "a",
    fill: "#2563eb"
  }), /*#__PURE__*/React.createElement(R.Bar, {
    dataKey: "Dyeing",
    stackId: "a",
    fill: "#16a34a",
    radius: [4, 4, 0, 0]
  })))) : /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Chart unavailable")), /*#__PURE__*/React.createElement("div", {
    className: "grid-half"
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Machine utilization"
  }, (util || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No machine telemetry yet \u2014 simulate on Devices & Labels.") : util.map((m, i) => {
    const pc = Math.min(100, Number(m.efficiency_pct || 0));
    return /*#__PURE__*/React.createElement("div", {
      className: "util-row",
      key: i
    }, /*#__PURE__*/React.createElement("span", {
      className: "nm"
    }, m.machine), /*#__PURE__*/React.createElement("span", {
      className: "util-bar"
    }, /*#__PURE__*/React.createElement("span", {
      className: "util-fill",
      style: {
        width: pc + "%",
        background: pc >= 80 ? "#16a34a" : pc >= 50 ? "#2563eb" : "#f59e0b"
      }
    })), /*#__PURE__*/React.createElement("span", {
      className: "pc"
    }, pc.toFixed(0), "%"));
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Active dyeing batches"
  }, (batches || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No batches yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Batch"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Fabric (kg)"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Rolls"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, batches.slice(0, 8).map(b => /*#__PURE__*/React.createElement("tr", {
    key: b.id
  }, /*#__PURE__*/React.createElement("td", null, b.batch_code), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(b.fabric_kg)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, b.rolls), /*#__PURE__*/React.createElement("td", null, stBadge(b.status)))))))));
}
function AccountsDash({
  onNav,
  asOf,
  refreshKey
}) {
  const nav = onNav || (() => {});
  const q = p => p + (p.includes("?") ? "&" : "?") + (asOf ? "as_of=" + asOf : "") + "&_r=" + (refreshKey || 0);
  const [d, err] = useData(q("/api/dashboard/accounts"));
  const acts = useList(q("/api/vouchers"));
  if (err) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Couldn't load \u2014 ", String(err)));
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Loading\u2026"));
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kpi-grid"
  }, /*#__PURE__*/React.createElement(Stat, {
    tone: "green",
    icon: "\uD83D\uDCE5",
    label: "Today's receipts",
    value: inr(d.today_receipts),
    onClick: () => nav("vouchers")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "rose",
    icon: "\uD83D\uDCE4",
    label: "Today's payments",
    value: inr(d.today_payments),
    onClick: () => nav("vouchers")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "amber",
    icon: "\uD83D\uDC64",
    label: "Debtors outstanding",
    value: inr(d.debtors_total),
    onClick: () => nav("customers")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "violet",
    icon: "\uD83C\uDFE2",
    label: "Creditors outstanding",
    value: inr(d.creditors_total),
    onClick: () => nav("suppliers")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "teal",
    icon: "\uD83E\uDDFE",
    label: "GST net payable",
    value: inr(d.gst_net_payable),
    onClick: () => nav("acctops")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "",
    icon: d.trial_balance_balanced ? "✅" : "⚠️",
    label: "Trial balance",
    onClick: () => nav("reports"),
    value: /*#__PURE__*/React.createElement("span", {
      className: "badge " + (d.trial_balance_balanced ? "ok" : "bad")
    }, d.trial_balance_balanced ? "Balanced" : "Out of balance")
  })), /*#__PURE__*/React.createElement("div", {
    className: "grid-half"
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Bank balances"
  }, (d.bank_balances || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No bank accounts yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Account"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Balance"))), /*#__PURE__*/React.createElement("tbody", null, (d.bank_balances || []).map((b, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, b.name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(b.balance))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent vouchers"
  }, (acts || []).length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No postings yet.") : acts.slice(0, 8).map((a, i) => /*#__PURE__*/React.createElement("div", {
    className: "act",
    key: i
  }, /*#__PURE__*/React.createElement("span", {
    className: "tt"
  }, a.voucher_date ? a.voucher_date.slice(5) : ""), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("b", null, a.number), " \u2014 ", a.narration || "—"), /*#__PURE__*/React.createElement("span", {
    className: "num",
    style: {
      width: 90
    }
  }, inr2(a.total_debit)), /*#__PURE__*/React.createElement("span", {
    className: "badge info"
  }, a.status))))));
}
function ChemicalDash({
  onNav,
  asOf,
  refreshKey
}) {
  const nav = onNav || (() => {});
  const q = p => p + (p.includes("?") ? "&" : "?") + (asOf ? "as_of=" + asOf : "") + "&_r=" + (refreshKey || 0);
  const [d, err] = useData(q("/api/dashboard/chemical"));
  if (err) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Couldn't load \u2014 ", String(err)));
  if (!d) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Loading\u2026"));
  const bars = (d.top_consumption || []).map(r => ({
    material: r.material,
    cost: Number(r.cost)
  }));
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kpi-grid"
  }, /*#__PURE__*/React.createElement(Stat, {
    tone: "violet",
    icon: "\uD83C\uDFA8",
    label: "Dye cost",
    value: inr(d.dye_cost),
    onClick: () => nav("dyeing")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "teal",
    icon: "\uD83E\uDDEA",
    label: "Chemical cost",
    value: inr(d.chemical_cost),
    onClick: () => nav("stock")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: "",
    icon: "\u03A3",
    label: "Total consumption",
    value: inr(d.total_consumption_cost),
    onClick: () => nav("costing")
  }), /*#__PURE__*/React.createElement(Stat, {
    tone: d.dispensing_errors ? "rose" : "green",
    icon: d.dispensing_errors ? "⚠️" : "✅",
    label: "Dispensing errors",
    value: d.dispensing_errors,
    onClick: () => nav("devices")
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
function FinalAccounts() {
  const mfg = useData("/api/reports/manufacturing-account")[0];
  const trd = useData("/api/reports/trading-account")[0];
  const ff = useData("/api/reports/funds-flow")[0];
  const line = (l, v, b) => /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    style: b ? {
      fontWeight: 700
    } : {}
  }, l), /*#__PURE__*/React.createElement("td", {
    className: "num",
    style: b ? {
      fontWeight: 700
    } : {}
  }, inr2(v)));
  return /*#__PURE__*/React.createElement("div", {
    className: "grid3"
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Manufacturing account"
  }, !mfg ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "\u2026") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("tbody", null, line("Opening WIP", mfg.opening_wip), line("Charged to production", mfg.charged_to_production), line("Less: closing WIP", mfg.closing_wip), line("Cost of goods manufactured", mfg.cost_of_goods_manufactured, true)))), /*#__PURE__*/React.createElement(Panel, {
    title: "Trading account"
  }, !trd ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "\u2026") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("tbody", null, line("Sales", trd.sales), line("Opening stock", trd.opening_stock), line("Cost of goods manufactured", trd.cost_of_goods_manufactured), line("Less: closing stock", trd.closing_stock), line("Cost of goods sold", trd.cost_of_goods_sold), line("Gross profit", trd.gross_profit, true)))), /*#__PURE__*/React.createElement(Panel, {
    title: "Funds flow statement"
  }, !ff ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "\u2026") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("tbody", null, line("Funds from operations", ff.funds_from_operations), /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "2",
    className: "muted",
    style: {
      paddingTop: 10
    }
  }, "Sources")), (ff.sources || []).map((s, i) => /*#__PURE__*/React.createElement("tr", {
    key: "s" + i
  }, /*#__PURE__*/React.createElement("td", null, "\xB7 ", s.item), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(s.amount)))), line("Total sources", ff.total_sources, true), /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "2",
    className: "muted",
    style: {
      paddingTop: 10
    }
  }, "Applications")), (ff.applications || []).map((a, i) => /*#__PURE__*/React.createElement("tr", {
    key: "a" + i
  }, /*#__PURE__*/React.createElement("td", null, "\xB7 ", a.item), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(a.amount)))), line("Total applications", ff.total_applications, true), line("Net change in working capital", ff.net_working_capital_change, true)))));
}
function GeneralLedger({
  code,
  onPick
}) {
  const accounts = useList("/api/accounts");
  const [sel, setSel] = useState(code || "");
  const [rng, setRng] = useState({
    from: "",
    to: today()
  });
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (code) setSel(code);
  }, [code]);
  const load = useCallback(async c => {
    const acc = c || sel;
    if (!acc) {
      setData(null);
      return;
    }
    setErr("");
    setBusy(true);
    try {
      let q = `/api/reports/ledger/${acc}`;
      const p = [];
      if (rng.from) p.push("date_from=" + rng.from);
      if (rng.to) p.push("date_to=" + rng.to);
      if (p.length) q += "?" + p.join("&");
      setData(await api(q));
    } catch (ex) {
      setErr(ex.message);
      setData(null);
    } finally {
      setBusy(false);
    }
  }, [sel, rng.from, rng.to]);
  useEffect(() => {
    if (code) {
      setSel(code);
      load(code);
    }
  }, [code]);
  return /*#__PURE__*/React.createElement(Panel, {
    title: "General ledger (by account)"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb",
    style: {
      alignItems: "end",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      minWidth: 280
    }
  }, /*#__PURE__*/React.createElement("label", null, "Account"), /*#__PURE__*/React.createElement("select", {
    value: sel,
    onChange: e => {
      setSel(e.target.value);
      load(e.target.value);
    }
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Select an account\u2026"), accounts.map(a => /*#__PURE__*/React.createElement("option", {
    key: a.code,
    value: a.code
  }, a.code, " \xB7 ", a.name)))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "From"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: rng.from,
    onChange: e => setRng({
      ...rng,
      from: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "To"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: rng.to,
    onChange: e => setRng({
      ...rng,
      to: e.target.value
    })
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    disabled: busy || !sel,
    onClick: () => load()
  }, "Refresh")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), data && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "mb",
    style: {
      fontWeight: 700
    }
  }, data.account_code, " \xB7 ", data.account_name), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Voucher"), /*#__PURE__*/React.createElement("th", null, "Narration"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Debit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Credit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Balance"))), /*#__PURE__*/React.createElement("tbody", null, data.entries.length === 0 && /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "6",
    className: "muted"
  }, "No postings in this period.")), data.entries.map((e, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, e.date), /*#__PURE__*/React.createElement("td", null, e.voucher_number), /*#__PURE__*/React.createElement("td", null, e.narration || "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, Number(e.debit) ? inr2(e.debit) : ""), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, Number(e.credit) ? inr2(e.credit) : ""), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(e.running_balance))))), /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    colSpan: "5"
  }, "Closing balance"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(data.closing_balance))))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      marginTop: 6
    }
  }, "Balance is debit-positive (Dr +, Cr \u2212).")));
}
function TrialBalance({
  onDrill
}) {
  const [rng, setRng] = useState({
    from: "",
    to: today()
  });
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setErr("");
    setBusy(true);
    try {
      let q = "/api/reports/trial-balance";
      const p = [];
      if (rng.from) p.push("date_from=" + rng.from);
      if (rng.to) p.push("date_to=" + rng.to);
      if (p.length) q += "?" + p.join("&");
      setData(await api(q));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }, [rng.from, rng.to]);
  useEffect(() => {
    load();
  }, []);
  const n = v => Number(v) ? inr2(v) : "";
  const t = data && data.totals;
  return /*#__PURE__*/React.createElement(Panel, {
    title: "Trial balance"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb",
    style: {
      alignItems: "end",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "From"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: rng.from,
    onChange: e => setRng({
      ...rng,
      from: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "To"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: rng.to,
    onChange: e => setRng({
      ...rng,
      to: e.target.value
    })
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    disabled: busy,
    onClick: load
  }, "Refresh"), data && /*#__PURE__*/React.createElement("span", {
    className: "badge " + (data.is_balanced ? "ok" : "bad"),
    style: {
      alignSelf: "center"
    }
  }, data.is_balanced ? "Balanced" : "Out of balance")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), data && /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Account"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Opening Dr"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Opening Cr"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Period Dr"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Period Cr"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Closing Dr"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Closing Cr"))), /*#__PURE__*/React.createElement("tbody", null, data.rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.account_code
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("a", {
    href: "#gl",
    onClick: ev => {
      ev.preventDefault();
      onDrill && onDrill(r.account_code);
    },
    style: {
      color: "#2563eb",
      cursor: "pointer",
      textDecoration: "underline"
    }
  }, r.account_code)), /*#__PURE__*/React.createElement("td", null, r.account_name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, n(r.opening_debit)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, n(r.opening_credit)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, n(r.period_debit)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, n(r.period_credit)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, n(r.closing_debit)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, n(r.closing_credit))))), t && /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    colSpan: "2"
  }, "Totals"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(t.opening_debit)), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(t.opening_credit)), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(t.period_debit)), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(t.period_credit)), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(t.closing_debit)), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(t.closing_credit))))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      marginTop: 6
    }
  }, "Click an account code to open its general ledger."));
}
function Reports() {
  const [fmt, setFmt] = useState("xlsx");
  const [busy, setBusy] = useState("");
  const [glCode, setGlCode] = useState("");
  const drill = c => {
    setGlCode(c);
    setTimeout(() => {
      const el = document.getElementById("gl");
      if (el) el.scrollIntoView({
        behavior: "smooth"
      });
    }, 50);
  };
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
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(TrialBalance, {
    onDrill: drill
  }), /*#__PURE__*/React.createElement("div", {
    id: "gl"
  }, /*#__PURE__*/React.createElement(GeneralLedger, {
    code: glCode
  })), /*#__PURE__*/React.createElement(FinalAccounts, null), /*#__PURE__*/React.createElement(Panel, {
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
  }, busy === id ? "…" : "Download"))))))));
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
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: insp,
    pageSize: 10,
    empty: "No inspections yet.",
    initialSort: {
      key: "inspection_date",
      dir: "desc"
    },
    searchKeys: ["number", "inspection_date", "result", "colour_fastness"],
    columns: [{
      key: "number",
      label: "No."
    }, {
      key: "inspection_date",
      label: "Date"
    }, {
      key: "delta_e",
      label: "Delta E",
      render: r => r.delta_e || "-"
    }, {
      key: "colour_fastness",
      label: "Fastness",
      render: r => r.colour_fastness || "-"
    }, {
      key: "result",
      label: "Result",
      render: r => /*#__PURE__*/React.createElement("span", {
        className: "badge " + (RESULT_CLS[r.result] || "")
      }, r.result)
    }]
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Customer complaints"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: comp,
    pageSize: 10,
    empty: "No complaints.",
    initialSort: {
      key: "complaint_date",
      dir: "desc"
    },
    searchKeys: ["number", "complaint_date", "severity", "status", "description"],
    columns: [{
      key: "number",
      label: "No."
    }, {
      key: "complaint_date",
      label: "Date"
    }, {
      key: "severity",
      label: "Severity"
    }, {
      key: "status",
      label: "Status",
      render: r => /*#__PURE__*/React.createElement("span", {
        className: "badge " + (r.status === "OPEN" ? "warn" : "ok")
      }, r.status)
    }, {
      key: "description",
      label: "Description"
    }]
  })));
}

// ---- masters: parties -----------------------------------------------------
function PartyLedger({
  kind,
  party,
  onClose
}) {
  // party: {id,name}
  const isCust = kind === "customers";
  const [rng, setRng] = useState({
    from: "",
    to: today()
  });
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setErr("");
    setBusy(true);
    try {
      const idp = isCust ? "customer_id" : "supplier_id";
      const ep = isCust ? "customer-statement" : "supplier-statement";
      let q = `/api/reports/${ep}?${idp}=${party.id}`;
      if (rng.from) q += `&date_from=${rng.from}`;
      if (rng.to) q += `&date_to=${rng.to}`;
      setData(await api(q));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }, [party.id, rng.from, rng.to, isCust]);
  useEffect(() => {
    load();
  }, [party.id]); // reload when party changes; date changes apply on Refresh
  const nature = isCust ? "Dr (owes us)" : "Cr (we owe)";
  return /*#__PURE__*/React.createElement(Panel, {
    title: `Ledger — ${party.name}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb",
    style: {
      alignItems: "end",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "From"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: rng.from,
    onChange: e => setRng({
      ...rng,
      from: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "To"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: rng.to,
    onChange: e => setRng({
      ...rng,
      to: e.target.value
    })
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    disabled: busy,
    onClick: load
  }, "Refresh"), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    onClick: onClose
  }, "Close")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), data && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Voucher"), /*#__PURE__*/React.createElement("th", null, "Narration"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Debit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Credit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Balance"))), /*#__PURE__*/React.createElement("tbody", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "5"
  }, /*#__PURE__*/React.createElement("b", null, "Opening balance")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("b", null, inr2(data.opening)))), data.rows.length === 0 && /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "6",
    className: "muted"
  }, "No transactions in this period.")), data.rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, r.date), /*#__PURE__*/React.createElement("td", null, r.voucher), /*#__PURE__*/React.createElement("td", null, r.narration || "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, Number(r.debit) ? inr2(r.debit) : ""), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, Number(r.credit) ? inr2(r.credit) : ""), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.balance))))), /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    colSpan: "5"
  }, "Closing balance"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(data.closing))))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      marginTop: 6
    }
  }, "Positive balance = outstanding \xB7 ", nature, ".")));
}
function PartyPage({
  kind
}) {
  // kind: "customers" | "suppliers"
  const isCust = kind === "customers";
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  const [ledger, setLedger] = useState(null); // {id,name} of party whose ledger is open
  const blank = {
    code: "",
    name: "",
    gstin: "",
    state_code: "",
    credit_limit: "0"
  };
  const [f, setF] = useState(blank);
  const load = useCallback(() => api(`/api/masters/${kind}`).then(setRows).catch(e => setErr(e.message)), [kind]);
  useEffect(() => {
    load();
    setLedger(null);
  }, [load]);
  const add = async e => {
    e.preventDefault();
    setErr("");
    const body = {
      code: f.code,
      name: f.name,
      gstin: f.gstin,
      state_code: f.state_code
    };
    if (isCust) body.credit_limit = f.credit_limit || "0";
    try {
      await api(`/api/masters/${kind}`, {
        method: "POST",
        body
      });
      setF(blank);
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const title = isCust ? "customer" : "supplier";
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
  })), isCust && /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Credit limit (\u20B9)"), /*#__PURE__*/React.createElement("input", {
    value: f.credit_limit,
    onChange: e => setF({
      ...f,
      credit_limit: e.target.value
    }),
    placeholder: "0 = unlimited",
    style: {
      width: 120
    }
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Add")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), ledger && /*#__PURE__*/React.createElement(PartyLedger, {
    kind: kind,
    party: ledger,
    onClose: () => setLedger(null)
  }), /*#__PURE__*/React.createElement(Panel, {
    title: title.charAt(0).toUpperCase() + title.slice(1) + "s"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: rows || [],
    pageSize: 12,
    empty: rows ? "None yet." : "Loading…",
    searchKeys: ["code", "name", "gstin", "state_code"],
    columns: [{
      key: "code",
      label: "Code"
    }, {
      key: "name",
      label: "Name"
    }, {
      key: "gstin",
      label: "GSTIN",
      render: r => r.gstin || "-"
    }, {
      key: "state_code",
      label: "State",
      render: r => r.state_code || "-"
    }, ...(isCust ? [{
      key: "credit_limit",
      label: "Credit limit",
      num: true,
      render: r => Number(r.credit_limit) > 0 ? inr2(r.credit_limit) : "—"
    }] : []), {
      key: "act",
      label: "",
      sortable: false,
      num: true,
      render: r => /*#__PURE__*/React.createElement("button", {
        className: "btn sec",
        onClick: () => setLedger({
          id: r.id,
          name: r.name
        })
      }, "Ledger")
    }]
  })));
}

// ---- pre-sales: enquiries, quotations, statements -------------------------
function SalesPipeline() {
  const customers = useList("/api/masters/customers");
  const fabrics = useList("/api/masters/fabric-types");
  const taxes = useList("/api/masters/tax-codes");
  const [enqs, setEnqs] = useState([]);
  const [quotes, setQuotes] = useState([]);
  const [ef, setEf] = useState({
    customer_id: "",
    number: genNo("ENQ"),
    description: ""
  });
  const [qf, setQf] = useState({
    customer_id: "",
    number: genNo("QT"),
    fabric_type_id: "",
    qty_kg: "100",
    rate: "300",
    tax_code_id: ""
  });
  const [stmtCust, setStmtCust] = useState("");
  const [stmt, setStmt] = useState(null);
  const [eRes, setERes] = useState(null);
  const [qRes, setQRes] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api("/api/sales/enquiries").then(setEnqs).catch(() => {});
    api("/api/sales/quotations").then(setQuotes).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const go = async fn => {
    setErr("");
    try {
      await fn();
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const addEnq = e => {
    e.preventDefault();
    setERes(null);
    go(async () => {
      setERes(await api("/api/sales/enquiries", {
        method: "POST",
        body: {
          customer_id: ef.customer_id,
          number: ef.number,
          enquiry_date: today(),
          description: ef.description || null
        }
      }));
      setEf({
        ...ef,
        number: genNo("ENQ"),
        description: ""
      });
    });
  };
  const addQuote = e => {
    e.preventDefault();
    setQRes(null);
    go(async () => {
      setQRes(await api("/api/sales/quotations", {
        method: "POST",
        body: {
          customer_id: qf.customer_id,
          number: qf.number,
          quote_date: today(),
          lines: [{
            fabric_type_id: qf.fabric_type_id || null,
            qty_kg: qf.qty_kg,
            rate: qf.rate,
            tax_code_id: qf.tax_code_id || null
          }]
        }
      }));
      setQf({
        ...qf,
        number: genNo("QT")
      });
    });
  };
  const convert = id => go(async () => {
    await api(`/api/sales/quotations/${id}/convert`, {
      method: "POST",
      body: {
        number: genNo("SO"),
        order_date: today()
      }
    });
  });
  const loadStmt = async id => {
    setStmtCust(id);
    setStmt(null);
    if (!id) return;
    try {
      setStmt(await api(`/api/reports/customer-statement?customer_id=${id}`));
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 16,
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "New enquiry"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: addEnq
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: ef.customer_id,
    onChange: e => setEf({
      ...ef,
      customer_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Number"), /*#__PURE__*/React.createElement("input", {
    value: ef.number,
    onChange: e => setEf({
      ...ef,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Requirement"), /*#__PURE__*/React.createElement("input", {
    value: ef.description,
    onChange: e => setEf({
      ...ef,
      description: e.target.value
    }),
    placeholder: "e.g. 500 kg navy single jersey"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Log enquiry"))), /*#__PURE__*/React.createElement(Result, {
    r: eRes
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 2
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "New quotation"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: addQuote
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: qf.customer_id,
    onChange: e => setQf({
      ...qf,
      customer_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Number"), /*#__PURE__*/React.createElement("input", {
    value: qf.number,
    onChange: e => setQf({
      ...qf,
      number: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fabric"), /*#__PURE__*/React.createElement(Opts, {
    items: fabrics,
    value: qf.fabric_type_id,
    onChange: e => setQf({
      ...qf,
      fabric_type_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty (kg)"), /*#__PURE__*/React.createElement("input", {
    value: qf.qty_kg,
    onChange: e => setQf({
      ...qf,
      qty_kg: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Rate"), /*#__PURE__*/React.createElement("input", {
    value: qf.rate,
    onChange: e => setQf({
      ...qf,
      rate: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Tax"), /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: qf.tax_code_id,
    onChange: e => setQf({
      ...qf,
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
  }, "Create quote")))), /*#__PURE__*/React.createElement(Result, {
    r: qRes
  })))), /*#__PURE__*/React.createElement(Panel, {
    title: "Quotations"
  }, quotes.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "None yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Number"), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Value"), /*#__PURE__*/React.createElement("th", null, "Status"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, quotes.map(q => /*#__PURE__*/React.createElement("tr", {
    key: q.id
  }, /*#__PURE__*/React.createElement("td", null, q.number), /*#__PURE__*/React.createElement("td", null, q.date), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(q.value)), /*#__PURE__*/React.createElement("td", null, stBadge(q.status)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, q.status !== "COMPLETED" && q.status !== "CANCELLED" && /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: () => convert(q.id)
  }, "Convert to SO"), q.status === "COMPLETED" && /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2192 SO created"))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Enquiries"
  }, enqs.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "None yet.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Number"), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Requirement"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, enqs.map(x => /*#__PURE__*/React.createElement("tr", {
    key: x.id
  }, /*#__PURE__*/React.createElement("td", null, x.number), /*#__PURE__*/React.createElement("td", null, x.date), /*#__PURE__*/React.createElement("td", null, x.description || "—"), /*#__PURE__*/React.createElement("td", null, stBadge(x.status))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Customer statement"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: stmtCust,
    onChange: e => loadStmt(e.target.value)
  }))), stmt && /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Voucher"), /*#__PURE__*/React.createElement("th", null, "Narration"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Debit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Credit"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Balance"))), /*#__PURE__*/React.createElement("tbody", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "5"
  }, /*#__PURE__*/React.createElement("b", null, "Opening balance")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("b", null, inr2(stmt.opening)))), stmt.rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, r.date), /*#__PURE__*/React.createElement("td", null, r.voucher), /*#__PURE__*/React.createElement("td", null, r.narration || "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, Number(r.debit) ? inr2(r.debit) : ""), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, Number(r.credit) ? inr2(r.credit) : ""), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.balance))))), /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    colSpan: "5"
  }, "Closing balance"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(stmt.closing)))))), err && /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)));
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
  }))), /*#__PURE__*/React.createElement(LineItems, {
    rows: lines,
    setRows: setLines,
    newRow: () => ({
      chemical_id: "",
      warehouse_id: "",
      qty: "",
      unit_cost: "",
      tax_code_id: ""
    }),
    amount: l => Number(l.qty || 0) * Number(l.unit_cost || 0),
    amountLabel: "Goods value (before GST)",
    note: "Input GST and payable are computed by the backend on posting.",
    columns: [{
      key: "chemical_id",
      label: "Chemical",
      type: "opts",
      items: chemicals
    }, {
      key: "warehouse_id",
      label: "Warehouse",
      type: "opts",
      items: warehouses
    }, {
      key: "qty",
      label: "Qty",
      num: true,
      width: 90
    }, {
      key: "unit_cost",
      label: "Unit cost",
      num: true,
      width: 100
    }, {
      key: "tax_code_id",
      label: "Tax",
      type: "opts",
      items: taxes
    }]
  }), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement("button", {
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
function EInvoiceBox({
  invoiceId,
  total
}) {
  const [cfg, setCfg] = useState(null);
  const [ei, setEi] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/api/einvoice/config").then(setCfg).catch(() => {});
    api("/api/einvoice/" + invoiceId).then(setEi).catch(() => setEi(null));
  }, [invoiceId]);
  const gen = async () => {
    setErr("");
    setBusy(true);
    try {
      setEi(await api("/api/einvoice/" + invoiceId + "/generate", {
        method: "POST"
      }));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    setErr("");
    setBusy(true);
    try {
      setEi(await api("/api/einvoice/" + invoiceId + "/cancel", {
        method: "POST",
        body: {
          reason_code: "2",
          remarks: "Cancelled from UI"
        }
      }));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  };
  const st = ei && ei.status;
  return /*#__PURE__*/React.createElement("div", {
    className: "panel",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement("h3", null, "E-invoice (IRN)"), /*#__PURE__*/React.createElement("div", {
    className: "row mb",
    style: {
      alignItems: "center",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12.5
    }
  }, "Mode:"), /*#__PURE__*/React.createElement("span", {
    className: "badge " + (cfg && cfg.live ? "info" : "")
  }, cfg ? cfg.live ? "LIVE GSP" : "SIMULATOR" : "…"), cfg && cfg.live && !cfg.configured && /*#__PURE__*/React.createElement("span", {
    className: "badge bad"
  }, "credentials missing"), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }), st !== "GENERATED" && /*#__PURE__*/React.createElement("button", {
    className: "btn",
    disabled: busy,
    onClick: gen
  }, "Generate IRN"), st === "GENERATED" && /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    disabled: busy,
    onClick: cancel
  }, "Cancel IRN")), ei && ei.irn && /*#__PURE__*/React.createElement("div", {
    className: "act",
    style: {
      flexDirection: "column",
      alignItems: "flex-start",
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("b", null, "Status:"), " ", /*#__PURE__*/React.createElement("span", {
    className: "badge " + (st === "GENERATED" ? "ok" : st === "CANCELLED" ? "bad" : "warn")
  }, st)), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "monospace",
      fontSize: 12,
      wordBreak: "break-all"
    }
  }, /*#__PURE__*/React.createElement("b", null, "IRN:"), " ", ei.irn), ei.ack_no && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12.5
    }
  }, /*#__PURE__*/React.createElement("b", null, "Ack:"), " ", ei.ack_no, " \xB7 ", ei.ack_date), ei.signed_qr && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "#64748b",
      wordBreak: "break-all"
    }
  }, /*#__PURE__*/React.createElement("b", null, "Signed QR:"), " ", ei.signed_qr.slice(0, 80), "\u2026")), !ei && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12.5
    }
  }, "No IRN yet for this invoice."), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err));
}
function EWayBillBox({
  invoiceId
}) {
  const [cfg, setCfg] = useState(null);
  const [eb, setEb] = useState(null);
  const [t, setT] = useState({
    trans_mode: "1",
    vehicle_no: "",
    distance_km: "",
    transporter_id: ""
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/api/ewaybill/config").then(setCfg).catch(() => {});
    api("/api/ewaybill/" + invoiceId).then(setEb).catch(() => setEb(null));
  }, [invoiceId]);
  const gen = async () => {
    setErr("");
    setBusy(true);
    try {
      setEb(await api("/api/ewaybill/" + invoiceId + "/generate", {
        method: "POST",
        body: {
          trans_mode: t.trans_mode,
          vehicle_no: t.vehicle_no || null,
          distance_km: Number(t.distance_km || 0),
          transporter_id: t.transporter_id || null
        }
      }));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    setErr("");
    setBusy(true);
    try {
      setEb(await api("/api/ewaybill/" + invoiceId + "/cancel", {
        method: "POST",
        body: {
          reason_code: "2",
          remarks: "Cancelled from UI"
        }
      }));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  };
  const st = eb && eb.status;
  return /*#__PURE__*/React.createElement("div", {
    className: "panel",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement("h3", null, "E-way bill"), /*#__PURE__*/React.createElement("div", {
    className: "row mb",
    style: {
      alignItems: "center",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12.5
    }
  }, "Mode:"), /*#__PURE__*/React.createElement("span", {
    className: "badge " + (cfg && cfg.live ? "info" : "")
  }, cfg ? cfg.live ? "LIVE GSP" : "SIMULATOR" : "…")), st !== "GENERATED" && /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Transport mode"), /*#__PURE__*/React.createElement("select", {
    value: t.trans_mode,
    onChange: e => setT({
      ...t,
      trans_mode: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "1"
  }, "Road"), /*#__PURE__*/React.createElement("option", {
    value: "2"
  }, "Rail"), /*#__PURE__*/React.createElement("option", {
    value: "3"
  }, "Air"), /*#__PURE__*/React.createElement("option", {
    value: "4"
  }, "Ship"))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Vehicle no."), /*#__PURE__*/React.createElement("input", {
    value: t.vehicle_no,
    onChange: e => setT({
      ...t,
      vehicle_no: e.target.value
    }),
    placeholder: "PB10AB1234"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Distance (km)"), /*#__PURE__*/React.createElement("input", {
    value: t.distance_km,
    onChange: e => setT({
      ...t,
      distance_km: e.target.value
    }),
    placeholder: "120"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Transporter ID"), /*#__PURE__*/React.createElement("input", {
    value: t.transporter_id,
    onChange: e => setT({
      ...t,
      transporter_id: e.target.value
    }),
    placeholder: "optional GSTIN"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn",
    disabled: busy,
    onClick: gen
  }, "Generate EWB"))), eb && eb.ewb_no && /*#__PURE__*/React.createElement("div", {
    className: "act",
    style: {
      flexDirection: "column",
      alignItems: "flex-start",
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("b", null, "Status:"), " ", /*#__PURE__*/React.createElement("span", {
    className: "badge " + (st === "GENERATED" ? "ok" : st === "CANCELLED" ? "bad" : "warn")
  }, st), st === "GENERATED" && /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    style: {
      marginLeft: 10
    },
    disabled: busy,
    onClick: cancel
  }, "Cancel EWB")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: "monospace",
      fontSize: 13
    }
  }, /*#__PURE__*/React.createElement("b", null, "EWB No:"), " ", eb.ewb_no), eb.valid_until && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12.5
    }
  }, /*#__PURE__*/React.createElement("b", null, "Valid until:"), " ", eb.valid_until), eb.irn && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: "#64748b"
    }
  }, "linked to IRN ", eb.irn.slice(0, 16), "\u2026")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err));
}
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
  }))), /*#__PURE__*/React.createElement(LineItems, {
    rows: lines,
    setRows: setLines,
    newRow: () => ({
      description: "",
      qty: "",
      rate: "",
      tax_code_id: ""
    }),
    amount: l => Number(l.qty || 0) * Number(l.rate || 0),
    note: "GST and invoice total are computed by the backend on posting.",
    columns: [{
      key: "description",
      label: "Description",
      width: 260,
      placeholder: "Dyed fabric"
    }, {
      key: "qty",
      label: "Qty",
      num: true,
      width: 90
    }, {
      key: "rate",
      label: "Rate",
      num: true,
      width: 90
    }, {
      key: "tax_code_id",
      label: "Tax",
      type: "opts",
      items: taxes
    }]
  }), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post invoice")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  }), res && res.invoice_id && /*#__PURE__*/React.createElement(EInvoiceBox, {
    invoiceId: res.invoice_id,
    total: res.total_value
  }), res && res.invoice_id && /*#__PURE__*/React.createElement(EWayBillBox, {
    invoiceId: res.invoice_id
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
  const accounts = useList("/api/accounts");
  const [cc, setCc] = useState(null);
  const [pools, setPools] = useState([]);
  const [pf, setPf] = useState({
    code: genNo("OH"),
    name: "Electricity",
    source_account_code: "5070",
    basis: "OUTPUT_KG"
  });
  const [af, setAf] = useState({
    pool_id: "",
    month: today().slice(0, 7),
    amount: "10000"
  });
  const [pRes, setPRes] = useState(null);
  const [aRes, setARes] = useState(null);
  const [ohErr, setOhErr] = useState("");
  const load = useCallback(() => {
    api("/api/reports/cost-centres").then(setCc).catch(() => {});
    api("/api/accounting/overhead/pools").then(setPools).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const expAccts = accounts.filter(a => a.code && (a.code[0] === "5" || a.code[0] === "6") && a.allow_posting).map(a => ({
    id: a.code,
    name: a.code + " — " + a.name
  }));
  const makePool = async e => {
    e.preventDefault();
    setOhErr("");
    setPRes(null);
    try {
      const r = await api("/api/accounting/overhead/pools", {
        method: "POST",
        body: pf
      });
      setPRes(r);
      setPf({
        ...pf,
        code: genNo("OH")
      });
      load();
    } catch (ex) {
      setOhErr(ex.message);
    }
  };
  const runAlloc = async e => {
    e.preventDefault();
    setOhErr("");
    setARes(null);
    if (!af.pool_id) {
      setOhErr("Pick a pool");
      return;
    }
    try {
      setARes(await api("/api/accounting/overhead/allocate", {
        method: "POST",
        body: {
          pool_id: af.pool_id,
          period_month: af.month + "-01",
          amount: af.amount || null
        }
      }));
      load();
    } catch (ex) {
      setOhErr(ex.message);
    }
  };
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
  }, inr2(m.cost))))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Overhead allocation (absorb indirect cost into work centres)"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 16,
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: makePool
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Pool code"), /*#__PURE__*/React.createElement("input", {
    value: pf.code,
    onChange: e => setPf({
      ...pf,
      code: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    value: pf.name,
    onChange: e => setPf({
      ...pf,
      name: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Source (expense) account"), /*#__PURE__*/React.createElement(Opts, {
    items: expAccts,
    value: pf.source_account_code,
    onChange: e => setPf({
      ...pf,
      source_account_code: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Basis"), /*#__PURE__*/React.createElement("select", {
    value: pf.basis,
    onChange: e => setPf({
      ...pf,
      basis: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "OUTPUT_KG"
  }, "Output kg"), /*#__PURE__*/React.createElement("option", {
    value: "MACHINE_HOURS"
  }, "Machine hours"), /*#__PURE__*/React.createElement("option", {
    value: "EQUAL"
  }, "Equal split"))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Create pool"))), /*#__PURE__*/React.createElement(Result, {
    r: pRes
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: runAlloc
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Pool"), /*#__PURE__*/React.createElement(Opts, {
    items: pools.map(p => ({
      id: p.id,
      name: `${p.code} — ${p.name} (${p.basis})`
    })),
    value: af.pool_id,
    onChange: e => setAf({
      ...af,
      pool_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Period (month)"), /*#__PURE__*/React.createElement("input", {
    type: "month",
    value: af.month,
    onChange: e => setAf({
      ...af,
      month: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Amount (blank = absorb account balance)"), /*#__PURE__*/React.createElement("input", {
    value: af.amount,
    onChange: e => setAf({
      ...af,
      amount: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Run allocation"))), /*#__PURE__*/React.createElement(Result, {
    r: aRes
  }))), ohErr && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, ohErr)), /*#__PURE__*/React.createElement(Panel, {
    title: "Work-centre cost (from the ledger)"
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginBottom: 8,
      fontSize: 12
    }
  }, "Each machine is a work centre; production postings tag its cost centre, which rolls up to the department."), !cc || cc.centres.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No cost-centre postings yet.") : /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 16,
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Work centre"), /*#__PURE__*/React.createElement("th", null, "Type"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Net cost"))), /*#__PURE__*/React.createElement("tbody", null, cc.centres.map((c, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, c.code, " \u2014 ", c.name), /*#__PURE__*/React.createElement("td", null, c.type), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(c.net_cost))))))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Department"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Net cost"))), /*#__PURE__*/React.createElement("tbody", null, cc.departments.map((d, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, d.code, " \u2014 ", d.name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(d.net_cost))))), /*#__PURE__*/React.createElement("tfoot", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Total"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, inr2(cc.total_net_cost)))))))));
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
  const buildBody = () => ({
    ...h,
    lines: lines.filter(l => l.account_code && (num(l.debit) || num(l.credit))).map(l => ({
      account_code: l.account_code,
      debit: l.debit || "0",
      credit: l.credit || "0",
      narration: l.narration || null
    }))
  });
  const submitApproval = async () => {
    setErr("");
    setRes(null);
    try {
      const r = await api("/api/vouchers/submit-approval", {
        method: "POST",
        body: buildBody()
      });
      setRes(r.approval_required ? {
        status: "HELD",
        approval: "Routed for approval (" + r.approvals_required + " needed)"
      } : {
        voucher: r.voucher_number,
        status: "POSTED",
        note: "Below threshold — posted directly"
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
  }))), /*#__PURE__*/React.createElement(LineItems, {
    rows: lines,
    setRows: setLines,
    minRows: 2,
    newRow: () => ({
      account_code: "",
      debit: "",
      credit: "",
      narration: ""
    }),
    columns: [{
      key: "account_code",
      label: "Account",
      type: "opts",
      items: acctOpts
    }, {
      key: "debit",
      label: "Debit",
      num: true,
      width: 100
    }, {
      key: "credit",
      label: "Credit",
      num: true,
      width: 100
    }, {
      key: "narration",
      label: "Narration",
      width: 150
    }],
    footer: () => /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Totals"), /*#__PURE__*/React.createElement("th", {
      className: "num"
    }, inr2(totDr)), /*#__PURE__*/React.createElement("th", {
      className: "num"
    }, inr2(totCr)), /*#__PURE__*/React.createElement("th", {
      colSpan: "2"
    }, /*#__PURE__*/React.createElement("span", {
      className: "badge " + (balanced ? "ok" : "bad")
    }, balanced ? "Balanced" : "Dr ≠ Cr")))
  }), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "btn sec",
    disabled: !balanced,
    onClick: submitApproval
  }, "Submit for approval"), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post voucher")), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Recent vouchers"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: recent,
    pageSize: 10,
    empty: "None yet.",
    initialSort: {
      key: "voucher_date",
      dir: "desc"
    },
    searchKeys: ["number", "status", "narration"],
    columns: [{
      key: "number",
      label: "Number"
    }, {
      key: "voucher_date",
      label: "Date"
    }, {
      key: "status",
      label: "Status",
      render: v => /*#__PURE__*/React.createElement("span", {
        className: "badge " + (v.status === "POSTED" ? "ok" : v.status === "REVERSED" ? "warn" : "")
      }, v.status)
    }, {
      key: "total_debit",
      label: "Debit",
      num: true,
      render: v => inr2(v.total_debit)
    }, {
      key: "narration",
      label: "Narration",
      render: v => v.narration || "-"
    }]
  })));
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
  const [dn, setDn] = useState({
    supplier_id: "",
    number: genNo("DN"),
    chemical_id: "",
    qty: "10",
    tax_code_id: "",
    reason: ""
  });
  const [dnRes, setDnRes] = useState(null);
  const [dnErr, setDnErr] = useState("");
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
  const submitApproval = async id => {
    setErr("");
    setRes(null);
    try {
      const r = await api(`/api/procurement/purchase-orders/${id}/submit-approval`, {
        method: "POST"
      });
      setRes(r.approval_required ? {
        status: r.status,
        approval: "Routed for approval (" + r.approvals_required + " needed)"
      } : {
        status: r.status,
        note: "Below threshold — confirmed directly"
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
  const createDN = async e => {
    e.preventDefault();
    setDnErr("");
    setDnRes(null);
    try {
      const r = await api("/api/purchase/debit-notes", {
        method: "POST",
        body: {
          supplier_id: dn.supplier_id,
          number: dn.number,
          note_date: today(),
          reason: dn.reason || null,
          lines: [{
            chemical_id: dn.chemical_id,
            qty: dn.qty,
            tax_code_id: dn.tax_code_id || null
          }]
        }
      });
      setDnRes(r);
      setDn({
        ...dn,
        number: genNo("DN")
      });
    } catch (ex) {
      setDnErr(ex.message);
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
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: pos,
    pageSize: 10,
    initialSort: {
      key: "order_date",
      dir: "desc"
    },
    empty: "None yet. GRNs raised against a confirmed PO roll it to PARTIAL / COMPLETED.",
    searchKeys: ["number", "order_date", "status"],
    expand: p => /*#__PURE__*/React.createElement(PODetail, {
      id: p.id,
      names: Object.fromEntries((chemicals || []).map(c => [c.id, c.name || c.label || c.code]))
    }),
    columns: [{
      key: "number",
      label: "Number"
    }, {
      key: "order_date",
      label: "Date"
    }, {
      key: "lines",
      label: "Lines",
      num: true
    }, {
      key: "status",
      label: "Status",
      render: p => stBadge(p.status)
    }, {
      key: "act",
      label: "",
      sortable: false,
      num: true,
      render: p => /*#__PURE__*/React.createElement(React.Fragment, null, p.status === "DRAFT" && /*#__PURE__*/React.createElement("button", {
        className: "btn sec",
        onClick: () => submitApproval(p.id)
      }, "Submit for approval"), " ", p.status === "DRAFT" && /*#__PURE__*/React.createElement("button", {
        className: "btn",
        onClick: () => confirm(p.id)
      }, "Confirm"))
    }]
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Debit note (return chemicals to supplier)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: createDN
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Supplier"), /*#__PURE__*/React.createElement(Opts, {
    items: suppliers,
    value: dn.supplier_id,
    onChange: e => setDn({
      ...dn,
      supplier_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "DN number"), /*#__PURE__*/React.createElement("input", {
    value: dn.number,
    onChange: e => setDn({
      ...dn,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Chemical"), /*#__PURE__*/React.createElement(Opts, {
    items: chemicals,
    value: dn.chemical_id,
    onChange: e => setDn({
      ...dn,
      chemical_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty returned"), /*#__PURE__*/React.createElement("input", {
    value: dn.qty,
    onChange: e => setDn({
      ...dn,
      qty: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Tax"), /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: dn.tax_code_id,
    onChange: e => setDn({
      ...dn,
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
  }, "Post debit note"))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", null, "Reason"), /*#__PURE__*/React.createElement("input", {
    value: dn.reason,
    onChange: e => setDn({
      ...dn,
      reason: e.target.value
    }),
    placeholder: "e.g. damaged goods"
  }))), dnErr && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, dnErr)), /*#__PURE__*/React.createElement(Result, {
    r: dnRes
  })));
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
  const [cn, setCn] = useState({
    customer_id: "",
    number: genNo("CN"),
    amount: "10000",
    tax_code_id: "",
    reason: ""
  });
  const [rret, setRret] = useState({
    customer_id: "",
    number: genNo("RET"),
    amount: "",
    tax_code_id: ""
  });
  const [rsel, setRsel] = useState({});
  const [delivered, setDelivered] = useState([]);
  const [res, setRes] = useState(null);
  const [dres, setDres] = useState(null);
  const [retRes, setRetRes] = useState(null);
  const [cnRes, setCnRes] = useState(null);
  const [cnErr, setCnErr] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api("/api/sales/orders").then(setSos).catch(() => {});
    api("/api/rolls?undelivered=true").then(setRolls).catch(() => {});
    api("/api/rolls").then(rs => setDelivered((rs || []).filter(r => r.delivered))).catch(() => {});
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
  const createCN = async e => {
    e.preventDefault();
    setCnErr("");
    setCnRes(null);
    try {
      const r = await api("/api/sales/credit-notes", {
        method: "POST",
        body: {
          customer_id: cn.customer_id,
          number: cn.number,
          note_date: today(),
          reason: cn.reason || null,
          lines: [{
            description: cn.reason || "Adjustment",
            qty: "1",
            rate: cn.amount,
            tax_code_id: cn.tax_code_id || null
          }]
        }
      });
      setCnRes(r);
      setCn({
        ...cn,
        number: genNo("CN")
      });
    } catch (ex) {
      setCnErr(ex.message);
    }
  };
  const retSel = Object.keys(rsel).filter(k => rsel[k]);
  const doReturn = async e => {
    e.preventDefault();
    setCnErr("");
    setRetRes(null);
    if (!rret.customer_id) {
      setCnErr("Pick the customer");
      return;
    }
    try {
      const r = await api("/api/sales/returns", {
        method: "POST",
        body: {
          customer_id: rret.customer_id,
          number: rret.number,
          note_date: today(),
          reason: "Sales return",
          restock_roll_ids: retSel,
          lines: [{
            description: "Sales return",
            qty: "1",
            rate: rret.amount || "0",
            tax_code_id: rret.tax_code_id || null
          }]
        }
      });
      setRetRes(r);
      setRret({
        ...rret,
        number: genNo("RET"),
        amount: ""
      });
      setRsel({});
      load();
    } catch (ex) {
      setCnErr(ex.message);
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
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: sos,
    pageSize: 10,
    empty: "None yet.",
    initialSort: {
      key: "order_date",
      dir: "desc"
    },
    searchKeys: ["number", "order_date", "status"],
    expand: s => /*#__PURE__*/React.createElement(SODetail, {
      id: s.id
    }),
    columns: [{
      key: "number",
      label: "Number"
    }, {
      key: "order_date",
      label: "Date"
    }, {
      key: "ordered_kg",
      label: "Ordered kg",
      num: true,
      render: s => s.ordered_kg ? inr2(s.ordered_kg) : "—"
    }, {
      key: "delivered_kg",
      label: "Delivered kg",
      num: true,
      render: s => s.delivered_kg ? inr2(s.delivered_kg) : "—"
    }, {
      key: "status",
      label: "Status",
      render: s => stBadge(s.status)
    }, {
      key: "act",
      label: "",
      sortable: false,
      num: true,
      render: s => s.status === "DRAFT" ? /*#__PURE__*/React.createElement("button", {
        className: "btn",
        onClick: () => confirm(s.id)
      }, "Confirm") : null
    }]
  })), /*#__PURE__*/React.createElement(Panel, {
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
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Credit note (customer adjustment)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: createCN
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: cn.customer_id,
    onChange: e => setCn({
      ...cn,
      customer_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "CN number"), /*#__PURE__*/React.createElement("input", {
    value: cn.number,
    onChange: e => setCn({
      ...cn,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Taxable amount"), /*#__PURE__*/React.createElement("input", {
    value: cn.amount,
    onChange: e => setCn({
      ...cn,
      amount: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Tax"), /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: cn.tax_code_id,
    onChange: e => setCn({
      ...cn,
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
  }, "Post credit note"))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", null, "Reason"), /*#__PURE__*/React.createElement("input", {
    value: cn.reason,
    onChange: e => setCn({
      ...cn,
      reason: e.target.value
    }),
    placeholder: "e.g. rate difference / short shipment"
  }))), cnErr && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, cnErr)), /*#__PURE__*/React.createElement(Result, {
    r: cnRes
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Sales return (credit note + restock)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: doReturn
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Customer"), /*#__PURE__*/React.createElement(Opts, {
    items: customers,
    value: rret.customer_id,
    onChange: e => setRret({
      ...rret,
      customer_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "RET number"), /*#__PURE__*/React.createElement("input", {
    value: rret.number,
    onChange: e => setRret({
      ...rret,
      number: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Credit amount (taxable)"), /*#__PURE__*/React.createElement("input", {
    value: rret.amount,
    onChange: e => setRret({
      ...rret,
      amount: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Tax"), /*#__PURE__*/React.createElement(Opts, {
    items: taxes,
    value: rret.tax_code_id,
    onChange: e => setRret({
      ...rret,
      tax_code_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Selected"), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "9px 0",
      fontWeight: 700
    }
  }, retSel.length, " rolls"))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginBottom: 6,
      fontSize: 12
    }
  }, "Tick returned rolls to take them back into stock and reverse their COGS."), /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    style: {
      width: 40
    }
  }), /*#__PURE__*/React.createElement("th", null, "Roll no."), /*#__PURE__*/React.createElement("th", null, "Fabric"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Weight (kg)"))), /*#__PURE__*/React.createElement("tbody", null, delivered.length === 0 ? /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "4",
    className: "muted"
  }, "No delivered rolls to return.")) : delivered.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!rsel[r.id],
    onChange: () => setRsel(s => ({
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
  }, "Post sales return"))), /*#__PURE__*/React.createElement(Result, {
    r: retRes
  })));
}

// ---- fixed assets & depreciation ------------------------------------------
function FixedAssets() {
  const accounts = useList("/api/accounts");
  const [assets, setAssets] = useState([]);
  const [res, setRes] = useState(null);
  const [dep, setDep] = useState(null);
  const [err, setErr] = useState("");
  const [month, setMonth] = useState(today().slice(0, 7));
  const [f, setF] = useState({
    code: genNo("FA"),
    name: "",
    category: "",
    asset_account_code: "",
    acquisition_date: today(),
    cost: "120000",
    salvage_value: "0",
    method: "SLM",
    useful_life_months: "60",
    dep_rate_pct: "15"
  });
  const load = useCallback(() => {
    api("/api/assets").then(setAssets).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const assetAccts = accounts.filter(a => a.code && a.code.indexOf("15") === 0 && a.allow_posting && a.code !== "1590").map(a => ({
    id: a.code,
    name: a.code + " — " + a.name
  }));
  const create = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    const body = {
      code: f.code,
      name: f.name,
      category: f.category || null,
      asset_account_code: f.asset_account_code,
      acquisition_date: f.acquisition_date,
      cost: f.cost,
      salvage_value: f.salvage_value,
      method: f.method
    };
    if (f.method === "SLM") body.useful_life_months = Number(f.useful_life_months);else body.dep_rate_pct = f.dep_rate_pct;
    try {
      const r = await api("/api/assets", {
        method: "POST",
        body
      });
      setRes({
        asset: r.code,
        book_value: r.book_value
      });
      setF({
        ...f,
        code: genNo("FA"),
        name: ""
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const runDep = async () => {
    setErr("");
    setDep(null);
    try {
      setDep(await api("/api/assets/depreciation-runs", {
        method: "POST",
        body: {
          period_month: month + "-01"
        }
      }));
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const dispose = async a => {
    const proceeds = prompt(`Dispose ${a.code} — sale proceeds (₹)?`, "0");
    if (proceeds === null) return;
    try {
      await api(`/api/assets/${a.id}/dispose`, {
        method: "POST",
        body: {
          disposal_date: today(),
          proceeds: proceeds || "0"
        }
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Register (capitalise) a fixed asset"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: create
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Code"), /*#__PURE__*/React.createElement("input", {
    value: f.code,
    onChange: e => setF({
      ...f,
      code: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    value: f.name,
    onChange: e => setF({
      ...f,
      name: e.target.value
    }),
    required: true
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Category"), /*#__PURE__*/React.createElement("input", {
    value: f.category,
    onChange: e => setF({
      ...f,
      category: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Asset account"), /*#__PURE__*/React.createElement(Opts, {
    items: assetAccts,
    value: f.asset_account_code,
    onChange: e => setF({
      ...f,
      asset_account_code: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Acquired"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: f.acquisition_date,
    onChange: e => setF({
      ...f,
      acquisition_date: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Cost"), /*#__PURE__*/React.createElement("input", {
    value: f.cost,
    onChange: e => setF({
      ...f,
      cost: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Salvage"), /*#__PURE__*/React.createElement("input", {
    value: f.salvage_value,
    onChange: e => setF({
      ...f,
      salvage_value: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Method"), /*#__PURE__*/React.createElement("select", {
    value: f.method,
    onChange: e => setF({
      ...f,
      method: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "SLM"
  }, "Straight-line"), /*#__PURE__*/React.createElement("option", {
    value: "WDV"
  }, "Written-down value"))), f.method === "SLM" ? /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Life (months)"), /*#__PURE__*/React.createElement("input", {
    value: f.useful_life_months,
    onChange: e => setF({
      ...f,
      useful_life_months: e.target.value
    })
  })) : /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Rate (% / yr)"), /*#__PURE__*/React.createElement("input", {
    value: f.dep_rate_pct,
    onChange: e => setF({
      ...f,
      dep_rate_pct: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Capitalise"))), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Run monthly depreciation"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Period (month)"), /*#__PURE__*/React.createElement("input", {
    type: "month",
    value: month,
    onChange: e => setMonth(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: runDep
  }, "Run depreciation"))), /*#__PURE__*/React.createElement(Result, {
    r: dep
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Asset register"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: assets,
    pageSize: 12,
    empty: "None yet.",
    searchKeys: ["code", "name", "method", "status"],
    columns: [{
      key: "code",
      label: "Code"
    }, {
      key: "name",
      label: "Name"
    }, {
      key: "method",
      label: "Method"
    }, {
      key: "cost",
      label: "Cost",
      num: true,
      render: a => inr2(a.cost)
    }, {
      key: "accumulated_depreciation",
      label: "Accum. dep.",
      num: true,
      render: a => inr2(a.accumulated_depreciation)
    }, {
      key: "book_value",
      label: "Book value",
      num: true,
      render: a => inr2(a.book_value)
    }, {
      key: "status",
      label: "Status",
      render: a => stBadge(a.status)
    }, {
      key: "act",
      label: "",
      sortable: false,
      num: true,
      render: a => a.status === "ACTIVE" ? /*#__PURE__*/React.createElement("button", {
        className: "btn sec",
        onClick: () => dispose(a)
      }, "Dispose") : null
    }]
  })));
}

// ---- payroll --------------------------------------------------------------
function Payroll() {
  const [emps, setEmps] = useState([]);
  const [structs, setStructs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [month, setMonth] = useState(today().slice(0, 7));
  const [ef, setEf] = useState({
    code: genNo("EMP"),
    name: "",
    designation: ""
  });
  const [sf, setSf] = useState({
    employee_id: "",
    basic: "20000",
    hra: "8000",
    other_allowances: "2000",
    pf_applicable: true,
    esi_applicable: false,
    professional_tax: "200",
    tds_monthly: "1000"
  });
  const [eRes, setERes] = useState(null);
  const [sRes, setSRes] = useState(null);
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api("/api/payroll/employees").then(setEmps).catch(() => {});
    api("/api/payroll/salary-structures").then(setStructs).catch(() => {});
    api("/api/payroll/runs").then(setRuns).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const addEmp = async e => {
    e.preventDefault();
    setErr("");
    setERes(null);
    try {
      const r = await api("/api/payroll/employees", {
        method: "POST",
        body: ef
      });
      setERes(r);
      setEf({
        code: genNo("EMP"),
        name: "",
        designation: ""
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const setStruct = async e => {
    e.preventDefault();
    setErr("");
    setSRes(null);
    try {
      const r = await api("/api/payroll/salary-structures", {
        method: "POST",
        body: {
          ...sf,
          effective_from: month + "-01"
        }
      });
      setSRes(r);
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const runPay = async () => {
    setErr("");
    setRun(null);
    try {
      setRun(await api("/api/payroll/runs", {
        method: "POST",
        body: {
          period_month: month + "-01"
        }
      }));
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 16,
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Add employee"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: addEmp
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Code"), /*#__PURE__*/React.createElement("input", {
    value: ef.code,
    onChange: e => setEf({
      ...ef,
      code: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    value: ef.name,
    onChange: e => setEf({
      ...ef,
      name: e.target.value
    }),
    required: true
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Designation"), /*#__PURE__*/React.createElement("input", {
    value: ef.designation,
    onChange: e => setEf({
      ...ef,
      designation: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Add employee"))), /*#__PURE__*/React.createElement(Result, {
    r: eRes
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 2
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Set salary structure"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: setStruct
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Employee"), /*#__PURE__*/React.createElement(Opts, {
    items: emps,
    value: sf.employee_id,
    onChange: e => setSf({
      ...sf,
      employee_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Basic"), /*#__PURE__*/React.createElement("input", {
    value: sf.basic,
    onChange: e => setSf({
      ...sf,
      basic: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "HRA"), /*#__PURE__*/React.createElement("input", {
    value: sf.hra,
    onChange: e => setSf({
      ...sf,
      hra: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Allowances"), /*#__PURE__*/React.createElement("input", {
    value: sf.other_allowances,
    onChange: e => setSf({
      ...sf,
      other_allowances: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Prof. tax"), /*#__PURE__*/React.createElement("input", {
    value: sf.professional_tax,
    onChange: e => setSf({
      ...sf,
      professional_tax: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "TDS / month"), /*#__PURE__*/React.createElement("input", {
    value: sf.tds_monthly,
    onChange: e => setSf({
      ...sf,
      tds_monthly: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "PF?"), /*#__PURE__*/React.createElement("select", {
    value: sf.pf_applicable ? "y" : "n",
    onChange: e => setSf({
      ...sf,
      pf_applicable: e.target.value === "y"
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "y"
  }, "Yes"), /*#__PURE__*/React.createElement("option", {
    value: "n"
  }, "No"))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "ESI?"), /*#__PURE__*/React.createElement("select", {
    value: sf.esi_applicable ? "y" : "n",
    onChange: e => setSf({
      ...sf,
      esi_applicable: e.target.value === "y"
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "n"
  }, "No"), /*#__PURE__*/React.createElement("option", {
    value: "y"
  }, "Yes"))), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Save structure")))), /*#__PURE__*/React.createElement(Result, {
    r: sRes
  })))), /*#__PURE__*/React.createElement(Panel, {
    title: "Salary structures"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: structs.map(s => ({
      ...s,
      employee_disp: (s.employee_code ? s.employee_code + " — " : "") + s.employee
    })),
    pageSize: 12,
    empty: "None yet \u2014 add an employee and set a structure.",
    searchKeys: ["employee_disp"],
    columns: [{
      key: "employee_disp",
      label: "Employee"
    }, {
      key: "basic",
      label: "Basic",
      num: true,
      render: s => inr2(s.basic)
    }, {
      key: "gross",
      label: "Gross",
      num: true,
      render: s => inr2(s.gross)
    }, {
      key: "deductions",
      label: "Deductions",
      num: true,
      render: s => inr2(s.deductions)
    }, {
      key: "net_pay",
      label: "Net pay",
      num: true,
      render: s => inr2(s.net_pay)
    }]
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Run monthly payroll"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Pay month"), /*#__PURE__*/React.createElement("input", {
    type: "month",
    value: month,
    onChange: e => setMonth(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: runPay
  }, "Run payroll"))), /*#__PURE__*/React.createElement(Result, {
    r: run
  }), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Panel, {
    title: "Payroll runs"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: runs,
    pageSize: 12,
    empty: "None yet.",
    initialSort: {
      key: "period",
      dir: "desc"
    },
    searchKeys: ["period", "status"],
    columns: [{
      key: "period",
      label: "Period"
    }, {
      key: "employees",
      label: "Employees",
      num: true
    }, {
      key: "total_gross",
      label: "Gross",
      num: true,
      render: r => inr2(r.total_gross)
    }, {
      key: "total_net",
      label: "Net paid",
      num: true,
      render: r => inr2(r.total_net)
    }, {
      key: "status",
      label: "Status",
      render: r => stBadge(r.status)
    }]
  })));
}

// ---- approvals (maker-checker) --------------------------------------------
function Approvals() {
  const suppliers = useList("/api/masters/suppliers");
  const banks = useList("/api/banking/accounts");
  const [rules, setRules] = useState([]);
  const [reqs, setReqs] = useState([]);
  const [rf, setRf] = useState({
    min_amount: "100000",
    approvals_required: "1"
  });
  const [pf, setPf] = useState({
    supplier_id: "",
    bank_account_id: "",
    amount: "150000",
    note: ""
  });
  const [rRes, setRRes] = useState(null);
  const [pRes, setPRes] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api("/api/approvals/rules").then(setRules).catch(() => {});
    api("/api/approvals/requests?status_filter=PENDING").then(setReqs).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const addRule = async e => {
    e.preventDefault();
    setErr("");
    setRRes(null);
    try {
      const r = await api("/api/approvals/rules", {
        method: "POST",
        body: {
          doc_type: "PAYMENT",
          min_amount: rf.min_amount,
          approvals_required: Number(rf.approvals_required)
        }
      });
      setRRes(r);
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const reqPay = async e => {
    e.preventDefault();
    setErr("");
    setPRes(null);
    try {
      const r = await api("/api/approvals/payments", {
        method: "POST",
        body: {
          supplier_id: pf.supplier_id,
          bank_account_id: pf.bank_account_id,
          amount: pf.amount,
          note: pf.note || null
        }
      });
      setPRes(r);
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const decide = async (id, action) => {
    setErr("");
    try {
      await api(`/api/approvals/requests/${id}/${action}`, {
        method: "POST",
        body: {}
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 16,
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Payment approval threshold"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: addRule
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Needs approval at or above (\u20B9)"), /*#__PURE__*/React.createElement("input", {
    value: rf.min_amount,
    onChange: e => setRf({
      ...rf,
      min_amount: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Approvals required"), /*#__PURE__*/React.createElement("input", {
    value: rf.approvals_required,
    onChange: e => setRf({
      ...rf,
      approvals_required: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Save rule"))), /*#__PURE__*/React.createElement(Result, {
    r: rRes
  }), rules.length > 0 && /*#__PURE__*/React.createElement("table", {
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Type"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Threshold"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Approvals"))), /*#__PURE__*/React.createElement("tbody", null, rules.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, r.doc_type), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.min_amount)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.approvals_required))))))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Request a supplier payment"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: reqPay
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Supplier"), /*#__PURE__*/React.createElement(Opts, {
    items: suppliers,
    value: pf.supplier_id,
    onChange: e => setPf({
      ...pf,
      supplier_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Bank account"), /*#__PURE__*/React.createElement(Opts, {
    items: banks,
    value: pf.bank_account_id,
    onChange: e => setPf({
      ...pf,
      bank_account_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Amount (\u20B9)"), /*#__PURE__*/React.createElement("input", {
    value: pf.amount,
    onChange: e => setPf({
      ...pf,
      amount: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Note"), /*#__PURE__*/React.createElement("input", {
    value: pf.note,
    onChange: e => setPf({
      ...pf,
      note: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Request payment"))), /*#__PURE__*/React.createElement(Result, {
    r: pRes
  })))), /*#__PURE__*/React.createElement(Panel, {
    title: "Pending approvals"
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginBottom: 8,
      fontSize: 12
    }
  }, "Maker-checker: the person who requested a payment cannot approve it \u2014 a different approver must sign off."), /*#__PURE__*/React.createElement(DataTable, {
    rows: reqs,
    pageSize: 10,
    empty: "Nothing awaiting approval.",
    searchKeys: ["title", "doc_type"],
    columns: [{
      key: "title",
      label: "Document"
    }, {
      key: "amount",
      label: "Amount",
      num: true,
      render: r => inr2(r.amount)
    }, {
      key: "approvals",
      label: "Approvals",
      num: true,
      sortable: false,
      render: r => `${r.approvals_received}/${r.approvals_required}`
    }, {
      key: "act",
      label: "",
      sortable: false,
      num: true,
      render: r => /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("button", {
        className: "btn",
        onClick: () => decide(r.id, "approve")
      }, "Approve"), " ", /*#__PURE__*/React.createElement("button", {
        className: "btn sec",
        onClick: () => decide(r.id, "reject")
      }, "Reject"))
    }]
  }), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)));
}

// ---- GST adjustments & year-end close -------------------------------------
function AccountingOps() {
  const [g, setG] = useState({
    kind: "itc-reversal",
    reference: genNo("GA"),
    cgst: "900",
    sgst: "900",
    igst: "0"
  });
  const [ro, setRo] = useState({
    reference: genNo("RO"),
    amount: "0.40"
  });
  const [fy, setFy] = useState("2026-2027");
  const [gRes, setGRes] = useState(null);
  const [roRes, setRoRes] = useState(null);
  const [yRes, setYRes] = useState(null);
  const [gErr, setGErr] = useState("");
  const [roErr, setRoErr] = useState("");
  const [yErr, setYErr] = useState("");
  const postGst = async e => {
    e.preventDefault();
    setGErr("");
    setGRes(null);
    try {
      const r = await api(`/api/accounting/gst-adjustments/${g.kind}`, {
        method: "POST",
        body: {
          vdate: today(),
          reference: g.reference,
          cgst: g.cgst || "0",
          sgst: g.sgst || "0",
          igst: g.igst || "0"
        }
      });
      setGRes(r);
      setG({
        ...g,
        reference: genNo("GA")
      });
    } catch (ex) {
      setGErr(ex.message);
    }
  };
  const postRound = async e => {
    e.preventDefault();
    setRoErr("");
    setRoRes(null);
    try {
      const r = await api("/api/accounting/gst-adjustments/round-off", {
        method: "POST",
        body: {
          vdate: today(),
          reference: ro.reference,
          amount: ro.amount
        }
      });
      setRoRes(r);
      setRo({
        ...ro,
        reference: genNo("RO")
      });
    } catch (ex) {
      setRoErr(ex.message);
    }
  };
  const closeYear = async e => {
    e.preventDefault();
    setYErr("");
    setYRes(null);
    if (!confirm(`Close the books for ${fy}? Income and expense accounts will be moved to retained earnings and every period in the year will be locked.`)) return;
    try {
      setYRes(await api("/api/accounting/year-end/close", {
        method: "POST",
        body: {
          fiscal_year: fy
        }
      }));
    } catch (ex) {
      setYErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "GST adjustment voucher"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: postGst
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Type"), /*#__PURE__*/React.createElement("select", {
    value: g.kind,
    onChange: e => setG({
      ...g,
      kind: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "itc-reversal"
  }, "ITC reversal"), /*#__PURE__*/React.createElement("option", {
    value: "itc-reclaim"
  }, "ITC reclaim"), /*#__PURE__*/React.createElement("option", {
    value: "rcm"
  }, "Reverse charge (RCM)"))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Reference"), /*#__PURE__*/React.createElement("input", {
    value: g.reference,
    onChange: e => setG({
      ...g,
      reference: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "CGST"), /*#__PURE__*/React.createElement("input", {
    value: g.cgst,
    onChange: e => setG({
      ...g,
      cgst: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "SGST"), /*#__PURE__*/React.createElement("input", {
    value: g.sgst,
    onChange: e => setG({
      ...g,
      sgst: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "IGST"), /*#__PURE__*/React.createElement("input", {
    value: g.igst,
    onChange: e => setG({
      ...g,
      igst: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post adjustment"))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 8,
      fontSize: 12
    }
  }, g.kind === "itc-reversal" ? "Dr Reversal expense · Cr Input CGST/SGST/IGST" : g.kind === "itc-reclaim" ? "Dr Input CGST/SGST/IGST · Cr Reversal expense (mirror of reversal)" : "Dr Input CGST/SGST/IGST · Cr Output CGST/SGST/IGST (net-nil to P&L)"), gErr && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, gErr)), /*#__PURE__*/React.createElement(Result, {
    r: gRes
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "GST round off"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: postRound
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Reference"), /*#__PURE__*/React.createElement("input", {
    value: ro.reference,
    onChange: e => setRo({
      ...ro,
      reference: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Amount (\xB1 \u20B9)"), /*#__PURE__*/React.createElement("input", {
    value: ro.amount,
    onChange: e => setRo({
      ...ro,
      amount: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post round off"))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 8,
      fontSize: 12
    }
  }, "Positive \u2192 Dr receivable \xB7 Cr Rounding Off (gain); negative \u2192 Dr Rounding Off \xB7 Cr receivable (cost)."), roErr && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, roErr)), /*#__PURE__*/React.createElement(Result, {
    r: roRes
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Year-end close"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: closeYear
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fiscal year"), /*#__PURE__*/React.createElement("input", {
    value: fy,
    onChange: e => setFy(e.target.value),
    placeholder: "2026-2027"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Close financial year"))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 8,
      fontSize: 12
    }
  }, "Zeroes income & expense accounts into retained earnings and locks every period in the year. Idempotent."), yErr && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, yErr)), /*#__PURE__*/React.createElement(Result, {
    r: yRes
  })));
}

// ---- inventory ops: transfers + roll split/merge --------------------------
function InventoryOps() {
  const chemicals = useList("/api/masters/chemicals");
  const warehouses = useList("/api/masters/warehouses");
  const [rolls, setRolls] = useState([]);
  const [transfers, setTransfers] = useState([]);
  const [tf, setTf] = useState({
    item_id: "",
    from_warehouse_id: "",
    to_warehouse_id: "",
    qty: "100",
    method: "FIFO"
  });
  const [sp, setSp] = useState({
    roll_id: "",
    a: "100",
    b: "150"
  });
  const [mSel, setMSel] = useState({});
  const [cf, setCf] = useState({
    item_id: "",
    warehouse_id: "",
    counted_qty: "",
    reason: ""
  });
  const [tRes, setTRes] = useState(null);
  const [spRes, setSpRes] = useState(null);
  const [mRes, setMRes] = useState(null);
  const [cRes, setCRes] = useState(null);
  const [err, setErr] = useState("");
  const count = e => {
    e.preventDefault();
    setCRes(null);
    go(async () => {
      setCRes(await api("/api/inventory/stock-count", {
        method: "POST",
        body: {
          item_type: "CHEMICAL",
          item_id: cf.item_id,
          warehouse_id: cf.warehouse_id || null,
          counted_qty: cf.counted_qty,
          reason: cf.reason || null
        }
      }));
      setCf({
        ...cf,
        counted_qty: ""
      });
    });
  };
  const load = useCallback(() => {
    api("/api/rolls?undelivered=true").then(setRolls).catch(() => {});
    api("/api/inventory/transfers").then(setTransfers).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const go = async fn => {
    setErr("");
    try {
      await fn();
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const transfer = e => {
    e.preventDefault();
    setTRes(null);
    go(async () => {
      setTRes(await api("/api/inventory/transfers", {
        method: "POST",
        body: {
          item_type: "CHEMICAL",
          item_id: tf.item_id,
          from_warehouse_id: tf.from_warehouse_id,
          to_warehouse_id: tf.to_warehouse_id,
          qty: tf.qty,
          method: tf.method
        }
      }));
    });
  };
  const split = e => {
    e.preventDefault();
    setSpRes(null);
    go(async () => {
      const parts = [{
        weight_kg: sp.a
      }];
      if (Number(sp.b) > 0) parts.push({
        weight_kg: sp.b
      });
      const r = await api(`/api/inventory/rolls/${sp.roll_id}/split`, {
        method: "POST",
        body: {
          parts
        }
      });
      setSpRes({
        parent_remaining_kg: r.parent_remaining_kg,
        parent_status: r.parent_status,
        children: (r.children || []).map(c => `${c.roll_no} (${c.weight_kg} kg)`).join(", ")
      });
      setSp({
        ...sp,
        roll_id: ""
      });
    });
  };
  const mergeSel = Object.keys(mSel).filter(k => mSel[k]);
  const merge = e => {
    e.preventDefault();
    setMRes(null);
    if (mergeSel.length < 2) {
      setErr("Select at least two rolls to merge");
      return;
    }
    go(async () => {
      setMRes(await api("/api/inventory/rolls/merge", {
        method: "POST",
        body: {
          roll_ids: mergeSel
        }
      }));
      setMSel({});
    });
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Physical stock count (inventory adjustment)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: count
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Chemical"), /*#__PURE__*/React.createElement(Opts, {
    items: chemicals,
    value: cf.item_id,
    onChange: e => setCf({
      ...cf,
      item_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Warehouse"), /*#__PURE__*/React.createElement(Opts, {
    items: warehouses,
    value: cf.warehouse_id,
    onChange: e => setCf({
      ...cf,
      warehouse_id: e.target.value
    }),
    placeholder: "\u2014 all \u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Counted qty"), /*#__PURE__*/React.createElement("input", {
    value: cf.counted_qty,
    onChange: e => setCf({
      ...cf,
      counted_qty: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Reason"), /*#__PURE__*/React.createElement("input", {
    value: cf.reason,
    onChange: e => setCf({
      ...cf,
      reason: e.target.value
    }),
    placeholder: "e.g. spillage"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Post count"))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 8,
      fontSize: 12
    }
  }, "Posts the costed variance vs book stock: shortage \u2192 Dr Inventory adjustment / Cr inventory; surplus \u2192 the reverse.")), /*#__PURE__*/React.createElement(Result, {
    r: cRes
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Stock transfer (between warehouses)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: transfer
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Chemical"), /*#__PURE__*/React.createElement(Opts, {
    items: chemicals,
    value: tf.item_id,
    onChange: e => setTf({
      ...tf,
      item_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "From"), /*#__PURE__*/React.createElement(Opts, {
    items: warehouses,
    value: tf.from_warehouse_id,
    onChange: e => setTf({
      ...tf,
      from_warehouse_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "To"), /*#__PURE__*/React.createElement(Opts, {
    items: warehouses,
    value: tf.to_warehouse_id,
    onChange: e => setTf({
      ...tf,
      to_warehouse_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty"), /*#__PURE__*/React.createElement("input", {
    value: tf.qty,
    onChange: e => setTf({
      ...tf,
      qty: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Method"), /*#__PURE__*/React.createElement("select", {
    value: tf.method,
    onChange: e => setTf({
      ...tf,
      method: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", null, "FIFO"), /*#__PURE__*/React.createElement("option", null, "FEFO"))), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Transfer")))), /*#__PURE__*/React.createElement(Result, {
    r: tRes
  }), transfers.length > 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: transfers,
    pageSize: 8,
    searchKeys: ["number", "date"],
    initialSort: {
      key: "date",
      dir: "desc"
    },
    columns: [{
      key: "number",
      label: "Number"
    }, {
      key: "date",
      label: "Date"
    }, {
      key: "qty",
      label: "Qty",
      num: true,
      render: t => inr2(t.qty)
    }, {
      key: "cost_value",
      label: "Value",
      num: true,
      render: t => inr2(t.cost_value)
    }]
  }))), /*#__PURE__*/React.createElement(Panel, {
    title: "Split a fabric roll"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: split
  }, /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      flex: 2
    }
  }, /*#__PURE__*/React.createElement("label", null, "Roll"), /*#__PURE__*/React.createElement(Opts, {
    items: rolls.map(r => ({
      id: r.id,
      name: `${r.roll_no} (${r.weight_kg} kg)`
    })),
    value: sp.roll_id,
    onChange: e => setSp({
      ...sp,
      roll_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Part 1 (kg)"), /*#__PURE__*/React.createElement("input", {
    value: sp.a,
    onChange: e => setSp({
      ...sp,
      a: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Part 2 (kg)"), /*#__PURE__*/React.createElement("input", {
    value: sp.b,
    onChange: e => setSp({
      ...sp,
      b: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Split roll"))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 8,
      fontSize: 12
    }
  }, "Parts can't exceed the roll weight; the parent keeps any remainder (or is closed when fully split).")), /*#__PURE__*/React.createElement(Result, {
    r: spRes
  })), /*#__PURE__*/React.createElement(Panel, {
    title: "Merge fabric rolls"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: merge
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    style: {
      width: 40
    }
  }), /*#__PURE__*/React.createElement("th", null, "Roll no."), /*#__PURE__*/React.createElement("th", null, "Fabric"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Weight (kg)"))), /*#__PURE__*/React.createElement("tbody", null, rolls.length === 0 ? /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: "4",
    className: "muted"
  }, "No active undelivered rolls.")) : rolls.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.id
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!mSel[r.id],
    onChange: () => setMSel(s => ({
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
  }, "Merge (", mergeSel.length, " rolls)"))), /*#__PURE__*/React.createElement(Result, {
    r: mRes
  }), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)));
}
function Notifications({
  onChange
}) {
  const [rows, setRows] = useState([]);
  const load = useCallback(() => api("/api/notifications").then(setRows).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);
  const readAll = async () => {
    try {
      await api("/api/notifications/mark-read", {
        method: "POST",
        body: {}
      });
      load();
      onChange && onChange();
    } catch (e) {}
  };
  const kindCls = {
    APPROVAL: "info",
    ALERT: "bad",
    INFO: ""
  };
  return /*#__PURE__*/React.createElement(Panel, {
    title: "Notifications"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    onClick: readAll
  }, "Mark all read")), rows.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Nothing here yet.") : rows.map(n => /*#__PURE__*/React.createElement("div", {
    className: "act",
    key: n.id,
    style: {
      opacity: n.is_read ? .55 : 1
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "tt"
  }, n.created_at ? n.created_at.slice(5, 10) : ""), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("b", null, n.title), n.message ? " — " + n.message : ""), /*#__PURE__*/React.createElement("span", {
    className: "badge " + (kindCls[n.kind] || "")
  }, n.kind))));
}
function JobWork() {
  const customers = useList("/api/masters/customers");
  const suppliers = useList("/api/masters/suppliers");
  const fabrics = useList("/api/masters/fabric-types");
  const taxes = useList("/api/masters/tax-codes");
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [res, setRes] = useState(null);
  const [f, setF] = useState({
    direction: "INWARD",
    process: "DYEING",
    party_id: "",
    fabric_type_id: "",
    qty_kg: "1000",
    rate: "25",
    tax_code_id: "",
    description: "",
    number: genNo("JW")
  });
  const load = useCallback(() => api("/api/jobwork").then(setRows).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);
  const parties = f.direction === "INWARD" ? customers : suppliers;
  const create = async e => {
    e.preventDefault();
    setErr("");
    setRes(null);
    try {
      const r = await api("/api/jobwork", {
        method: "POST",
        body: {
          number: f.number,
          direction: f.direction,
          process: f.process,
          party_id: f.party_id,
          order_date: today(),
          qty_kg: f.qty_kg,
          rate: f.rate,
          fabric_type_id: f.fabric_type_id || null,
          tax_code_id: f.tax_code_id || null,
          description: f.description || null
        }
      });
      setRes(r);
      setF({
        ...f,
        number: genNo("JW"),
        party_id: ""
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const act = async (id, action) => {
    setErr("");
    try {
      await api(`/api/jobwork/${id}/${action}`, {
        method: "POST"
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const stCls = {
    OPEN: "",
    SENT: "warn",
    RECEIVED: "warn",
    BILLED: "ok",
    CANCELLED: "bad"
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Job work \u2014 inward (we process for others) / outward (we send ours out)"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: create
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Direction"), /*#__PURE__*/React.createElement("select", {
    value: f.direction,
    onChange: e => setF({
      ...f,
      direction: e.target.value,
      party_id: ""
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "INWARD"
  }, "Inward \u2014 we dye/finish others' fabric (we earn)"), /*#__PURE__*/React.createElement("option", {
    value: "OUTWARD"
  }, "Outward \u2014 we send our fabric to another dyer (we pay)"))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Process"), /*#__PURE__*/React.createElement("select", {
    value: f.process,
    onChange: e => setF({
      ...f,
      process: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "DYEING"
  }, "Dyeing"), /*#__PURE__*/React.createElement("option", {
    value: "FINISHING"
  }, "Finishing"), /*#__PURE__*/React.createElement("option", {
    value: "KNITTING"
  }, "Knitting"), /*#__PURE__*/React.createElement("option", {
    value: "OTHER"
  }, "Other"))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, f.direction === "INWARD" ? "Customer" : "Subcontractor (supplier)"), /*#__PURE__*/React.createElement(Opts, {
    items: parties,
    value: f.party_id,
    onChange: e => setF({
      ...f,
      party_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "JW number"), /*#__PURE__*/React.createElement("input", {
    value: f.number,
    onChange: e => setF({
      ...f,
      number: e.target.value
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Fabric type"), /*#__PURE__*/React.createElement(Opts, {
    items: fabrics,
    value: f.fabric_type_id,
    onChange: e => setF({
      ...f,
      fabric_type_id: e.target.value
    }),
    placeholder: "\u2014"
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
  }, /*#__PURE__*/React.createElement("label", null, "Rate / kg"), /*#__PURE__*/React.createElement("input", {
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
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", null, "Note"), /*#__PURE__*/React.createElement("input", {
    value: f.description,
    onChange: e => setF({
      ...f,
      description: e.target.value
    }),
    placeholder: "e.g. navy, 1000 kg"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Create"))), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err)), /*#__PURE__*/React.createElement(Result, {
    r: res
  }), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      marginTop: 6
    }
  }, "Inward material belongs to the customer \u2014 it is never valued in your stock; you bill a processing charge (income). Outward keeps your fabric as your asset; on receipt you book the dyer's processing charge as a cost + payable.")), /*#__PURE__*/React.createElement(Panel, {
    title: "Job work orders"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: rows,
    pageSize: 10,
    empty: "None yet.",
    initialSort: {
      key: "number",
      dir: "desc"
    },
    searchKeys: ["number", "direction", "process", "party", "status"],
    columns: [{
      key: "number",
      label: "Number"
    }, {
      key: "direction",
      label: "Dir",
      render: j => /*#__PURE__*/React.createElement("span", {
        className: "badge " + (j.direction === "INWARD" ? "ok" : "info")
      }, j.direction)
    }, {
      key: "process",
      label: "Process"
    }, {
      key: "party",
      label: "Party"
    }, {
      key: "qty_kg",
      label: "Qty",
      num: true,
      render: j => inr2(j.qty_kg)
    }, {
      key: "charge",
      label: "Charge",
      num: true,
      sortable: false,
      render: j => Number(j.total_value) ? inr2(j.total_value) : inr2(Number(j.qty_kg) * Number(j.rate))
    }, {
      key: "status",
      label: "Status",
      render: j => /*#__PURE__*/React.createElement("span", {
        className: "badge " + (stCls[j.status] || "")
      }, j.status)
    }, {
      key: "act",
      label: "",
      sortable: false,
      num: true,
      render: j => /*#__PURE__*/React.createElement(React.Fragment, null, j.direction === "OUTWARD" && j.status === "OPEN" && /*#__PURE__*/React.createElement("button", {
        className: "btn sec",
        onClick: () => act(j.id, "dispatch")
      }, "Dispatch"), " ", j.status !== "BILLED" && j.status !== "CANCELLED" && /*#__PURE__*/React.createElement("button", {
        className: "btn",
        onClick: () => act(j.id, "bill")
      }, "Bill"))
    }]
  })));
}
function Stores() {
  const warehouses = useList("/api/masters/warehouses");
  const suppliers = useList("/api/masters/suppliers");
  const [items, setItems] = useState([]);
  const [err, setErr] = useState("");
  const [res, setRes] = useState(null);
  const [ni, setNi] = useState({
    code: "",
    name: "",
    category: "NEEDLE",
    uom: "PCS"
  });
  const [rc, setRc] = useState({
    store_item_id: "",
    qty: "",
    unit_cost: "",
    warehouse_id: "",
    supplier_id: ""
  });
  const [is, setIs] = useState({
    store_item_id: "",
    qty: "",
    warehouse_id: "",
    machine: ""
  });
  const load = useCallback(() => api("/api/stores/items").then(setItems).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);
  const go = async fn => {
    setErr("");
    setRes(null);
    try {
      await fn();
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  const addItem = e => {
    e.preventDefault();
    go(async () => {
      await api("/api/stores/items", {
        method: "POST",
        body: ni
      });
      setRes({
        created: ni.code
      });
      setNi({
        code: "",
        name: "",
        category: ni.category,
        uom: "PCS"
      });
    });
  };
  const receive = e => {
    e.preventDefault();
    go(async () => {
      const r = await api("/api/stores/receipts", {
        method: "POST",
        body: {
          store_item_id: rc.store_item_id,
          qty: rc.qty,
          unit_cost: rc.unit_cost,
          warehouse_id: rc.warehouse_id || null,
          supplier_id: rc.supplier_id || null
        }
      });
      setRes({
        received: r.value,
        voucher: r.voucher_number
      });
      setRc({
        ...rc,
        qty: "",
        unit_cost: ""
      });
    });
  };
  const issue = e => {
    e.preventDefault();
    go(async () => {
      const r = await api("/api/stores/issues", {
        method: "POST",
        body: {
          store_item_id: is.store_item_id,
          qty: is.qty,
          warehouse_id: is.warehouse_id || null,
          machine: is.machine || null
        }
      });
      setRes({
        issued_cost: r.cost,
        voucher: r.voucher_number
      });
      setIs({
        ...is,
        qty: ""
      });
    });
  };
  const itemOpts = items.map(i => ({
    id: i.id,
    label: `${i.code} · ${i.name}`
  }));
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Add store item (needles / spares / packing)"
  }, /*#__PURE__*/React.createElement("form", {
    className: "row",
    onSubmit: addItem
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Code"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: ni.code,
    onChange: e => setNi({
      ...ni,
      code: e.target.value
    }),
    placeholder: "NDL-9G",
    style: {
      width: 120
    }
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: ni.name,
    onChange: e => setNi({
      ...ni,
      name: e.target.value
    }),
    placeholder: "9G Knitting Needle"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Category"), /*#__PURE__*/React.createElement("select", {
    value: ni.category,
    onChange: e => setNi({
      ...ni,
      category: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "NEEDLE"
  }, "Needle"), /*#__PURE__*/React.createElement("option", {
    value: "SPARE"
  }, "Spare"), /*#__PURE__*/React.createElement("option", {
    value: "PACKING"
  }, "Packing"), /*#__PURE__*/React.createElement("option", {
    value: "OTHER"
  }, "Other"))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "UoM"), /*#__PURE__*/React.createElement("input", {
    value: ni.uom,
    onChange: e => setNi({
      ...ni,
      uom: e.target.value
    }),
    style: {
      width: 80
    }
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Add"))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 16,
      alignItems: "flex-start"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Receive into store"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: receive
  }, /*#__PURE__*/React.createElement("div", {
    className: "field mb"
  }, /*#__PURE__*/React.createElement("label", null, "Item"), /*#__PURE__*/React.createElement(Opts, {
    items: itemOpts,
    value: rc.store_item_id,
    onChange: e => setRc({
      ...rc,
      store_item_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty"), /*#__PURE__*/React.createElement("input", {
    value: rc.qty,
    onChange: e => setRc({
      ...rc,
      qty: e.target.value
    }),
    placeholder: "500"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Unit cost"), /*#__PURE__*/React.createElement("input", {
    value: rc.unit_cost,
    onChange: e => setRc({
      ...rc,
      unit_cost: e.target.value
    }),
    placeholder: "20"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Warehouse"), /*#__PURE__*/React.createElement(Opts, {
    items: warehouses,
    value: rc.warehouse_id,
    onChange: e => setRc({
      ...rc,
      warehouse_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Supplier (credit) / blank = cash"), /*#__PURE__*/React.createElement(Opts, {
    items: suppliers,
    value: rc.supplier_id,
    onChange: e => setRc({
      ...rc,
      supplier_id: e.target.value
    }),
    placeholder: "cash"
  }))), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Receive")))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Issue to machine"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: issue
  }, /*#__PURE__*/React.createElement("div", {
    className: "field mb"
  }, /*#__PURE__*/React.createElement("label", null, "Item"), /*#__PURE__*/React.createElement(Opts, {
    items: itemOpts,
    value: is.store_item_id,
    onChange: e => setIs({
      ...is,
      store_item_id: e.target.value
    })
  })), /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Qty"), /*#__PURE__*/React.createElement("input", {
    value: is.qty,
    onChange: e => setIs({
      ...is,
      qty: e.target.value
    }),
    placeholder: "150"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Machine"), /*#__PURE__*/React.createElement("input", {
    value: is.machine,
    onChange: e => setIs({
      ...is,
      machine: e.target.value
    }),
    placeholder: "KNIT-01"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "field mb"
  }, /*#__PURE__*/React.createElement("label", null, "From warehouse"), /*#__PURE__*/React.createElement(Opts, {
    items: warehouses,
    value: is.warehouse_id,
    onChange: e => setIs({
      ...is,
      warehouse_id: e.target.value
    }),
    placeholder: "\u2014"
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Issue (expense at FIFO cost)"))))), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), /*#__PURE__*/React.createElement(Result, {
    r: res
  }), /*#__PURE__*/React.createElement(Panel, {
    title: "Store items"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: items,
    pageSize: 10,
    empty: "None yet.",
    searchKeys: ["code", "name", "category"],
    columns: [{
      key: "code",
      label: "Code"
    }, {
      key: "name",
      label: "Name"
    }, {
      key: "category",
      label: "Category"
    }, {
      key: "uom",
      label: "UoM"
    }]
  }), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      marginTop: 6
    }
  }, "On-hand quantities show on the Stock on Hand page (item type STORE).")));
}
function StockOnHand() {
  const warehouses = useList("/api/masters/warehouses");
  const [wh, setWh] = useState("");
  const [it, setIt] = useState("");
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setErr("");
    setBusy(true);
    try {
      const p = [];
      if (wh) p.push("warehouse_id=" + wh);
      if (it) p.push("item_type=" + it);
      setRows(await api("/api/inventory/stock-on-hand" + (p.length ? "?" + p.join("&") : "")));
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }, [wh, it]);
  useEffect(() => {
    load();
  }, [wh, it]);
  const totVal = rows.reduce((s, r) => s + Number(r.value || 0), 0);
  const typeBadge = {
    YARN: "",
    CHEMICAL: "info",
    FABRIC: "ok"
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Stock on hand (live)"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb",
    style: {
      alignItems: "end",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Warehouse"), /*#__PURE__*/React.createElement("select", {
    value: wh,
    onChange: e => setWh(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All warehouses"), warehouses.map(w => /*#__PURE__*/React.createElement("option", {
    key: w.id,
    value: w.id
  }, w.code, " \xB7 ", w.name)))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Item type"), /*#__PURE__*/React.createElement("select", {
    value: it,
    onChange: e => setIt(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All"), /*#__PURE__*/React.createElement("option", {
    value: "YARN"
  }, "Yarn"), /*#__PURE__*/React.createElement("option", {
    value: "CHEMICAL"
  }, "Chemical / Dye"), /*#__PURE__*/React.createElement("option", {
    value: "FABRIC"
  }, "Fabric"))), /*#__PURE__*/React.createElement("button", {
    className: "btn sec",
    disabled: busy,
    onClick: load
  }, "Refresh"), /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: "right"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, "Total stock value"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 700,
      fontSize: 16
    }
  }, inr2(totVal)))), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), /*#__PURE__*/React.createElement(DataTable, {
    rows: rows,
    pageSize: 12,
    empty: "No stock on hand for this filter.",
    searchKeys: ["item_type", "item_code", "item_name", "warehouse"],
    initialSort: {
      key: "value",
      dir: "desc"
    },
    columns: [{
      key: "item_type",
      label: "Type",
      render: r => /*#__PURE__*/React.createElement("span", {
        className: "badge " + (typeBadge[r.item_type] || "")
      }, r.item_type)
    }, {
      key: "item_code",
      label: "Code",
      render: r => r.item_code || "—"
    }, {
      key: "item_name",
      label: "Item"
    }, {
      key: "warehouse",
      label: "Warehouse"
    }, {
      key: "qty",
      label: "Qty",
      num: true,
      render: r => inr2(r.qty)
    }, {
      key: "value",
      label: "Value",
      num: true,
      render: r => inr2(r.value)
    }]
  }), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      marginTop: 6
    }
  }, "Live from open FIFO cost layers \xB7 fabric grouped by type & greige/finished.")));
}
const ACCT_TYPES = [["ASSET", "Asset"], ["LIABILITY", "Liability"], ["EQUITY", "Equity"], ["INCOME", "Income"], ["EXPENSE", "Expense"]];
function ChartOfAccounts() {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [f, setF] = useState({
    code: "",
    name: "",
    account_type: "EXPENSE",
    parent_code: "",
    is_group: false
  });
  const load = useCallback(() => api("/api/accounts").then(setRows).catch(e => setErr(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);
  const groups = rows.filter(a => a.is_group && a.account_type === f.account_type);
  const byCode = Object.fromEntries(rows.map(a => [a.id, a.code]));
  const add = async e => {
    e.preventDefault();
    setErr("");
    setOk("");
    try {
      await api("/api/accounts", {
        method: "POST",
        body: {
          code: f.code,
          name: f.name,
          account_type: f.account_type,
          parent_code: f.parent_code || null,
          is_group: f.is_group
        }
      });
      setOk(`Account ${f.code} created`);
      setF({
        code: "",
        name: "",
        account_type: f.account_type,
        parent_code: "",
        is_group: false
      });
      load();
    } catch (ex) {
      setErr(ex.message);
    }
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Panel, {
    title: "Add account"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: add
  }, /*#__PURE__*/React.createElement("div", {
    className: "row mb"
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Code"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: f.code,
    onChange: e => setF({
      ...f,
      code: e.target.value
    }),
    placeholder: "e.g. 2511",
    style: {
      width: 120
    }
  })), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    required: true,
    value: f.name,
    onChange: e => setF({
      ...f,
      name: e.target.value
    }),
    placeholder: "e.g. HDFC Term Loan A/c 1234"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Type"), /*#__PURE__*/React.createElement("select", {
    value: f.account_type,
    onChange: e => setF({
      ...f,
      account_type: e.target.value,
      parent_code: ""
    })
  }, ACCT_TYPES.map(([v, l]) => /*#__PURE__*/React.createElement("option", {
    key: v,
    value: v
  }, l)))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Under group"), /*#__PURE__*/React.createElement("select", {
    value: f.parent_code,
    onChange: e => setF({
      ...f,
      parent_code: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014 top level \u2014"), groups.map(g => /*#__PURE__*/React.createElement("option", {
    key: g.id,
    value: g.code
  }, g.code, " \xB7 ", g.name)))), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: "flex",
      gap: 6,
      alignItems: "center",
      fontSize: 12.5
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: f.is_group,
    onChange: e => setF({
      ...f,
      is_group: e.target.checked
    })
  }), " is a heading")), /*#__PURE__*/React.createElement("div", {
    className: "field",
    style: {
      alignSelf: "end"
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn"
  }, "Add"))), err && /*#__PURE__*/React.createElement("div", {
    className: "err"
  }, err), ok && /*#__PURE__*/React.createElement("div", {
    style: {
      color: "#16a34a",
      fontSize: 12.5
    }
  }, ok)), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, "Direct expenses sit under ", /*#__PURE__*/React.createElement("b", null, "5000 Direct Expenses & COGS"), ", indirect under ", /*#__PURE__*/React.createElement("b", null, "6000 Indirect Expenses"), ", loans under ", /*#__PURE__*/React.createElement("b", null, "2500 Long-term Liabilities"), " or ", /*#__PURE__*/React.createElement("b", null, "2000 Current Liabilities"), ". A heading cannot receive postings.")), /*#__PURE__*/React.createElement(Panel, {
    title: "Chart of accounts"
  }, /*#__PURE__*/React.createElement(DataTable, {
    rows: rows,
    pageSize: 15,
    rowClass: a => a.is_group ? "row-group" : undefined,
    searchKeys: ["code", "name", "account_type"],
    columns: [{
      key: "code",
      label: "Code"
    }, {
      key: "name",
      label: "Name"
    }, {
      key: "account_type",
      label: "Type"
    }, {
      key: "under",
      label: "Under",
      sortable: false,
      render: a => a.parent_id ? byCode[a.parent_id] : "—"
    }, {
      key: "postable",
      label: "Postable",
      sortable: false,
      render: a => a.is_group ? "heading" : a.allow_posting ? "yes" : "no"
    }]
  })));
}

// Group & Tenancy admin — set up organizations (groups), assign companies to a
// group, grant company memberships, and configure inter-company account mappings.
function Tenancy() {
  const [orgs, setOrgs] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [users, setUsers] = useState([]);
  const [maps, setMaps] = useState([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [org, setOrg] = useState({
    code: "",
    name: ""
  });
  const [assign, setAssign] = useState({
    company_id: "",
    organization_id: ""
  });
  const [mem, setMem] = useState({
    user_id: "",
    company_id: "",
    is_default: true
  });
  const [icm, setIcm] = useState({
    company_id: "",
    counterparty_company_id: "",
    due_from_account_code: "",
    due_to_account_code: ""
  });
  const reload = useCallback(() => {
    setErr("");
    api("/api/admin/organizations").then(setOrgs).catch(e => setErr(e.message));
    api("/api/admin/companies").then(setCompanies).catch(e => setErr(e.message));
    api("/api/admin/users").then(setUsers).catch(() => {});
    api("/api/admin/intercompany-mappings").then(setMaps).catch(() => {});
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);
  const run = async fn => {
    setErr("");
    setMsg("");
    try {
      await fn();
      reload();
    } catch (e) {
      setErr(e.message);
    }
  };
  const coName = id => {
    const c = companies.find(x => x.id === id);
    return c ? c.code : (id || "").slice(0, 8);
  };
  return /*#__PURE__*/React.createElement("div", null, err && /*#__PURE__*/React.createElement("div", {
    className: "alert-list"
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      color: "var(--rose,#c0392b)"
    }
  }, "\u26A0 ", err)), msg && /*#__PURE__*/React.createElement("div", {
    className: "alert-list"
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "\u2713 ", msg)), /*#__PURE__*/React.createElement(Panel, {
    title: "Organizations (company groups)"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Name"))), /*#__PURE__*/React.createElement("tbody", null, orgs.map(o => /*#__PURE__*/React.createElement("tr", {
    key: o.id
  }, /*#__PURE__*/React.createElement("td", null, o.code), /*#__PURE__*/React.createElement("td", null, o.name))), orgs.length === 0 && /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: 2,
    className: "muted"
  }, "No groups yet.")))), /*#__PURE__*/React.createElement("div", {
    className: "form-row",
    style: {
      marginTop: 10,
      display: "flex",
      gap: 8,
      flexWrap: "wrap",
      alignItems: "end"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Code"), /*#__PURE__*/React.createElement("input", {
    value: org.code,
    onChange: e => setOrg({
      ...org,
      code: e.target.value
    }),
    placeholder: "GRP"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Name"), /*#__PURE__*/React.createElement("input", {
    value: org.name,
    onChange: e => setOrg({
      ...org,
      name: e.target.value
    }),
    placeholder: "Textile Group"
  })), /*#__PURE__*/React.createElement("button", {
    onClick: () => run(async () => {
      await api("/api/admin/organizations", {
        body: org
      });
      setOrg({
        code: "",
        name: ""
      });
      setMsg("Group created");
    })
  }, "Create group"))), /*#__PURE__*/React.createElement(Panel, {
    title: "Assign a company to a group"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      flexWrap: "wrap",
      alignItems: "end"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Company"), /*#__PURE__*/React.createElement("select", {
    value: assign.company_id,
    onChange: e => setAssign({
      ...assign,
      company_id: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014 select \u2014"), companies.map(c => /*#__PURE__*/React.createElement("option", {
    key: c.id,
    value: c.id
  }, c.code, " \xB7 ", c.name, c.organization_id ? " (grouped)" : "")))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Group"), /*#__PURE__*/React.createElement("select", {
    value: assign.organization_id,
    onChange: e => setAssign({
      ...assign,
      organization_id: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014 none \u2014"), orgs.map(o => /*#__PURE__*/React.createElement("option", {
    key: o.id,
    value: o.id
  }, o.code)))), /*#__PURE__*/React.createElement("button", {
    disabled: !assign.company_id,
    onClick: () => run(async () => {
      await api(`/api/admin/companies/${assign.company_id}/organization`, {
        body: {
          organization_id: assign.organization_id || null
        }
      });
      setMsg("Company assignment updated");
    })
  }, "Assign"))), /*#__PURE__*/React.createElement(Panel, {
    title: "Grant company membership"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      flexWrap: "wrap",
      alignItems: "end"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "User"), /*#__PURE__*/React.createElement("select", {
    value: mem.user_id,
    onChange: e => setMem({
      ...mem,
      user_id: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014 select \u2014"), users.map(u => /*#__PURE__*/React.createElement("option", {
    key: u.id,
    value: u.id
  }, u.username)))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Company"), /*#__PURE__*/React.createElement("select", {
    value: mem.company_id,
    onChange: e => setMem({
      ...mem,
      company_id: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014 select \u2014"), companies.map(c => /*#__PURE__*/React.createElement("option", {
    key: c.id,
    value: c.id
  }, c.code)))), /*#__PURE__*/React.createElement("label", {
    style: {
      display: "flex",
      gap: 4,
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: mem.is_default,
    onChange: e => setMem({
      ...mem,
      is_default: e.target.checked
    })
  }), " default"), /*#__PURE__*/React.createElement("button", {
    disabled: !mem.user_id || !mem.company_id,
    onClick: () => run(async () => {
      await api("/api/admin/memberships/company", {
        body: mem
      });
      setMsg("Membership granted");
    })
  }, "Grant"))), /*#__PURE__*/React.createElement(Panel, {
    title: "Inter-company account mappings"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "From \u2192 To"), /*#__PURE__*/React.createElement("th", null, "Approved"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, maps.map(m => /*#__PURE__*/React.createElement("tr", {
    key: m.id
  }, /*#__PURE__*/React.createElement("td", null, coName(m.company_id), " \u2192 ", coName(m.counterparty_company_id)), /*#__PURE__*/React.createElement("td", null, m.is_approved ? "✓" : "pending"), /*#__PURE__*/React.createElement("td", null, !m.is_approved && /*#__PURE__*/React.createElement("button", {
    onClick: () => run(async () => {
      await api(`/api/admin/intercompany-mappings/${m.id}/approve`, {
        method: "POST"
      });
      setMsg("Mapping approved");
    })
  }, "Approve")))), maps.length === 0 && /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", {
    colSpan: 3,
    className: "muted"
  }, "No mappings configured.")))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      flexWrap: "wrap",
      alignItems: "end",
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Company"), /*#__PURE__*/React.createElement("select", {
    value: icm.company_id,
    onChange: e => setIcm({
      ...icm,
      company_id: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014"), companies.map(c => /*#__PURE__*/React.createElement("option", {
    key: c.id,
    value: c.id
  }, c.code)))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Counterparty"), /*#__PURE__*/React.createElement("select", {
    value: icm.counterparty_company_id,
    onChange: e => setIcm({
      ...icm,
      counterparty_company_id: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "\u2014"), companies.map(c => /*#__PURE__*/React.createElement("option", {
    key: c.id,
    value: c.id
  }, c.code)))), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Due-from a/c"), /*#__PURE__*/React.createElement("input", {
    value: icm.due_from_account_code,
    onChange: e => setIcm({
      ...icm,
      due_from_account_code: e.target.value
    }),
    placeholder: "1050"
  })), /*#__PURE__*/React.createElement("div", {
    className: "field"
  }, /*#__PURE__*/React.createElement("label", null, "Due-to a/c"), /*#__PURE__*/React.createElement("input", {
    value: icm.due_to_account_code,
    onChange: e => setIcm({
      ...icm,
      due_to_account_code: e.target.value
    }),
    placeholder: "2070"
  })), /*#__PURE__*/React.createElement("button", {
    disabled: !icm.company_id || !icm.counterparty_company_id,
    onClick: () => run(async () => {
      await api("/api/admin/intercompany-mappings", {
        body: icm
      });
      setIcm({
        company_id: "",
        counterparty_company_id: "",
        due_from_account_code: "",
        due_to_account_code: ""
      });
      setMsg("Mapping created (pending approval)");
    })
  }, "Add mapping")), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 6
    }
  }, "Due-from must be an ASSET account, due-to a LIABILITY account, in the company's own chart of accounts. An accountant approves before posting.")));
}

// Group (consolidated) dashboard — company-wise + consolidated figures for the
// user's company group, from the read-only consolidation APIs.
function GroupDash({
  refreshKey
}) {
  const [ctx, setCtx] = useState(null);
  const [pnl, setPnl] = useState(null);
  const [recon, setRecon] = useState([]);
  const [byFactory, setByFactory] = useState([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr("");
    setNote("");
    api("/api/me/context").then(c => {
      if (!alive) return;
      setCtx(c);
      const org = (c.companies || []).map(x => x.organization_id).find(Boolean);
      if (!org) {
        setNote("No company group is configured. Assign companies to an Organization (Administration) to see consolidated figures.");
        setLoading(false);
        return;
      }
      Promise.all([api("/api/reports/consolidated/pnl?organization_id=" + org).catch(e => {
        setErr(e.message);
        return null;
      }), api("/api/admin/intercompany-reconciliation").catch(() => []), api("/api/costing/by-factory").catch(() => [])]).then(([p, r, f]) => {
        if (!alive) return;
        setPnl(p);
        setRecon(r || []);
        setByFactory(f || []);
        setLoading(false);
      });
    }).catch(e => {
      if (alive) {
        setErr(e.message);
        setLoading(false);
      }
    });
    return () => {
      alive = false;
    };
  }, [refreshKey]);
  if (loading) return /*#__PURE__*/React.createElement(Panel, null, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Loading group figures\u2026"));
  if (note) return /*#__PURE__*/React.createElement(Panel, {
    title: "Group Dashboard"
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, note));
  if (err && !pnl) return /*#__PURE__*/React.createElement(Panel, {
    title: "Group Dashboard"
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "Consolidated view unavailable: ", err, ". You must be authorized for every company in the group."));
  const companies = pnl && pnl.companies || [];
  const readyPairs = recon.filter(r => r.ready_for_elimination);
  const net = pnl ? Number(pnl.net_profit) : 0;
  const mono = {
    fontFamily: "monospace",
    fontSize: ".85em"
  };
  const cols = {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))",
    gap: "12px"
  };
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kpi-grid"
  }, /*#__PURE__*/React.createElement(Stat, {
    icon: "\uD83C\uDFE2",
    label: "Companies in group",
    value: companies.length,
    tone: "info"
  }), /*#__PURE__*/React.createElement(Stat, {
    icon: "\uD83D\uDCB9",
    label: "Consolidated income",
    value: pnl ? inr(pnl.total_income) : "—",
    tone: "green"
  }), /*#__PURE__*/React.createElement(Stat, {
    icon: "\uD83D\uDCB8",
    label: "Consolidated expense",
    value: pnl ? inr(pnl.total_expense) : "—",
    tone: "amber"
  }), /*#__PURE__*/React.createElement(Stat, {
    icon: "\uD83D\uDCCA",
    label: "Consolidated net profit",
    value: pnl ? inr(pnl.net_profit) : "—",
    tone: net >= 0 ? "green" : "rose"
  })), pnl && !pnl.approved && /*#__PURE__*/React.createElement("div", {
    className: "alert-list"
  }, /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "\u26A0 ", pnl.eliminations_pending.length, " elimination(s) pending finance approval \u2014 figures exclude unapproved eliminations.")), /*#__PURE__*/React.createElement("div", {
    style: cols
  }, /*#__PURE__*/React.createElement(Panel, {
    title: "Companies in the group"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Code"), /*#__PURE__*/React.createElement("th", null, "Name"))), /*#__PURE__*/React.createElement("tbody", null, companies.map(c => /*#__PURE__*/React.createElement("tr", {
    key: c.id
  }, /*#__PURE__*/React.createElement("td", null, c.code), /*#__PURE__*/React.createElement("td", null, c.name)))))), /*#__PURE__*/React.createElement(Panel, {
    title: "Inter-company reconciliation"
  }, recon.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted"
  }, "No inter-company mappings configured.") : /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Direction"), /*#__PURE__*/React.createElement("th", null, "Approved"), /*#__PURE__*/React.createElement("th", null, "Reciprocal"), /*#__PURE__*/React.createElement("th", null, "Ready"))), /*#__PURE__*/React.createElement("tbody", null, recon.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", {
    style: mono
  }, (r.company_id || "").slice(0, 8), " \u2192 ", (r.counterparty_company_id || "").slice(0, 8)), /*#__PURE__*/React.createElement("td", null, r.approved ? "✓" : "—"), /*#__PURE__*/React.createElement("td", null, r.reciprocal_approved ? "✓" : r.reciprocal_configured ? "unapproved" : "missing"), /*#__PURE__*/React.createElement("td", null, r.ready_for_elimination ? "✓" : "—"))))), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      marginTop: 6
    }
  }, readyPairs.length, " pair(s) ready for elimination."))), /*#__PURE__*/React.createElement(Panel, {
    title: "Consolidated Profit & Loss"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Account"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Income"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Expense"))), /*#__PURE__*/React.createElement("tbody", null, (pnl.income || []).map(r => /*#__PURE__*/React.createElement("tr", {
    key: "i" + r.account_code
  }, /*#__PURE__*/React.createElement("td", null, r.account_code, " \xB7 ", r.account_name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.amount)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }))), (pnl.expense || []).map(r => /*#__PURE__*/React.createElement("tr", {
    key: "e" + r.account_code
  }, /*#__PURE__*/React.createElement("td", null, r.account_code, " \xB7 ", r.account_name), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.amount)))), /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, "Totals")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("b", null, inr2(pnl.total_income))), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("b", null, inr2(pnl.total_expense)))), /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, "Net profit")), /*#__PURE__*/React.createElement("td", {
    className: "num",
    colSpan: 2
  }, /*#__PURE__*/React.createElement("b", null, inr(pnl.net_profit))))))), byFactory.length > 0 && /*#__PURE__*/React.createElement(Panel, {
    title: "Factory production (acting company)"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Factory"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Knit kg"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Dye kg"), /*#__PURE__*/React.createElement("th", {
    className: "num"
  }, "Efficiency %"))), /*#__PURE__*/React.createElement("tbody", null, byFactory.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, r.factory), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.knitting_produced_kg)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, inr2(r.dyeing_fabric_kg)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.knitting_efficiency_pct)))))));
}
const PAGES = {
  group: {
    t: "Group Dashboard",
    c: GroupDash
  },
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
  inventoryops: {
    t: "Inventory Ops (transfer / split / merge)",
    c: InventoryOps
  },
  stock: {
    t: "Stock on Hand",
    c: StockOnHand
  },
  stores: {
    t: "Stores & Spares",
    c: Stores
  },
  pipeline: {
    t: "Sales Pipeline (enquiry → quote → SO)",
    c: SalesPipeline
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
  jobwork: {
    t: "Job Work (inward / outward)",
    c: JobWork
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
  assets: {
    t: "Fixed Assets & Depreciation",
    c: FixedAssets
  },
  payroll: {
    t: "Payroll",
    c: Payroll
  },
  approvals: {
    t: "Approvals (maker-checker)",
    c: Approvals
  },
  notifications: {
    t: "Notifications",
    c: Notifications
  },
  acctops: {
    t: "GST Adjustments & Year-End",
    c: AccountingOps
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
  coa: {
    t: "Chart of Accounts",
    c: ChartOfAccounts
  },
  vouchers: {
    t: "Vouchers",
    c: Vouchers
  },
  admin: {
    t: "Administration",
    c: Admin
  },
  tenancy: {
    t: "Group & Tenancy",
    c: Tenancy
  },
  reports: {
    t: "Reports",
    c: Reports
  }
};
const NAV_ICON = {
  group: "🏛️",
  owner: "📊",
  factory: "🏭",
  accounts: "💼",
  chemical: "🧪",
  purchaseorders: "📝",
  purchase: "📥",
  knitting: "🧶",
  batching: "🧺",
  pipeline: "🎯",
  salesorders: "🛒",
  sales: "🧾",
  dyeing: "🎨",
  vouchers: "📒",
  inventoryops: "🔁",
  stock: "📦",
  stores: "🪡",
  jobwork: "🔄",
  masters: "🗃️",
  coa: "📚",
  customers: "👤",
  suppliers: "🏢",
  warehouses: "🏬",
  quality: "🔍",
  assets: "🏗️",
  payroll: "💰",
  costing: "📐",
  devices: "🖨️",
  reports: "📈",
  approvals: "✔️",
  notifications: "🔔",
  acctops: "🧮",
  admin: "⚙️",
  tenancy: "🏛️"
};
function fyLabel() {
  const d = new Date();
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `FY ${y}–${(y + 1) % 100}`;
}

// Financial year helpers: FY string "2026-2027" -> the as-of date to query.
function currentFY() {
  const d = new Date();
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `${y}-${y + 1}`;
}
function fyAsOf(fy) {
  const parts = (fy || "").split("-").map(Number);
  if (parts.length !== 2 || isNaN(parts[1])) return today();
  const start = new Date(parts[0], 3, 1),
    end = new Date(parts[1], 2, 31),
    now = new Date();
  return now >= start && now <= end ? today() : `${parts[1]}-03-31`; // current FY -> today, else year-end
}
function App() {
  const [page, setPage] = useState("owner");
  const [me, setMe] = useState(null);
  const [notif, setNotif] = useState(0);
  // Financial-year context: options come from real accounting periods; the
  // selection drives the as-of date applied to dashboard/report queries.
  const [fys, setFys] = useState([]);
  const [fy, setFy] = useState(() => {
    try {
      return localStorage.getItem("erp_fy") || currentFY();
    } catch (e) {
      return currentFY();
    }
  });
  const [refreshKey, setRefreshKey] = useState(0);
  // Company/factory scope: options come from the user's authorized memberships.
  const [ctx, setCtx] = useState(null);
  const [companyId, setCompanyId] = useState(() => getScope(COMPANY_KEY));
  const [factoryId, setFactoryId] = useState(() => getScope(FACTORY_KEY));
  const loadCtx = useCallback(recover => {
    api("/api/me/context").then(c => {
      setCtx(c);
      if (c && c.active) {
        if (c.active.company_id) setCompanyId(c.active.company_id);
      }
    }).catch(() => {
      // stale scope (e.g. membership changed) -> clear once and retry
      if (recover !== false) {
        setScope(COMPANY_KEY, "");
        setScope(FACTORY_KEY, "");
        setCompanyId("");
        setFactoryId("");
        api("/api/me/context").then(setCtx).catch(() => {});
      }
    });
  }, []);
  useEffect(() => {
    loadCtx();
  }, [loadCtx]);
  useEffect(() => {
    api("/api/admin/periods").then(ps => {
      const years = [...new Set((ps || []).map(p => p.fiscal_year).filter(Boolean))].sort().reverse();
      if (!years.includes(currentFY())) years.unshift(currentFY());
      setFys(years);
    }).catch(() => setFys([currentFY()]));
  }, [refreshKey]);
  const pickFy = v => {
    setFy(v);
    try {
      localStorage.setItem("erp_fy", v);
    } catch (e) {}
    setRefreshKey(k => k + 1);
  };
  const pickCompany = v => {
    setScope(COMPANY_KEY, v);
    setCompanyId(v);
    setScope(FACTORY_KEY, "");
    setFactoryId(""); // factory belongs to a company
    setRefreshKey(k => k + 1);
    loadCtx(false);
  };
  const pickFactory = v => {
    setScope(FACTORY_KEY, v);
    setFactoryId(v);
    setRefreshKey(k => k + 1);
  };
  const activeCompany = (ctx && ctx.companies || []).find(c => c.id === (companyId || ctx && ctx.active && ctx.active.company_id));
  const factories = activeCompany && activeCompany.factories || [];
  const asOf = fyAsOf(fy);
  const loadNotif = useCallback(() => api("/api/notifications/unread-count").then(r => setNotif(r.count || 0)).catch(() => {}), []);
  useEffect(() => {
    api("/api/auth/me").then(setMe).catch(() => {});
    loadNotif();
    const t = setInterval(loadNotif, 30000);
    return () => clearInterval(t);
  }, [loadNotif]);
  const [drawer, setDrawer] = useState(false); // mobile nav drawer open
  const go = id => {
    setPage(id);
    setDrawer(false);
  };
  const Cur = PAGES[page].c;
  const navBtn = (id, label) => /*#__PURE__*/React.createElement("button", {
    className: page === id ? "active" : "",
    onClick: () => go(id)
  }, /*#__PURE__*/React.createElement("span", {
    className: "nav-ico"
  }, NAV_ICON[id] || "•"), label);
  return /*#__PURE__*/React.createElement("div", {
    className: "app" + (drawer ? " drawer-open" : "")
  }, /*#__PURE__*/React.createElement("div", {
    className: "scrim",
    onClick: () => setDrawer(false)
  }), /*#__PURE__*/React.createElement("aside", {
    className: "sidebar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "brand"
  }, "\u25C6 Textile ERP", /*#__PURE__*/React.createElement("small", null, "Knit \xB7 Dye \xB7 Finish \xB7 Grow")), /*#__PURE__*/React.createElement("nav", {
    className: "nav"
  }, /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Dashboards"), navBtn("group", "Group"), navBtn("owner", "Owner"), navBtn("factory", "Factory"), navBtn("accounts", "Accounts"), navBtn("chemical", "Chemical"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Transactions"), navBtn("purchaseorders", "Purchase Orders"), navBtn("purchase", "Purchase (GRN)"), navBtn("knitting", "Knitting"), navBtn("batching", "Batching"), navBtn("pipeline", "Sales Pipeline"), navBtn("salesorders", "Sales Orders"), navBtn("sales", "Sales Invoice"), navBtn("dyeing", "Dyeing & Dispensing"), navBtn("jobwork", "Job Work"), navBtn("vouchers", "Vouchers"), navBtn("inventoryops", "Inventory Ops"), navBtn("stock", "Stock on Hand"), navBtn("stores", "Stores & Spares"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Masters"), navBtn("masters", "Master Data"), navBtn("coa", "Chart of Accounts"), navBtn("customers", "Customers"), navBtn("suppliers", "Suppliers"), navBtn("warehouses", "Warehouses"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Quality"), navBtn("quality", "Quality"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Assets"), navBtn("assets", "Fixed Assets"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Payroll"), navBtn("payroll", "Payroll"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Costing"), navBtn("costing", "Costing"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Devices"), navBtn("devices", "Devices & Labels"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Reports"), navBtn("reports", "Reports"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Admin"), navBtn("approvals", "Approvals"), navBtn("acctops", "GST Adj / Year-End"), navBtn("admin", "Administration"), navBtn("tenancy", "Group & Tenancy"), /*#__PURE__*/React.createElement("div", {
    className: "group"
  }, "Session"), /*#__PURE__*/React.createElement("button", {
    onClick: () => {
      setToken(null);
      setScope(COMPANY_KEY, "");
      setScope(FACTORY_KEY, "");
      location.reload();
    }
  }, "Log out"))), /*#__PURE__*/React.createElement("div", {
    className: "main"
  }, /*#__PURE__*/React.createElement("div", {
    className: "topbar"
  }, /*#__PURE__*/React.createElement("button", {
    className: "hamburger",
    "aria-label": "Menu",
    onClick: () => setDrawer(v => !v)
  }, "\u2630"), /*#__PURE__*/React.createElement("h1", null, PAGES[page].t), /*#__PURE__*/React.createElement("div", {
    className: "who-wrap"
  }, /*#__PURE__*/React.createElement("button", {
    className: "bell",
    title: "Notifications",
    onClick: () => {
      go("notifications");
      loadNotif();
    }
  }, "\uD83D\uDD14", notif > 0 && /*#__PURE__*/React.createElement("span", {
    className: "bell-dot"
  }, notif > 99 ? "99+" : notif)), /*#__PURE__*/React.createElement("button", {
    className: "refresh-btn",
    title: "Refresh data",
    onClick: () => setRefreshKey(k => k + 1)
  }, "\u27F3"), ctx && (ctx.companies || []).length > 1 && /*#__PURE__*/React.createElement("select", {
    className: "fy-select",
    title: "Company",
    value: companyId || ctx.active && ctx.active.company_id || "",
    onChange: e => pickCompany(e.target.value)
  }, ctx.companies.map(c => /*#__PURE__*/React.createElement("option", {
    key: c.id,
    value: c.id
  }, c.code))), factories.length > 0 && /*#__PURE__*/React.createElement("select", {
    className: "fy-select",
    title: "Factory",
    value: factoryId,
    onChange: e => pickFactory(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All factories"), factories.map(f => /*#__PURE__*/React.createElement("option", {
    key: f.id,
    value: f.id
  }, f.name))), /*#__PURE__*/React.createElement("select", {
    className: "fy-select",
    title: "Financial year",
    value: fy,
    onChange: e => pickFy(e.target.value)
  }, fys.map(y => {
    const [a, b] = y.split("-");
    return /*#__PURE__*/React.createElement("option", {
      key: y,
      value: y
    }, "FY ", a, "\u2013", (b || "").slice(2));
  })), /*#__PURE__*/React.createElement("div", {
    className: "who"
  }, me ? me.full_name || me.username : "", me && me.company ? " · " + me.company : ""))), /*#__PURE__*/React.createElement("div", {
    className: "content"
  }, page === "notifications" ? /*#__PURE__*/React.createElement(Notifications, {
    onChange: loadNotif
  }) : page === "group" ? /*#__PURE__*/React.createElement(GroupDash, {
    refreshKey: refreshKey
  }) : page === "owner" ? /*#__PURE__*/React.createElement(OwnerDash, {
    onNav: go,
    asOf: asOf,
    refreshKey: refreshKey
  }) : page === "factory" ? /*#__PURE__*/React.createElement(FactoryDash, {
    onNav: go,
    asOf: asOf,
    refreshKey: refreshKey
  }) : page === "accounts" ? /*#__PURE__*/React.createElement(AccountsDash, {
    onNav: go,
    asOf: asOf,
    refreshKey: refreshKey
  }) : page === "chemical" ? /*#__PURE__*/React.createElement(ChemicalDash, {
    onNav: go,
    asOf: asOf,
    refreshKey: refreshKey
  }) : /*#__PURE__*/React.createElement(Cur, null))));
}
function Root() {
  const [authed, setAuthed] = useState(!!getToken());
  return authed ? /*#__PURE__*/React.createElement(App, null) : /*#__PURE__*/React.createElement(Login, {
    onLogin: () => setAuthed(true)
  });
}
ReactDOM.createRoot(document.getElementById("root")).render(/*#__PURE__*/React.createElement(Root, null));