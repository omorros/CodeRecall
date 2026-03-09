const API_BASE = "http://localhost:8000";

export interface Repo {
  id: string;
  github_url: string;
  name: string;
  status: "pending" | "processing" | "ready" | "failed";
  progress: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Source {
  file_path: string;
  chunk_id: string;
  similarity: number;
}

export interface ChatResponse {
  answer: string;
  sources: Source[];
  conversation_id: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[] | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  repo_id: string;
  created_at: string;
}

export async function createRepo(githubUrl: string): Promise<Repo> {
  const res = await fetch(`${API_BASE}/repos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ github_url: githubUrl }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || "Failed to create repo");
  return res.json();
}

export async function getRepos(): Promise<Repo[]> {
  const res = await fetch(`${API_BASE}/repos`);
  if (!res.ok) throw new Error("Failed to fetch repos");
  return res.json();
}

export async function getRepo(id: string): Promise<Repo> {
  const res = await fetch(`${API_BASE}/repos/${id}`);
  if (!res.ok) throw new Error("Failed to fetch repo");
  return res.json();
}

export async function deleteRepo(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/repos/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete repo");
}

export async function chat(
  repoId: string,
  message: string,
  conversationId?: string
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      conversation_id: conversationId || null,
    }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || "Chat failed");
  return res.json();
}
