/**
 * Community / logged-out optional endpoints must return null, not throw.
 */
import {
  FetchError,
  isOptionalEndpointMiss,
  optionalJsonFetcher,
} from "@/lib/fetcher";

function makeFetchError(status: number): FetchError {
  const err = Object.create(FetchError.prototype) as FetchError;
  err.status = status;
  err.message = "fail";
  return err;
}

describe("isOptionalEndpointMiss", () => {
  test.each([401, 403, 404])("treats %s as a miss", (status) => {
    expect(isOptionalEndpointMiss(makeFetchError(status))).toBe(true);
  });

  test("does not treat 500 as a miss", () => {
    expect(isOptionalEndpointMiss(makeFetchError(500))).toBe(false);
  });
});

describe("optionalJsonFetcher", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  test.each([401, 403, 404])("returns null on %s", async (status) => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      status,
      ok: false,
      json: async () => ({ detail: "missing" }),
    } as Response);
    await expect(optionalJsonFetcher("/api/enterprise-settings")).resolves.toBe(
      null
    );
  });

  test("returns JSON when ok", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => ({ application_name: "Desk" }),
    } as Response);
    await expect(optionalJsonFetcher("/api/enterprise-settings")).resolves.toEqual(
      { application_name: "Desk" }
    );
  });

  test("throws FetchError on 500", async () => {
    jest.spyOn(global, "fetch").mockResolvedValue({
      status: 500,
      ok: false,
      json: async () => ({ detail: "boom" }),
    } as Response);
    await expect(optionalJsonFetcher("/api/enterprise-settings")).rejects.toMatchObject(
      { status: 500 }
    );
  });
});
