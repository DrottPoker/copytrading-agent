import { getPublicApiBaseUrl } from "./config";

type QueryValue = string | number | boolean | null | undefined;

/** Builds a browser URL for the backend proxy. */
export function apiUrl(path: string, params: Record<string, QueryValue> = {}) {
  const base = getPublicApiBaseUrl().replace(/\/$/, "");
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return `${base}${path.startsWith("/") ? path : `/${path}`}${query ? `?${query}` : ""}`;
}

/**
  Turns a failed response into a readable message. FastAPI details are used when present and the
  proxy request id is appended so a failure can be found in the logs.
*/
export async function responseError(response: Response, fallback: string) {
  const requestId = response.headers.get("x-request-id")?.trim();
  const requestSuffix = requestId ? ` Request ID: ${requestId}.` : "";
  const detail = await responseDetail(response);
  if (detail) {
    return `${detail}${requestSuffix}`;
  }
  return `${fallback} with HTTP ${response.status}.${requestSuffix}`;
}

async function responseDetail(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    return errorDetail(payload);
  } catch {
    return null;
  }
}

export function errorDetail(value: unknown): string | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const detail = (value as { detail?: unknown }).detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return null;
      })
      .filter(Boolean);
    return messages.length > 0 ? messages.join(", ") : null;
  }
  return null;
}

/** Sends a request through the backend proxy and throws a readable error on failure. */
export async function apiRequest<T>(
  path: string,
  {
    body,
    fallback = "Request failed",
    method = "GET",
    params,
    signal,
  }: {
    body?: unknown;
    fallback?: string;
    method?: "GET" | "POST" | "PATCH" | "DELETE";
    params?: Record<string, QueryValue>;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path, params), {
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      method,
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new Error("Could not reach the dashboard API.");
  }
  if (!response.ok) {
    throw new Error(await responseError(response, fallback));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}
