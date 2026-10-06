import { useEffect, useState } from "react";

export default function App() {
  const [ideas, setIdeas] = useState([]);
  const [content, setContent] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const res = await fetch("/api/ideas");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setIdeas(await res.json());
      setError("");
    } catch (e) {
      setError(`Could not load ideas (${e.message})`);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function submit(e) {
    e.preventDefault();
    if (!content.trim()) return;
    setBusy(true);
    try {
      const res = await fetch("/api/ideas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setContent("");
      await load();
    } catch (e) {
      setError(`Could not save idea (${e.message})`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="container">
      <h1>Idea Board</h1>
      <form onSubmit={submit} className="form">
        <input
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Share an idea..."
          maxLength={1000}
          aria-label="New idea"
        />
        <button disabled={busy || !content.trim()}>Add</button>
      </form>
      {error && <p className="error">{error}</p>}
      <ul className="ideas">
        {ideas.map((i) => (
          <li key={i.id}>
            <span>{i.content}</span>
            <time>{new Date(i.created_at).toLocaleString()}</time>
          </li>
        ))}
        {ideas.length === 0 && !error && <li className="empty">No ideas yet. Add the first one!</li>}
      </ul>
    </main>
  );
}
