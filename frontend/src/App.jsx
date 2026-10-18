
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Bell,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  CreditCard,
  LayoutDashboard,
  LogOut,
  Menu,
  Mic,
  Plus,
  Search,
  Settings,
  Sparkles,
  Trash2,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import "./App.css";

const today = new Date();
const dateString = (offset) => {
  const date = new Date();
  date.setDate(date.getDate() + offset);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
};

const initialBills = [
  { id: 1, name: "Electricity", category: "Utilities", amount: 1850, dueDate: dateString(2), paid: false },
  { id: 2, name: "Internet", category: "Subscriptions", amount: 999, dueDate: dateString(4), paid: false },
  { id: 3, name: "House Rent", category: "Housing", amount: 12000, dueDate: dateString(6), paid: false },
  { id: 4, name: "Mobile Recharge", category: "Subscriptions", amount: 599, dueDate: dateString(-2), paid: false },
  { id: 5, name: "Water Bill", category: "Utilities", amount: 450, dueDate: dateString(-5), paid: true },
];

const API_BASE =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? `http://${window.location.hostname}:8000` : "");

function apiRequest(path, options = {}) {
  if (!API_BASE) {
    throw new Error("VITE_API_BASE_URL must be configured for production.");
  }

  return fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: "include",
  });
}

function fromApi(bill) {
  return {
    id: bill.id,
    name: bill.name,
    category: bill.category,
    amount: bill.amount,
    dueDate: bill.due_date,
    paid: bill.paid,
  };
}

function toApi(bill) {
  return {
    name: bill.name,
    category: bill.category,
    amount: Number(bill.amount),
    due_date: bill.dueDate,
    paid: Boolean(bill.paid),
  };
}

async function loadBills() {
  const response = await apiRequest("/bills");
  if (!response.ok) throw new Error("Could not load bills");

  const serverBills = await response.json();
  return serverBills.map(fromApi);
}


const money = (value) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);

function getStatus(bill) {
  if (bill.paid) return "Paid";
  if (bill.dueDate < dateString(0)) return "Overdue";
  if (bill.dueDate === dateString(0)) return "Due today";
  return "Upcoming";
}

