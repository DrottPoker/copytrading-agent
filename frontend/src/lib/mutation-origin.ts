const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const HTTP_PROTOCOLS = new Set(["http:", "https:"]);

export function browserMutationOriginIsAllowed(request: Request) {
  if (!UNSAFE_METHODS.has(request.method.toUpperCase())) {
    return true;
  }

  const originHeader = request.headers.get("origin");
  if (!originHeader) {
    return true;
  }
  const browserOrigin = canonicalOrigin(originHeader);
  if (!browserOrigin) {
    return false;
  }

  return requestOrigins(request).has(browserOrigin);
}

export function requestOrigins(request: Request) {
  const origins = new Set<string>();
  const requestUrl = new URL(request.url);
  addCanonicalOrigin(origins, requestUrl.origin);

  const forwardedHost = firstHeaderValue(request.headers.get("x-forwarded-host"));
  const forwardedProtocol = firstHeaderValue(request.headers.get("x-forwarded-proto"));
  const publicHost = forwardedHost || firstHeaderValue(request.headers.get("host"));
  const publicProtocol = forwardedProtocol || requestUrl.protocol;
  if (publicHost && publicProtocol) {
    addCanonicalOrigin(origins, `${normalizeProtocol(publicProtocol)}//${publicHost}`);
  }

  return origins;
}

function addCanonicalOrigin(origins: Set<string>, value: string) {
  const origin = canonicalOrigin(value);
  if (origin) {
    origins.add(origin);
  }
}

function canonicalOrigin(value: string) {
  try {
    const url = new URL(value.trim());
    if (
      !HTTP_PROTOCOLS.has(url.protocol) ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    ) {
      return null;
    }
    return url.origin.toLowerCase();
  } catch {
    return null;
  }
}

function firstHeaderValue(value: string | null) {
  return value?.split(",", 1)[0]?.trim() || null;
}

function normalizeProtocol(value: string) {
  return value.endsWith(":") ? value : `${value}:`;
}
