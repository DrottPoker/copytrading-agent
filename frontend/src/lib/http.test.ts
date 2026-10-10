import { describe, expect, it } from "vitest";

import { apiUrl, errorDetail, responseError } from "./http";

describe("responseError", () => {
  it("shows the proxy request id for a non-JSON server failure", async () => {
    const response = new Response("Internal Server Error", {
      headers: { "X-Request-ID": "close-request-123" },
      status: 500,
    });

    await expect(responseError(response, "Manual close failed")).resolves.toBe(
      "Manual close failed with HTTP 500. Request ID: close-request-123.",
    );
  });

  it("prefers the FastAPI detail", async () => {
    const response = Response.json({ detail: "Account is not flat." }, { status: 409 });

    await expect(responseError(response, "Disable failed")).resolves.toBe("Account is not flat.");
  });
});

describe("errorDetail", () => {
  it("joins validation messages", () => {
    expect(errorDetail({ detail: [{ msg: "field required" }, { msg: "too large" }] })).toBe("field required, too large");
    expect(errorDetail({ detail: [] })).toBeNull();
  });
});

describe("apiUrl", () => {
  it("adds only the query values that are set", () => {
    expect(apiUrl("/paper-trading", { closed_trade_limit: 250, include_market_prices: false, missing: null })).toBe(
      "/api/backend/paper-trading?closed_trade_limit=250&include_market_prices=false",
    );
  });
});