function App() {
  const [bills, setBills] = useState(() => {
    try {
      const saved = localStorage.getItem("billbuddy-bills");
      return saved ? JSON.parse(saved) : initialBills;
    } catch {
      return initialBills;
    }
  });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const [modal, setModal] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [form, setForm] = useState({
    name: "",
    category: "Utilities",
    amount: "",
    dueDate: dateString(3),
  });
  const [notice, setNotice] = useState("");
  const [authChecked, setAuthChecked] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [dashboardPassword, setDashboardPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [isLoggingIn, setIsLoggingIn] = useState(false);

const initialized = useRef(false);

useEffect(() => {
  if (initialized.current) return;
  initialized.current = true;

  async function initialize() {
    let sessionAuthenticated = false;

    try {
      const sessionResponse = await apiRequest("/auth/session");
      if (!sessionResponse.ok) throw new Error("Could not check dashboard session");
      const session = await sessionResponse.json();
      sessionAuthenticated = session.authenticated;

      setIsAuthenticated(sessionAuthenticated);
      setAuthChecked(true);

      if (sessionAuthenticated) {
        const serverBills = await loadBills();
        setBills(serverBills);
      }
    } catch (error) {
      console.error("BillBuddy API error:", error);
      if (sessionAuthenticated) {
        setNotice("Could not load bills. Check that the backend is running.");
      } else {
        setAuthError("Could not connect to BillBuddy. Check that the backend is running.");
      }
      setAuthChecked(true);
    }
  }

  initialize();
}, []);

  useEffect(() => {
    localStorage.setItem("billbuddy-bills", JSON.stringify(bills));
  }, [bills]);

  async function login(event) {
    event.preventDefault();
    setIsLoggingIn(true);
    setAuthError("");
    let loginCompleted = false;

    try {
      const response = await apiRequest("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: dashboardPassword }),
      });

      if (!response.ok) {
        setAuthError(
          response.status === 401
            ? "The dashboard password is incorrect."
            : "Could not sign in. Check the backend configuration."
        );
        return;
      }

      loginCompleted = true;
      setIsAuthenticated(true);
      setDashboardPassword("");

      const serverBills = await loadBills();
      setBills(serverBills);
    } catch (error) {
      console.error("BillBuddy login error:", error);
      if (loginCompleted) {
        setNotice("Signed in, but could not load bills. Check the backend and try again.");
      } else {
        setAuthError("Could not sign in. Check that the backend is running.");
      }
    } finally {
      setIsLoggingIn(false);
    }
  }

  async function logout() {
    try {
      const response = await apiRequest("/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("Could not sign out");
      setIsAuthenticated(false);
      setDashboardPassword("");
    } catch (error) {
      console.error("BillBuddy logout error:", error);
      setNotice("Could not sign out. Please try again.");
    }
  }

  const totals = useMemo(() => {
    const unpaid = bills.filter((b) => !b.paid);
    const overdue = unpaid.filter((b) => b.dueDate < dateString(0));
    const upcoming = unpaid.filter((b) => b.dueDate >= dateString(0));
    const month = dateString(0).slice(0, 7);
    return {
      outstanding: unpaid.reduce((sum, b) => sum + Number(b.amount), 0),
      overdue: overdue.reduce((sum, b) => sum + Number(b.amount), 0),
      upcoming: upcoming.reduce((sum, b) => sum + Number(b.amount), 0),
      month: bills
        .filter((b) => b.dueDate.startsWith(month))
        .reduce((sum, b) => sum + Number(b.amount), 0),
      unpaidCount: unpaid.length,
      overdueCount: overdue.length,
    };
  }, [bills]);

  const visibleBills = useMemo(() => {
    return bills
      .filter((b) => {
        const matchesQuery = `${b.name} ${b.category}`.toLowerCase().includes(query.toLowerCase());
        const status = getStatus(b);
        const matchesFilter =
          filter === "All" ||
          (filter === "Upcoming" && (status === "Upcoming" || status === "Due today")) ||
          status === filter;
        return matchesQuery && matchesFilter;
      })
      .sort((a, b) => a.dueDate.localeCompare(b.dueDate));
  }, [bills, query, filter]);

  function openAdd() {
    setEditingId(null);
    setForm({ name: "", category: "Utilities", amount: "", dueDate: dateString(3) });
    setModal(true);
  }

  function openEdit(bill) {
    setEditingId(bill.id);
    setForm({
      name: bill.name,
      category: bill.category,
      amount: String(bill.amount),
      dueDate: bill.dueDate,
    });
    setModal(true);
  }

  
async function saveBill(event) {
  event.preventDefault();

  const amount = Number(form.amount);

  if (
    !form.name.trim() ||
    !form.dueDate ||
    !Number.isFinite(amount) ||
    amount <= 0
  ) {
    setNotice("Enter a bill name, a valid amount and a due date.");
    return;
  }

  const bill = {
    name: form.name.trim(),
    category: form.category,
    amount,
    dueDate: form.dueDate,
    paid: false,
  };

  try {
    const response = await apiRequest(
      editingId !== null
        ? `/bills/${editingId}`
        : "/bills",
      {
        method: editingId !== null ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          editingId !== null
            ? toApi(bill)
            : toApi(bill)
        ),
      }
    );

    if (!response.ok) throw new Error("Failed to save bill");

    const savedBill = fromApi(await response.json());

    setBills((old) =>
      editingId !== null
        ? old.map((b) => b.id === editingId ? savedBill : b)
        : [...old, savedBill]
    );

    setModal(false);
    setNotice(editingId !== null ? "Bill updated successfully." : "Bill added successfully.");
  } catch (error) {
    console.error(error);
    setNotice("Could not save bill. Check the backend and try again.");
  }
}


  
async function togglePaid(id) {
  const bill = bills.find((b) => b.id === id);
  if (!bill) return;

  try {
    const response = await apiRequest(`/bills/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paid: !bill.paid }),
    });

    if (!response.ok) throw new Error("Failed to update payment");

    const updated = fromApi(await response.json());

    setBills((old) =>
      old.map((b) => b.id === id ? updated : b)
    );
    setNotice("Payment status updated.");
  } catch (error) {
    console.error(error);
    setNotice("Could not update payment status.");
  }
}


  
async function deleteBill(id) {
  if (!window.confirm("Delete this bill permanently?")) return;

  try {
    const response = await apiRequest(`/bills/${id}`, {
      method: "DELETE",
    });

    if (!response.ok) throw new Error("Failed to delete bill");

    setBills((old) => old.filter((b) => b.id !== id));
    setNotice("Bill deleted.");
  } catch (error) {
    console.error(error);
    setNotice("Could not delete bill.");
  }
}


  const nextBill = bills
    .filter((b) => !b.paid && b.dueDate >= dateString(0))
    .sort((a, b) => a.dueDate.localeCompare(b.dueDate))[0];

  if (!authChecked) {
    return <main className="auth-shell"><p>Connecting to BillBuddy...</p></main>;
  }

  if (!isAuthenticated) {
    return (
      <main className="auth-shell">
        <form className="auth-card" onSubmit={login}>
          <span className="brand-mark"><Wallet size={22} /></span>
          <span className="modal-eyebrow">BILLBUDDY DASHBOARD</span>
          <h1>Sign in to BillBuddy</h1>
          <p>Enter your dashboard password to manage your bills.</p>
          <label className="auth-label">
            Dashboard password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={dashboardPassword}
              onChange={(event) => setDashboardPassword(event.target.value)}
            />
          </label>
          {authError && <span className="auth-error" role="alert">{authError}</span>}
          <button className="primary-button" type="submit" disabled={isLoggingIn}>
            {isLoggingIn ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "sidebar-open" : ""}`}>
        <a className="brand" href="#dashboard" onClick={() => setMobileMenu(false)}>
          <span className="brand-mark"><Wallet size={22} /></span>
          <span>billbuddy<span className="brand-dot">.</span></span>
        </a>

        <div className="workspace-label">WORKSPACE</div>
        <nav className="side-nav">
          <a className="nav-link active" href="#dashboard" onClick={() => setMobileMenu(false)}>
            <LayoutDashboard size={19} /> Dashboard
          </a>
          <a className="nav-link" href="#bills" onClick={() => setMobileMenu(false)}>
            <CreditCard size={19} /> My bills
            <span className="nav-count">{bills.length}</span>
          </a>
          <a className="nav-link" href="#upcoming" onClick={() => { setFilter("Upcoming"); setMobileMenu(false); }}>
            <CalendarDays size={19} /> Upcoming
          </a>
          <a className="nav-link" href="#insights" onClick={() => setMobileMenu(false)}>
            <Activity size={19} /> Insights
          </a>
        </nav>

        <div className="sidebar-bottom">
          <div className="helper-card">
            <span className="helper-icon"><Sparkles size={18} /></span>
            <strong>Less stress, more control.</strong>
            <p>Stay ahead of your bills and keep your finances organized.</p>
            <button className="helper-button" onClick={openAdd}>Add your first bill <ArrowUpRight size={15} /></button>
          </div>
          <div className="profile">
            <div className="avatar">C</div>
            <div className="profile-text"><strong>Charan</strong><span>Personal account</span></div>
            <ChevronDown size={16} />
          </div>
        </div>
      </aside>

      {mobileMenu && <button className="mobile-overlay" aria-label="Close menu" onClick={() => setMobileMenu(false)} />}

      <main className="main-content" id="dashboard">
        <header className="topbar">
          <button className="icon-button mobile-menu-button" aria-label="Open menu" onClick={() => setMobileMenu(!mobileMenu)}><Menu /></button>
          <div className="breadcrumb">Workspace <span>/</span> <strong>Dashboard</strong></div>
          <div className="topbar-actions">
            <span className="today-label"><CalendarDays size={16} /> {today.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</span>
            <button className="icon-button" title="Notifications" onClick={() => setNotice(`${totals.overdueCount} overdue bills need attention.`)}><Bell size={19} /></button>
            <button className="icon-button" title="Settings" onClick={() => setNotice("Your bills are synced with BillBuddy.")}><Settings size={19} /></button>
            <button className="icon-button" title="Sign out" aria-label="Sign out" onClick={logout}><LogOut size={18} /></button>
          </div>
        </header>

        <div className="page-content">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="live-dot" /> YOUR MONEY, IN CONTROL</div>
              <h1>Good to see you, Charan <span className="wave">✳</span></h1>
              <p className="subheading">Here's what's happening with your bills today.</p>
            </div>
            <button className="primary-button" onClick={openAdd}><Plus size={19} /> Add a bill</button>
          </section>

          <section className="hero-card">
            <div className="hero-copy">
              <span className="hero-tag"><Sparkles size={14} /> YOUR FINANCIAL OVERVIEW</span>
              <p className="hero-label">Total outstanding</p>
              <h2>{money(totals.outstanding)}</h2>
              <p className="hero-description">Across {totals.unpaidCount} unpaid {totals.unpaidCount === 1 ? "bill" : "bills"}. You're one step closer to staying on top of everything.</p>
              <a href="#bills" className="hero-link">View all bills <ArrowUpRight size={16} /></a>
            </div>
            <div className="hero-visual" aria-hidden="true">
              <div className="orbit orbit-one" />
              <div className="orbit orbit-two" />
              <div className="hero-coin"><Wallet size={45} /></div>
              <div className="floating-chip chip-one"><CheckCircle2 size={16} /> Organized</div>
              <div className="floating-chip chip-two"><Zap size={16} /> On track</div>
            </div>
          </section>

          <section className="stats-grid" id="insights">
            <article className="stat-card">
              <div className="stat-top"><span className="stat-icon violet"><CalendarDays size={19} /></span><span className="stat-trend">This month</span></div>
              <p className="stat-label">Bills due this month</p>
              <h3>{money(totals.month)}</h3>
              <p className="stat-foot">Based on due dates</p>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span className="stat-icon orange"><Clock3 size={19} /></span><span className="stat-trend warning">{totals.overdueCount ? "Needs attention" : "All clear"}</span></div>
              <p className="stat-label">Overdue payments</p>
              <h3>{money(totals.overdue)}</h3>
              <p className="stat-foot">{totals.overdueCount} {totals.overdueCount === 1 ? "bill" : "bills"} past due</p>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span className="stat-icon green"><ArrowUpRight size={19} /></span><span className="stat-trend positive">Upcoming</span></div>
              <p className="stat-label">Yet to be paid</p>
              <h3>{money(totals.upcoming)}</h3>
              <p className="stat-foot">Due today or later</p>
            </article>
          </section>

          <section className="next-payment" id="upcoming">
            <div className="next-icon"><CalendarDays size={23} /></div>
            <div className="next-copy">
              <span className="next-kicker">COMING UP NEXT</span>
              {nextBill ? <>
                <strong>{nextBill.name}</strong>
                <span>Due {new Date(`${nextBill.dueDate}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "long" })}</span>
              </> : <><strong>You're all caught up!</strong><span>No upcoming unpaid bills.</span></>}
            </div>
            {nextBill && <strong className="next-amount">{money(nextBill.amount)}</strong>}
          </section>

          <section className="bills-section" id="bills">
            <div className="section-heading">
              <div><h2>Your bills</h2><p>Everything you need to keep track of.</p></div>
              <button className="text-button" onClick={openAdd}><Plus size={17} /> New bill</button>
            </div>

            <div className="table-toolbar">
              <div className="search-box"><Search size={18} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search bills..." aria-label="Search bills" /></div>
              <div className="filter-group">
                {["All", "Upcoming", "Overdue", "Paid"].map((item) => <button key={item} onClick={() => setFilter(item)} className={`filter-button ${filter === item ? "selected" : ""}`}>{item}</button>)}
              </div>
            </div>

            <div className="bill-list">
              <div className="bill-list-header"><span>BILL DETAILS</span><span>AMOUNT</span><span>DUE DATE</span><span>STATUS</span><span>ACTIONS</span></div>
              {visibleBills.map((bill) => {
                const status = getStatus(bill);
                const iconClass = bill.category === "Housing" ? "bill-icon housing" : bill.category === "Subscriptions" ? "bill-icon subscription" : "bill-icon utility";
                return (
                  <article className="bill-row" key={bill.id}>
                    <div className="bill-name-cell"><span className={iconClass}>{bill.category === "Housing" ? <Wallet size={19} /> : bill.category === "Subscriptions" ? <CreditCard size={19} /> : <Zap size={19} />}</span><span className="bill-name-text"><strong>{bill.name}</strong><span>{bill.category}</span></span></div>
                    <strong className="bill-amount">{money(bill.amount)}</strong>
                    <span className="bill-date">{new Date(`${bill.dueDate}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</span>
                    <span className={`status-pill ${status.toLowerCase().replace(" ", "-")}`}><span />{status}</span>
                    <div className="bill-actions">
                      <button className="action-button" title={bill.paid ? "Mark unpaid" : "Mark paid"} onClick={() => togglePaid(bill.id)}><Check size={16} /></button>
                      <button className="action-button" title="Edit bill" onClick={() => openEdit(bill)}><Settings size={16} /></button>
                      <button className="action-button delete-action" title="Delete bill" onClick={() => deleteBill(bill.id)}><Trash2 size={16} /></button>
                    </div>
                  </article>
                );
              })}
              {visibleBills.length === 0 && <div className="empty-state"><Search size={28} /><strong>No bills found</strong><span>Try a different search or add a new bill.</span><button className="primary-button" onClick={openAdd}><Plus size={17} /> Add a bill</button></div>}
            </div>
            <div className="list-footer"><span>Showing {visibleBills.length} of {bills.length} bills</span><span><span className="live-dot" /> Saved and synced</span></div>
          </section>

          <section className="voice-banner">
            <div className="voice-banner-icon"><Mic size={25} /></div>
            <div className="voice-banner-copy"><strong>Your bills. Just a conversation away.</strong><span>Voice-powered bill management is the next step in your BillBuddy experience.</span></div>
            <button className="voice-button" onClick={() => setNotice("Voice assistant integration is our next milestone.")}><Mic size={17} /> Voice assistant <ArrowDownRight size={16} /></button>
          </section>

          <footer className="footer"><span>© 2026 BillBuddy</span><span>Made for a more organized you <span className="footer-heart">♥</span></span></footer>
        </div>
      </main>

      {notice && <div className="toast" role="status"><CheckCircle2 size={18} />{notice}<button aria-label="Dismiss" onClick={() => setNotice("")}><X size={16} /></button></div>}

      {modal && <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setModal(false); }}>
        <form className="bill-modal" onSubmit={saveBill}>
          <div className="modal-heading"><div><span className="modal-eyebrow">{editingId !== null ? "UPDATE DETAILS" : "KEEP THINGS ORGANIZED"}</span><h2>{editingId !== null ? "Edit your bill" : "Add a new bill"}</h2><p>Enter the details below to keep your payments on track.</p></div><button type="button" className="icon-button" onClick={() => setModal(false)} aria-label="Close"><X size={20} /></button></div>
          <label>Bill name<input required autoFocus placeholder="e.g. Electricity bill" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Category<select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}><option>Utilities</option><option>Subscriptions</option><option>Housing</option><option>Insurance</option><option>Education</option><option>Other</option></select></label>
          <label>Amount (₹)<input required type="number" min="1" step="0.01" placeholder="e.g. 1850" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
          <label>Due date<input required type="date" value={form.dueDate} onChange={(e) => setForm({ ...form, dueDate: e.target.value })} /></label>
          <div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setModal(false)}>Cancel</button><button type="submit" className="primary-button"><Check size={18} /> {editingId !== null ? "Save changes" : "Add bill"}</button></div>
        </form>
      </div>}
    </div>
  );
}

export default App;
