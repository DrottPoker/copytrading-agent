import { NextRequest, NextResponse } from "next/server";

type BasicCredentials = {
  username: string;
  password: string;
};

export function middleware(request: NextRequest) {
  if (!dashboardAuthEnabled() || request.method === "OPTIONS") {
    return NextResponse.next();
  }

  const credentials = parseBasicAuth(request.headers.get("authorization"));
  if (credentialsAreValid(credentials)) {
    return NextResponse.next();
  }

  return new NextResponse("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Hyperliquid Copy Agent"',
    },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|robots.txt).*)"],
};

function dashboardAuthEnabled() {
  return process.env.DASHBOARD_AUTH_ENABLED !== "false";
}

function parseBasicAuth(authorization: string | null): BasicCredentials | null {
  if (!authorization) {
    return null;
  }

  const [scheme, encoded] = authorization.split(" ", 2);
  if (scheme?.toLowerCase() !== "basic" || !encoded) {
    return null;
  }

  try {
    const decoded = atob(encoded);
    const separatorIndex = decoded.indexOf(":");
    if (separatorIndex < 0) {
      return null;
    }
    return {
      username: decoded.slice(0, separatorIndex),
      password: decoded.slice(separatorIndex + 1),
    };
  } catch {
    return null;
  }
}

function credentialsAreValid(credentials: BasicCredentials | null) {
  if (credentials === null) {
    return false;
  }

  return (
    constantTimeEqual(credentials.username, expectedUsername()) &&
    constantTimeEqual(credentials.password, expectedPassword())
  );
}

function expectedUsername() {
  return process.env.DASHBOARD_AUTH_USERNAME ?? "admin";
}

function expectedPassword() {
  return process.env.DASHBOARD_AUTH_PASSWORD ?? "change-me";
}

function constantTimeEqual(left: string, right: string) {
  const maxLength = Math.max(left.length, right.length);
  let mismatch = left.length === right.length ? 0 : 1;

  for (let index = 0; index < maxLength; index += 1) {
    mismatch |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }

  return mismatch === 0;
}
