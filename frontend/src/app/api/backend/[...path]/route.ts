import appConfig from "../../../../../config/app.json";

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
  const params = await context.params;
  const path = (params.path ?? []).map(encodeURIComponent).join("/");
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(path, `${getServerApiBaseUrl()}/`);
  upstreamUrl.search = requestUrl.search;

  const headers = filteredRequestHeaders(request.headers);
  const authHeader = backendAuthHeader();
  if (authHeader) {
    headers.set("Authorization", authHeader);
  }

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body: requestHasBody(request.method) ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch (error) {
    return Response.json(
      {
        detail: `Could not reach backend API at ${upstreamUrl.origin}. ${errorMessage(error)}`,
      },
      { status: 502 },
    );
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: filteredResponseHeaders(upstreamResponse.headers),
  });
}

function getServerApiBaseUrl() {
  const config = appConfig as FrontendAppConfig;
  return process.env.SERVER_API_BASE_URL ?? config.serverApiBaseUrl ?? "http://127.0.0.1:8000";
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
  return error instanceof Error ? error.message : "Upstream request failed.";
}
