"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { createRepo, getRepos, deleteRepo, type Repo } from "@/lib/api";
import Link from "next/link";

export default function Home() {
  const [url, setUrl] = useState("");
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchRepos = async () => {
    try {
      const data = await getRepos();
      setRepos(data);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    fetchRepos();
    const interval = setInterval(fetchRepos, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    setLoading(true);
    setError("");
    try {
      await createRepo(url.trim());
      setUrl("");
      await fetchRepos();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteRepo(id);
      await fetchRepos();
    } catch {
      /* ignore */
    }
  };

  const statusColor = (status: Repo["status"]) => {
    switch (status) {
      case "ready":
        return "text-emerald-400";
      case "processing":
      case "pending":
        return "text-amber-400";
      case "failed":
        return "text-red-400";
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center px-4 pt-24 pb-12">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-2xl"
      >
        <h1 className="text-3xl font-semibold tracking-tight mb-1">
          CodeRecall
        </h1>
        <p className="text-neutral-500 mb-8">
          Paste a GitHub repo URL and chat with its codebase.
        </p>

        <form onSubmit={handleSubmit} className="flex gap-3 mb-10">
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repo"
            className="flex-1 bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-2.5 text-sm placeholder:text-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
          />
          <button
            type="submit"
            disabled={loading || !url.trim()}
            className="bg-white text-black px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-neutral-200 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {loading ? "Adding..." : "Add"}
          </button>
        </form>

        {error && (
          <p className="text-red-400 text-sm mb-4">{error}</p>
        )}

        <div className="space-y-2">
          <AnimatePresence mode="popLayout">
            {repos.map((repo) => (
              <motion.div
                key={repo.id}
                layout
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95 }}
                transition={{ duration: 0.2 }}
                className="flex items-center justify-between bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3 group"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span className={`text-xs font-mono ${statusColor(repo.status)}`}>
                    {repo.status === "pending" || repo.status === "processing"
                      ? "●"
                      : repo.status === "ready"
                      ? "✓"
                      : "✕"}
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{repo.name}</p>
                    <p className={`text-xs ${statusColor(repo.status)}`}>
                      {repo.status}
                      {repo.error_message && ` — ${repo.error_message}`}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0 ml-4">
                  {repo.status === "ready" && (
                    <Link
                      href={`/repos/${repo.id}/chat`}
                      className="text-xs text-neutral-400 hover:text-white border border-neutral-700 hover:border-neutral-500 px-3 py-1.5 rounded-md transition-colors"
                    >
                      Chat
                    </Link>
                  )}
                  <button
                    onClick={() => handleDelete(repo.id)}
                    className="text-xs text-neutral-600 hover:text-red-400 px-2 py-1.5 rounded-md transition-colors opacity-0 group-hover:opacity-100"
                  >
                    Delete
                  </button>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>

          {repos.length === 0 && (
            <p className="text-neutral-600 text-sm text-center py-8">
              No repos yet. Add one above.
            </p>
          )}
        </div>
      </motion.div>
    </div>
  );
}
