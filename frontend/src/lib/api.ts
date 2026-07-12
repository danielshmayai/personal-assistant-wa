// Same-origin API client. The session rides an HttpOnly cookie; the
// X-Requested-With header is the CSRF guard required on mutating calls.

class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "pa-app",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401) {
    window.location.href = "/login";
    throw new ApiError(401, "not_authenticated");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail ?? detail;
    } catch { /* not json */ }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  // No Content-Type header — the browser sets the multipart boundary itself.
  const resp = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Requested-With": "pa-app" },
    body: form,
  });
  if (resp.status === 401) {
    window.location.href = "/login";
    throw new ApiError(401, "not_authenticated");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail ?? detail;
    } catch { /* not json */ }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
  upload,
};

export { ApiError };
