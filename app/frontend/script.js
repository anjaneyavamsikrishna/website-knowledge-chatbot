(() => {
  const WELCOME =
    "Hello! Welcome to PlacidWay. I can help you explore available treatment information, clinics, packages, and pricing based on our website content. What would you like to know?";
  const EXAMPLES = [
    "What does the Alternative Adrenal Cancer Treatment package include?",
    "What is on the ITC Immunity Therapy Center price list?",
    "Tell me about the ITC Immunity Therapy Center.",
  ];

  const messagesEl = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("question");
  const sendBtn = document.getElementById("send-btn");
  const clearBtn = document.getElementById("clear-btn");

  const newId = () =>
    (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9_-]/g, "");
  let sessionId = sessionStorage.getItem("pw_session") || newId();
  sessionStorage.setItem("pw_session", sessionId);
  let busy = false;

  function scrollDown() { messagesEl.scrollTop = messagesEl.scrollHeight; }

  function addMessage(text, who, extraClass) {
    const div = document.createElement("div");
    div.className = `msg ${who}${extraClass ? " " + extraClass : ""}`;
    div.setAttribute("dir", "auto"); // correct rendering for Arabic and other RTL languages
    div.textContent = text; // textContent only: model output is never injected as HTML
    messagesEl.appendChild(div);
    scrollDown();
    return div;
  }

  function addSources(container, sources) {
    if (!sources || !sources.length) return;
    const box = document.createElement("div");
    box.className = "sources";
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = sources.length > 1 ? "Sources" : "Source";
    box.appendChild(label);
    sources.forEach((s) => {
      const a = document.createElement("a");
      a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.textContent = s.title || s.url;
      box.appendChild(a);
    });
    container.appendChild(box);
  }

  function addLeadForm(container) {
    const wrap = document.createElement("form");
    wrap.className = "lead";
    wrap.innerHTML = `
      <input type="text" name="name" placeholder="Your name" required minlength="2" maxlength="100" aria-label="Your name">
      <input type="email" name="email" placeholder="Your email" required maxlength="200" aria-label="Your email">
      <label class="consent"><input type="checkbox" name="consent" required>
        <span>I agree to have my name and email saved in this demo application.</span></label>
      <button type="submit">Save my details</button>
      <span class="note">Saving details here does not send a quote request to PlacidWay.</span>`;
    wrap.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(wrap);
      const btn = wrap.querySelector("button");
      btn.disabled = true;
      try {
        const res = await fetch("/api/leads", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: fd.get("name"), email: fd.get("email"), consent: fd.get("consent") === "on", session_id: sessionId }),
        });
        const data = await res.json();
        wrap.replaceWith(Object.assign(document.createElement("div"), {
          className: "note", textContent: res.ok ? data.message : (formatDetail(data.detail) || "Could not save your details."),
        }));
      } catch (e) {
        btn.disabled = false;
        addMessage("Could not reach the server. Please try again.", "bot", "error");
      }
    });
    container.appendChild(wrap);
  }

  function formatDetail(detail) {
    if (!detail) return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
    return "";
  }

  function showWelcome() {
    const div = addMessage(WELCOME, "bot");
    const chips = document.createElement("div");
    chips.className = "chips";
    EXAMPLES.forEach((q) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip"; b.textContent = q;
      b.addEventListener("click", () => send(q));
      chips.appendChild(b);
    });
    div.appendChild(chips);
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true;
    addMessage(text, "user");
    input.value = ""; autoGrow();

    const typing = document.createElement("div");
    typing.className = "msg bot";
    typing.innerHTML = '<span class="typing" aria-label="Assistant is typing"><i></i><i></i><i></i></span>';
    messagesEl.appendChild(typing); scrollDown();

    try {
      const res = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, session_id: sessionId }),
      });
      const data = await res.json().catch(() => ({}));
      typing.remove();
      if (!res.ok) {
        const msg = res.status === 429 ? "You're sending messages too quickly. Please wait a moment."
          : formatDetail(data.detail) || "Something went wrong. Please try again.";
        addMessage(msg, "bot", "error");
      } else {
        const div = addMessage(data.answer, "bot");
        addSources(div, data.sources);
        if (data.suggest_lead_capture) addLeadForm(div);
        scrollDown();
      }
    } catch (e) {
      typing.remove();
      addMessage("I couldn't reach the server. Please check your connection and try again.", "bot", "error");
    } finally {
      busy = false; sendBtn.disabled = false; input.focus();
    }
  }

  function autoGrow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; }
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });

  clearBtn.addEventListener("click", async () => {
    try { await fetch(`/api/chat/${sessionId}`, { method: "DELETE" }); } catch (_) { /* best effort */ }
    sessionId = newId(); sessionStorage.setItem("pw_session", sessionId);
    messagesEl.textContent = ""; showWelcome(); input.focus();
  });

  showWelcome();
})();
