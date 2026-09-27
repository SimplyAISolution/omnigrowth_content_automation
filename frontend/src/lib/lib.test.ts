import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { addDays, daysBetween, zonedToIso } from "./dates";
import { explainError } from "./format";
import { useAction } from "./hooks";
import { ASSET_STATUS, JOB_STATUS, PUBLICATION_STATUS } from "./status";

describe("status vocabulary", () => {
  it("never calls a mock publication a real one", () => {
    for (const info of [ASSET_STATUS.PUBLISHED, JOB_STATUS.SUCCEEDED, PUBLICATION_STATUS.PUBLISHED]) {
      expect(info.label).toMatch(/simulated/i);
      expect(info.tone).toBe("simulated");
    }
    expect(ASSET_STATUS.AWAITING_APPROVAL.help).toMatch(/Nothing is scheduled or published until a person approves/);
  });
});

describe("dates", () => {
  it("converts a New York wall-clock time to UTC across DST", () => {
    expect(zonedToIso("2026-07-01", "09:00", "America/New_York")).toBe("2026-07-01T13:00:00.000Z");
    expect(zonedToIso("2026-12-01", "09:00", "America/New_York")).toBe("2026-12-01T14:00:00.000Z");
  });
  it("lists the days of a range with an exclusive end", () => {
    expect(daysBetween("2026-09-28T04:00:00Z", "2026-10-05T04:00:00Z", "America/New_York")).toEqual(
      ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"],
    );
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
  });
});

describe("error explanations", () => {
  it("turns API errors into actionable sentences", () => {
    expect(explainError(new ApiError(403, { code: "FORBIDDEN", message: "x" }))).toMatch(/role does not allow/);
    expect(explainError(new ApiError(0, undefined))).toMatch(/could not be reached|Request failed/);
    const validation = new ApiError(422, {
      code: "VALIDATION_FAILURE",
      message: "Request validation failed",
      details: { errors: [{ loc: ["body", "comment"], msg: "Value error, REJECT requires a comment" }] },
    });
    expect(validation.fieldErrors()).toEqual({ comment: "REJECT requires a comment" });
    expect(explainError(validation)).toBe("Check the form: REJECT requires a comment");
  });
});

describe("useAction", () => {
  it("reuses one Idempotency-Key when the same action is retried, and a new one for new input", async () => {
    const keys: string[] = [];
    let fail = true;
    const client = new QueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(
      () =>
        useAction(async (_input: { n: number }, key: string) => {
          keys.push(key);
          if (fail) throw new ApiError(0, undefined);
          return "ok";
        }),
      { wrapper },
    );
    act(() => result.current.submit({ n: 1 }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    fail = false;
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(keys[0]).toBe(keys[1]);
    act(() => result.current.submit({ n: 2 }));
    await waitFor(() => expect(keys).toHaveLength(3));
    expect(keys[2]).not.toBe(keys[0]);
  });
});
