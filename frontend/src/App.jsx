import { useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:5000";

export default function App() {
  const [token, setToken] = useState(localStorage.getItem("token") || "");
  const [user, setUser] = useState(null);
  const [freeLimit, setFreeLimit] = useState(2);
  const [documents, setDocuments] = useState([]);
  const [pendingDocs, setPendingDocs] = useState([]);
  const [audits, setAudits] = useState([]);
  const [showAudits, setShowAudits] = useState(false);
  const [status, setStatus] = useState("");
  const [docQuestions, setDocQuestions] = useState({});
  const [loadingQuestionsFor, setLoadingQuestionsFor] = useState(null);
  const [generatingFor, setGeneratingFor] = useState(null);
  const [authForm, setAuthForm] = useState({ email: "", password: "" });
  const [uploadForm, setUploadForm] = useState({ title: "", course_code: "", year: "", term: "", kind: "paper", notes: "", file: null });
  const [feedback, setFeedback] = useState({ message: "", contact: "" });

  const authHeaders = useMemo(() => (token ? { Authorization: `Bearer ${token}` } : {}), [token]);
  const isPaid = user && (user.subscription_status === "paid" || user.role === "admin");

  useEffect(() => {
    fetchDocuments();
    if (token) {
      fetchProfile();
    } else {
      setUser(null);
    }
  }, [token]);

  useEffect(() => {
    if (user?.role === "admin") {
      fetchPending();
    }
  }, [user]);

  async function fetchProfile() {
    try {
      const res = await fetch(`${API_BASE}/api/me`, { headers: { ...authHeaders } });
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || "Could not load profile");
        return;
      }
      setUser(data.user);
      setFreeLimit(data.free_limit || 2);
    } catch (err) {
      setStatus("Could not reach API");
    }
  }

  async function fetchDocuments() {
    try {
      const res = await fetch(`${API_BASE}/api/docs`, { headers: { ...authHeaders } });
      const data = await res.json();
      if (res.ok) setDocuments(data.documents || []);
    } catch {
      setStatus("Could not load documents");
    }
  }

  async function fetchPending() {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/api/docs?status=pending`, { headers: { ...authHeaders } });
      const data = await res.json();
      if (res.ok) setPendingDocs(data.documents || []);
    } catch {
      setStatus("Could not load pending docs");
    }
  }

  async function fetchAudits() {
    if (!token || user?.role !== "admin") return;
    setStatus("Loading downloads...");
    try {
      const res = await fetch(`${API_BASE}/api/admin/downloads?limit=100`, { headers: { ...authHeaders } });
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || "Failed to load audits");
        return;
      }
      setAudits(data.audits || []);
      setStatus("Downloads loaded");
    } catch {
      setStatus("Could not load audits");
    }
  }

  async function handleAuth(endpoint) {
    setStatus("Working...");
    try {
      const res = await fetch(`${API_BASE}/api/auth/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authForm),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || "Auth failed");
        return;
      }
      setToken(data.access_token);
      localStorage.setItem("token", data.access_token);
      setUser(data.user);
      setStatus("Welcome!");
    } catch (err) {
      setStatus("Cannot reach API");
    }
  }

  function logout() {
    setToken("");
    localStorage.removeItem("token");
    setUser(null);
  }

  async function handleUpload(e) {
    e.preventDefault();
    if (!uploadForm.file) {
      setStatus("Select a PDF to upload");
      return;
    }
    setStatus("Uploading...");
    const fd = new FormData();
    Object.entries(uploadForm).forEach(([key, val]) => {
      if (key === "file") return;
      fd.append(key, val);
    });
    fd.append("file", uploadForm.file);
    const res = await fetch(`${API_BASE}/api/docs`, {
      method: "POST",
      headers: { ...authHeaders },
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.error || "Upload failed");
      return;
    }
    setStatus("Submitted for review");
    setUploadForm({ title: "", course_code: "", year: "", term: "", kind: "paper", notes: "", file: null });
    fetchDocuments();
    fetchPending();
  }

  async function viewDoc(doc) {
    if (!token) {
      setStatus("Login to view");
      return;
    }
    setStatus("Preparing view...");
    const res = await fetch(`${API_BASE}/api/docs/${doc.id}/download?view=1`, { headers: { ...authHeaders } });
    if (res.status === 402) {
      const data = await res.json();
      setStatus(data.error || "Upgrade required");
      return;
    }
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const data = await res.json();
      if (data.download_url) {
        window.open(data.download_url, "_blank", "noopener");
        setStatus("Opened in viewer");
        fetchProfile();
        return;
      }
      setStatus(data.error || "Could not view");
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setStatus(data.error || "Could not view");
      return;
    }
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setStatus("Opened in viewer");
    fetchProfile();
  }

  async function deleteDoc(docId) {
    if (!token) {
      setStatus("Login first");
      return;
    }
    if (!window.confirm("Delete this document? This cannot be undone.")) return;
    const res = await fetch(`${API_BASE}/api/docs/${docId}`, {
      method: "DELETE",
      headers: { ...authHeaders },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(data.error || "Delete failed");
      return;
    }
    setStatus("Document deleted");
    fetchDocuments();
    fetchPending();
  }

  async function approve(docId, action) {
    const res = await fetch(`${API_BASE}/api/docs/${docId}/${action}`, { method: "POST", headers: { ...authHeaders } });
    if (!res.ok) {
      const data = await res.json();
      setStatus(data.error || "Update failed");
      return;
    }
    setStatus(`Marked ${action}`);
    fetchDocuments();
    fetchPending();
  }

  async function fetchDocQuestions(docId) {
    if (!token) {
      setStatus("Login to view questions");
      return;
    }
    setLoadingQuestionsFor(docId);
    setStatus("Loading practice questions...");
    try {
      const res = await fetch(`${API_BASE}/api/docs/${docId}/questions`, { headers: { ...authHeaders } });
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || "Could not load questions");
        return;
      }
      setDocQuestions((prev) => ({ ...prev, [docId]: data.questions || [] }));
      setStatus(data.questions?.length ? "Questions loaded" : "No questions yet — generate some!");
    } catch {
      setStatus("Could not load questions");
    } finally {
      setLoadingQuestionsFor(null);
    }
  }

  async function generateDocQuestions(doc) {
    if (!token) {
      setStatus("Login first");
      return;
    }
    if (!isPaid && user?.role !== "admin") {
      setStatus("Upgrade to generate practice questions");
      return;
    }
    setGeneratingFor(doc.id);
    setStatus("Generating practice questions...");
    try {
      const res = await fetch(`${API_BASE}/api/docs/${doc.id}/questions`, { method: "POST", headers: { ...authHeaders } });
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || "Generation failed");
        return;
      }
      setDocQuestions((prev) => ({ ...prev, [doc.id]: data.questions || [] }));
      setStatus("Questions ready");
      fetchProfile();
    } catch {
      setStatus("Could not generate questions");
    } finally {
      setGeneratingFor(null);
    }
  }

  async function sendFeedback(e) {
    e.preventDefault();
    if (!feedback.message.trim()) {
      setStatus("Add feedback text");
      return;
    }
    const res = await fetch(`${API_BASE}/api/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify(feedback),
    });
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.error || "Feedback failed");
      return;
    }
    setStatus("Thanks for the feedback!");
    setFeedback({ message: "", contact: "" });
  }

  async function startCheckout() {
    if (!token) {
      setStatus("Login first");
      return;
    }
    setStatus("Redirecting to checkout...");
    const res = await fetch(`${API_BASE}/api/billing/checkout`, { method: "POST", headers: { ...authHeaders } });
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.error || "Payment failed");
      return;
    }
    if (data.checkout_url) {
      window.location.href = data.checkout_url;
      return;
    }
    if (data.simulated) {
      setStatus("Plan upgraded (simulated)");
      fetchProfile();
    }
  }

  const lockedDocIds = useMemo(() => {
    if (!user) return new Set(documents.map((d) => d.id));
    if (isPaid) return new Set();
    const used = new Set(user.accessed_doc_ids || []);
    const remaining = Math.max(freeLimit - used.size, 0);
    const locked = new Set();
    documents.forEach((doc) => {
      if (used.has(doc.id)) return;
      if (locked.size >= remaining) locked.add(doc.id);
    });
    return locked;
  }, [user, documents, isPaid, freeLimit]);

  return (
    <div className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">McMaster Past Papers</p>
          <h1>Upload, review, and access past exams</h1>
          <p className="subhead">Uploads are admin-approved. Free users can unlock {freeLimit} documents; upgrade for full access.</p>
        </div>
        <div className="status">{status}</div>
      </header>

      {!token ? (
        <section className="card auth-card">
          <h2>Sign up / Log in</h2>
          <div className="form-grid">
            <label>Email</label>
            <input value={authForm.email} onChange={(e) => setAuthForm({ ...authForm, email: e.target.value })} placeholder="you@school.ca" />
            <label>Password</label>
            <input type="password" value={authForm.password} onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })} placeholder="••••••••" />
          </div>
          <div className="actions">
            <button onClick={() => handleAuth("register")}>Create account</button>
            <button className="secondary" onClick={() => handleAuth("login")}>
              Log in
            </button>
          </div>
        </section>
      ) : (
        <section className="card compact">
          <div className="card-header">
            <div>
              <p className="eyebrow">Signed in</p>
              <h3>{user?.email}</h3>
              <p className="muted">
                Role: {user?.role} · Plan: {user?.subscription_status} · Free remaining: {user?.free_docs_remaining}
              </p>
            </div>
            <button className="ghost" onClick={logout}>
              Logout
            </button>
          </div>
        </section>
      )}

      <section className="grid">
        <div className="card">
          <div className="card-header">
            <h2>Plans</h2>
          </div>
          <div className="plans">
            <div className={`plan ${!isPaid ? "selected" : ""}`}>
              <p className="eyebrow">Free</p>
              <h3>$0</h3>
              <ul>
                <li>Access {freeLimit} approved documents</li>
                <li>Submit uploads for approval</li>
                <li>Feedback to admin</li>
              </ul>
            </div>
            <div className={`plan ${isPaid ? "selected" : ""}`}>
              <p className="eyebrow">Pro</p>
              <h3>$</h3>
              <ul>
                <li>Unlimited downloads</li>
                <li>No blur/locks</li>
                <li>Priority support</li>
              </ul>
              <button onClick={startCheckout} disabled={isPaid}>
                {isPaid ? "You are Pro" : "Upgrade"}
              </button>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <h2>Upload past paper / solution</h2>
          </div>
          {token ? (
            <form className="form-grid" onSubmit={handleUpload}>
              <label>Title</label>
              <input value={uploadForm.title} onChange={(e) => setUploadForm({ ...uploadForm, title: e.target.value })} placeholder="Linear Algebra 1ZC3 Final 2022" />
              <label>Course code</label>
              <input value={uploadForm.course_code} onChange={(e) => setUploadForm({ ...uploadForm, course_code: e.target.value })} placeholder="MATH 1ZC3" />
              <label>Year / Term</label>
              <div className="two-col">
                <input value={uploadForm.year} onChange={(e) => setUploadForm({ ...uploadForm, year: e.target.value })} placeholder="2023" />
                <input value={uploadForm.term} onChange={(e) => setUploadForm({ ...uploadForm, term: e.target.value })} placeholder="Fall" />
              </div>
              <label>Type</label>
              <select value={uploadForm.kind} onChange={(e) => setUploadForm({ ...uploadForm, kind: e.target.value })}>
                <option value="paper">Past paper</option>
                <option value="solution">Solved solutions</option>
              </select>
              <label>Notes</label>
              <input value={uploadForm.notes} onChange={(e) => setUploadForm({ ...uploadForm, notes: e.target.value })} placeholder="Any context for this file" />
              <label>PDF file</label>
              <input type="file" accept=".pdf" onChange={(e) => setUploadForm({ ...uploadForm, file: e.target.files?.[0] || null })} />
              <button type="submit">Submit for approval</button>
            </form>
          ) : (
            <p className="muted">Log in to upload.</p>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-header">
          <h2>Library</h2>
          <small className="muted">Approved uploads</small>
        </div>
        <div className="doc-grid">
          {documents.map((doc) => {
            const locked = lockedDocIds.has(doc.id);
            return (
                <div className={`doc-card ${locked ? "locked" : ""}`} key={doc.id}>
                  <div className="doc-meta">
                  <p className="eyebrow">{doc.kind === "solution" ? "Solution" : "Past paper"}</p>
                  <h3>{doc.title}</h3>
                  <p className="muted">
                    {doc.course_code} · {doc.term || "Term"} {doc.year || ""} · Uploaded {new Date(doc.created_at).toLocaleDateString()}
                  </p>
                  {doc.notes && <p className="notes">{doc.notes}</p>}
                </div>
                {locked && <div className="blur-overlay">Locked</div>}
                <div className="doc-actions">
                  {user?.role === "admin" && (
                    <button className="secondary" onClick={() => deleteDoc(doc.id)}>
                      Delete
                    </button>
                  )}
                  <button onClick={() => viewDoc(doc)} disabled={locked}>
                    {locked ? "Upgrade to unlock" : "View PDF"}
                  </button>
                  {user && (
                    <>
                      <button className="secondary" onClick={() => fetchDocQuestions(doc.id)} disabled={loadingQuestionsFor === doc.id}>
                        {loadingQuestionsFor === doc.id ? "Loading..." : "View questions"}
                      </button>
                      <button
                        onClick={() => generateDocQuestions(doc)}
                        disabled={generatingFor === doc.id || (!isPaid && user?.role !== "admin")}
                      >
                        {generatingFor === doc.id ? "Generating..." : !isPaid && user?.role !== "admin" ? "Upgrade to generate" : "Generate questions"}
                      </button>
                    </>
                  )}
                </div>
                {docQuestions[doc.id]?.length > 0 && (
                  <div className="qa-list">
                    {docQuestions[doc.id].map((qa, idx) => (
                      <div className="qa-item" key={idx}>
                        <p className="muted">Q{idx + 1}. {qa.question}</p>
                        <p className="answer">{qa.answer}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {documents.length === 0 && <p className="muted">No documents yet.</p>}
        </div>
      </section>

      {user?.role === "admin" && (
        <section className="card">
          <div className="card-header">
            <h2>Pending approvals</h2>
            <small className="muted">{pendingDocs.length} waiting</small>
          </div>
          {pendingDocs.length === 0 && <p className="muted">Nothing to review.</p>}
          <div className="doc-grid">
            {pendingDocs.map((doc) => (
              <div className="doc-card pending" key={doc.id}>
                <div className="doc-meta">
                  <p className="eyebrow">Pending</p>
                  <h3>{doc.title}</h3>
                  <p className="muted">
                    {doc.course_code} · {doc.term || "Term"} {doc.year || ""}
                  </p>
                  <p className="muted">Uploader: {doc.uploader?.email}</p>
                </div>
                <div className="doc-actions">
                  <button className="secondary" onClick={() => approve(doc.id, "reject")}>
                    Reject
                  </button>
                  <button onClick={() => approve(doc.id, "approve")}>Approve</button>
                  <button className="secondary" onClick={() => deleteDoc(doc.id)}>
                    Delete
                  </button>
                  <button onClick={() => viewDoc(doc)}>View</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {user?.role === "admin" && (
        <section className="card">
          <div className="card-header">
            <h2>Download audits</h2>
            <div className="actions">
              <button className="secondary" onClick={() => setShowAudits((v) => !v)}>
                {showAudits ? "Hide" : "Show"}
              </button>
              {showAudits && (
                <button onClick={fetchAudits}>
                  Refresh
                </button>
              )}
            </div>
          </div>
          {showAudits && (
            <div className="table audits">
              <div className="row head">
                <span>When</span>
                <span>User</span>
                <span>Doc</span>
                <span>IP</span>
              </div>
              {audits.map((a) => (
                <div className="row" key={a.id}>
                  <span>{new Date(a.created_at).toLocaleString()}</span>
                  <span>{a.user_email || `User #${a.user_id || "?"}`}</span>
                  <span>{a.doc_title || `Doc #${a.document_id}`}</span>
                  <span className="muted">{a.ip_address || "-"}</span>
                </div>
              ))}
              {audits.length === 0 && <p className="muted">No audits yet.</p>}
            </div>
          )}
        </section>
      )}

      <section className="card">
        <div className="card-header">
          <h2>Feedback</h2>
          <small className="muted">Tell the admin what to fix or add.</small>
        </div>
        <form className="form-grid" onSubmit={sendFeedback}>
          <label>Message</label>
          <textarea value={feedback.message} onChange={(e) => setFeedback({ ...feedback, message: e.target.value })} placeholder="What should we improve?" />
          <label>Contact (optional)</label>
          <input value={feedback.contact} onChange={(e) => setFeedback({ ...feedback, contact: e.target.value })} placeholder="Email or handle" />
          <button type="submit">Send feedback</button>
        </form>
      </section>
    </div>
  );
}
