import appConfig from "../../../../../config/app.json";

import { browserMutationOriginIsAllowed } from "@/lib/mutation-origin";

type FrontendAppConfig = {
  serverApiBaseUrl?: string;
};

type RouteContext = {
  params: Promise<{ path?: string[] }>;
};

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function PATCH(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function DELETE(request: Request, context: RouteContext) {
  return proxyBackendRequest(request, context);
}

export async function OPTIONS() {
  return new Response(null, { status: 204 });
}

async function proxyBackendRequest(request: Request, context: RouteContext) {
  const requestId = proxyRequestId(request);
  try {
    return await proxyBackendRequestWithId(request, context, requestId);
  } catch (error) {
    console.error(
      "backend proxy failure request_id=%s method=%s path=%s error=%s",
      requestId,
      request.method,
      new URL(request.url).pathname,
      errorMessage(error),
    );
    return Response.json(
      { detail: `Backend proxy failed. Request ID: ${requestId}` },
      { status: 500, headers: { "X-Request-ID": requestId } },
    );
  }
}

async function proxyBackendRequestWithId(
  request: Request,
  context: RouteContext,
  requestId: string,
) {
  const params = await context.params;
  const path = (params.path ?? []).map(encodeURIComponent).join("/");
  const requestUrl = new URL(request.url);
  if (!browserMutationOriginIsAllowed(request)) {
    return Response.json(
      { detail: "Cross-origin mutation request rejected." },
      { status: 403, headers: { "X-Request-ID": requestId } },
    );
  }

  const headers = filteredRequestHeaders(request.headers);
  headers.set("X-Request-ID", requestId);
  const authHeader = backendAuthHeader();
  if (authHeader) {
    headers.set("Authorization", authHeader);
  }
  const requestBody = requestHasBody(request.method) ? await request.arrayBuffer() : undefined;

  const errors: string[] = [];
  for (const upstreamUrl of backendUpstreamUrls(path, requestUrl.search)) {
    try {
      const upstreamHeaders = new Headers(headers);
      upstreamHeaders.set("X-Forwarded-Host", upstreamUrl.host);
      upstreamHeaders.set("X-Forwarded-Proto", upstreamUrl.protocol.replace(/:$/, ""));
      if (requestHasBody(request.method)) {
        upstreamHeaders.set("Origin", upstreamUrl.origin);
      }
      const upstreamResponse = await fetch(upstreamUrl, {
        method: request.method,
        headers: upstreamHeaders,
        body: requestBody,
        cache: "no-store",
        redirect: "manual",
      });

      if (upstreamResponse.status >= 500) {
        console.error(
          "backend proxy upstream failure request_id=%s method=%s path=%s status=%s",
          requestId,
          request.method,
          requestUrl.pathname,
          upstreamResponse.status,
        );
      }

      const responseHeaders = filteredResponseHeaders(upstreamResponse.headers);
      responseHeaders.set("X-Request-ID", requestId);

      return new Response(upstreamResponse.body, {
        status: upstreamResponse.status,
        statusText: upstreamResponse.statusText,
        headers: responseHeaders,
      });
    } catch (error) {
      console.error(
        "backend proxy upstream unavailable request_id=%s method=%s path=%s origin=%s error=%s",
        requestId,
        request.method,
        requestUrl.pathname,
        upstreamUrl.origin,
        errorMessage(error),
      );
      errors.push(`${upstreamUrl.origin}: ${errorMessage(error)}`);
    }
  }

  return Response.json(
    {
      detail: `Could not reach backend API. Tried ${errors.join("; ")}`,
    },
    { status: 502, headers: { "X-Request-ID": requestId } },
  );
}

function proxyRequestId(request: Request) {
  const supplied = request.headers.get("x-request-id")?.trim();
  if (supplied && /^[A-Za-z0-9._:-]{1,128}$/.test(supplied)) {
    return supplied;
  }
  return crypto.randomUUID();
}

function backendUpstreamUrls(path: string, search: string) {
  const urls: URL[] = [];
  for (const baseUrl of serverApiBaseUrls()) {
    const upstreamUrl = new URL(path, `${baseUrl}/`);
    upstreamUrl.search = search;
    urls.push(upstreamUrl);
  }
  return urls;
}

function serverApiBaseUrls() {
  const config = appConfig as FrontendAppConfig;
  const configuredUrl = process.env.SERVER_API_BASE_URL;
  const values =
    process.env.NODE_ENV === "production" && configuredUrl
      ? [configuredUrl]
      : [configuredUrl, config.serverApiBaseUrl, "http://127.0.0.1:8000"];
  return uniqueStrings(values.filter((value): value is string => Boolean(value)));
}

function uniqueStrings(values: string[]) {
  return [...new Set(values.map((value) => value.replace(/\/+$/, "")))];
}

function backendAuthHeader() {
  const username = process.env.DASHBOARD_AUTH_USERNAME ?? "admin";
  const password = process.env.DASHBOARD_AUTH_PASSWORD ?? "change-me";
  if (!username || !password) {
    return null;
  }
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
}

function filteredRequestHeaders(source: Headers) {
  const headers = new Headers();
  source.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase()) && key.toLowerCase() !== "authorization") {
      headers.set(key, value);
    }
  });
  return headers;
}

function filteredResponseHeaders(source: Headers) {
  const headers = new Headers();
  source.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  return headers;
}

function requestHasBody(method: string) {
  return method !== "GET" && method !== "HEAD";
}

function errorMessage(error: unknown) {
  if (!(error instanceof Error)) {
    return "Upstream request failed.";
  }
  const cause = error.cause;
  if (cause instanceof Error && cause.message) {
    return `${error.message}: ${cause.message}`;
  }
  return error.message;
}
