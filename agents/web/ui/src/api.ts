import type { ApiError } from "./types";

export async function apiJson<T>(
  method: string,
  path: string,
  body: unknown,
  token: string
): Promise<{ ok: true; result: T } | { ok: false; error: ApiError }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json; charset=utf-8",
  };
  if (token.trim()) headers.Authorization = `Bearer ${token.trim()}`;
  try {
    const res = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    const json = await res.json();
    return json;
  } catch (e) {
    return { ok: false, error: { code: "network_error", message: String(e) } };
  }
}

