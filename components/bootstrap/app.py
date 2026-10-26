from proteus import Component


def setup():
    component = Component()

    @component.get("/")
    def index():
        body = """
<style>
  :root {
    --ink: #101218;
    --text: #f4f1ea;
    --muted: #a39888;
    --faint: #7c7468;
    --line: #2c3140;
    --gold: #f0d7a4;
    --gold-deep: #8a6a32;
    --sea: #8fd0c8;
    --panel: rgba(244, 241, 234, 0.035);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    padding: 2.75rem 1.25rem 4rem;
    background:
      radial-gradient(880px 460px at 8% -12%, rgba(143, 208, 200, 0.16), transparent 58%),
      radial-gradient(620px 380px at 100% 0%, rgba(240, 215, 164, 0.12), transparent 52%),
      var(--ink);
    color: var(--text);
    font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: rgba(240, 215, 164, 0.32); }
  .shell { max-width: 44rem; margin: 0 auto; }
  .mast {
    display: grid;
    grid-template-columns: minmax(0, 1.45fr) minmax(12rem, 0.7fr);
    gap: 2rem;
    align-items: end;
    margin-bottom: 1.6rem;
  }
  .kicker {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 0;
    color: var(--gold);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }
  .mark {
    width: 0.8rem;
    height: 0.8rem;
    border-radius: 42% 58% 48% 52%;
    background: linear-gradient(135deg, var(--sea), var(--gold));
    box-shadow: 0 0 16px rgba(143, 208, 200, 0.45);
    animation: drift 8s ease-in-out infinite;
  }
  @keyframes drift {
    50% { border-radius: 62% 38% 56% 44%; transform: rotate(20deg); }
  }
  h1 {
    margin: 0.7rem 0 0.65rem;
    font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    font-size: clamp(3rem, 8vw, 4.6rem);
    font-weight: 500;
    letter-spacing: -0.035em;
    line-height: 0.9;
  }
  .lede {
    margin: 0;
    max-width: 34rem;
    color: var(--muted);
    font-size: 1.05rem;
  }
  .steps {
    list-style: none;
    margin: 0;
    padding: 0;
    border-top: 1px solid var(--line);
  }
  .steps li {
    display: flex;
    gap: 0.75rem;
    align-items: baseline;
    padding: 0.55rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.92rem;
  }
  .steps span {
    color: var(--gold);
    font-size: 0.72rem;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.08em;
  }
  .composer {
    background:
      linear-gradient(180deg, rgba(240, 215, 164, 0.06), transparent 32%),
      var(--panel);
    border: 1px solid var(--line);
    border-radius: 18px;
    padding: 1.05rem 1.05rem 0.95rem;
    box-shadow: 0 28px 70px rgba(0, 0, 0, 0.32);
  }
  .shell label {
    display: block;
    margin-bottom: 0.45rem;
    color: var(--muted);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }
  .shell textarea {
    display: block;
    width: 100%;
    min-height: 9.5rem;
    margin: 0;
    padding: 0.9rem 1rem;
    resize: vertical;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #0c0e14;
    color: var(--text);
    font: 15px/1.55 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  .shell textarea::placeholder { color: var(--faint); }
  .shell textarea:focus {
    outline: none;
    border-color: var(--sea);
    box-shadow: 0 0 0 3px rgba(143, 208, 200, 0.18);
  }
  .actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-top: 0.85rem;
  }
  .hint {
    margin: 0;
    color: var(--faint);
    font-size: 0.82rem;
  }
  .shell button {
    appearance: none;
    border: 0;
    border-radius: 999px;
    padding: 0.68rem 1.2rem;
    background: var(--gold);
    color: #1c1508;
    font: inherit;
    font-size: 0.92rem;
    font-weight: 650;
    letter-spacing: 0.01em;
    cursor: pointer;
    transition: background 0.15s ease, transform 0.15s ease;
  }
  .shell button:hover { background: #f7e7c4; }
  .shell button:active { transform: translateY(1px); }
  .shell button:disabled { opacity: 0.55; cursor: progress; }
  .log { margin-top: 1.85rem; }
  .log-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.35rem;
  }
  .log-head h2 {
    margin: 0;
    color: var(--muted);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }
  .log-head p {
    margin: 0;
    color: var(--faint);
    font-size: 0.75rem;
  }
  #result { min-height: 2.4rem; }
  #result .tasks:empty::before {
    content: "Nothing queued yet. The first prompt lands here.";
    display: block;
    padding: 0.7rem 0 0.2rem;
    color: var(--faint);
    font-size: 0.92rem;
  }
  .task {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.75rem;
    align-items: start;
    padding: 0.8rem 0;
    border-bottom: 1px solid var(--line);
    overflow-wrap: anywhere;
  }
  .task strong {
    margin-top: 0.12rem;
    padding: 0.16rem 0.5rem;
    border-radius: 999px;
    background: var(--gold);
    color: #1c1508;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .foot {
    display: flex;
    gap: 1.25rem;
    margin-top: 1.8rem;
    font-size: 0.88rem;
  }
  .shell a {
    color: var(--gold);
    text-decoration-thickness: 1px;
    text-underline-offset: 0.18em;
  }
  .shell a:hover { color: #f7e7c4; }
  @media (max-width: 720px) {
    body { padding-top: 1.75rem; }
    .mast { grid-template-columns: 1fr; gap: 1.15rem; }
    .actions { align-items: stretch; flex-direction: column; }
    .shell button { width: 100%; }
  }
  @media (prefers-reduced-motion: reduce) {
    .mark { animation: none; }
    .shell button { transition: none; }
  }
</style>
<main class="shell">
  <header class="mast">
    <div>
      <p class="kicker"><span class="mark" aria-hidden="true"></span> polymorphic app</p>
      <h1>Proteus</h1>
      <p class="lede">The coordinator swaps components in as they are built. Components can ask the coding agent through Temporal, and CSS they register is rendered on every component page.</p>
    </div>
    <ol class="steps">
      <li><span>01</span> Describe the change</li>
      <li><span>02</span> A component is built</li>
      <li><span>03</span> The live route swaps</li>
    </ol>
  </header>
  <section class="composer" aria-label="Prompt">
    <form method="post" action="/__proteus/api/tasks" hx-post="/__proteus/api/tasks" hx-target="#result" hx-disabled-elt="find button" hx-on::after-request="if (event.detail.successful) this.reset()">
      <label for="proteus-prompt">Prompt</label>
      <textarea id="proteus-prompt" name="prompt" rows="7" required placeholder="Ask Proteus to change the app"></textarea>
      <div class="actions">
        <p class="hint">Ctrl or ⌘ Enter to send.</p>
        <button type="submit">Send</button>
      </div>
    </form>
  </section>
  <section class="log" aria-labelledby="mutations">
    <div class="log-head">
      <h2 id="mutations">Mutations</h2>
      <p>Refreshes every 3s</p>
    </div>
    <div id="result" hx-get="/__proteus/api/tasks/html" hx-trigger="load, every 3s"></div>
  </section>
  <nav class="foot">
    <a href="/chat">Chat</a>
    <a href="/llm">LLM</a>
    <a href="/notes">Notes</a>
    <a href="/__proteus/introspect">Introspection</a>
    <a href="/echo">Echo</a>
  </nav>
</main>
<script>
(function () {
  var field = document.getElementById("proteus-prompt");
  if (!field || !field.form) return;
  field.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      field.form.requestSubmit();
    }
  });
})();
</script>
"""
        return component.page("Proteus", body)

    return component
