/**
 * @jest-environment jsdom
 *
 * Login must keep rendering when optional EE settings 404 (Community).
 */
import { render, screen } from "@testing-library/react";
import { SettingsProvider } from "./SettingsProvider";
import { useSettings } from "@/lib/settings/hooks";
import { FetchError } from "@/lib/fetcher";

jest.mock("@/lib/settings/hooks", () => ({ useSettings: jest.fn() }));
jest.mock("@/lib/constants", () => ({ NEXT_PUBLIC_CLOUD_ENABLED: false }));
jest.mock("@/components/errorPages/ErrorPage", () => ({
  __esModule: true,
  default: () => <div>We encountered an issue</div>,
}));
jest.mock("@/components/errorPages/CloudErrorPage", () => ({
  __esModule: true,
  default: () => <div>cloud error</div>,
}));

const mockUseSettings = useSettings as jest.MockedFunction<typeof useSettings>;

function makeFetchError(status: number): FetchError {
  const err = Object.create(FetchError.prototype) as FetchError;
  err.status = status;
  err.message = "fail";
  return err;
}

describe("SettingsProvider", () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  test.each([401, 403, 404])(
    "renders children when settings miss with HTTP %s",
    (status) => {
      mockUseSettings.mockReturnValue({ error: makeFetchError(status) } as ReturnType<
        typeof useSettings
      >);
      render(
        <SettingsProvider>
          <div>login form</div>
        </SettingsProvider>
      );
      expect(screen.getByText("login form")).toBeInTheDocument();
      expect(
        screen.queryByText("We encountered an issue")
      ).not.toBeInTheDocument();
    }
  );

  test("shows the error card on a real settings failure", () => {
    mockUseSettings.mockReturnValue({ error: makeFetchError(500) } as ReturnType<
      typeof useSettings
    >);
    render(
      <SettingsProvider>
        <div>login form</div>
      </SettingsProvider>
    );
    expect(screen.getByText("We encountered an issue")).toBeInTheDocument();
    expect(screen.queryByText("login form")).not.toBeInTheDocument();
  });
});
