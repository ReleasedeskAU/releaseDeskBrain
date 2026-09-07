"use client";

import { JSX } from "react";
import { useSettings } from "@/lib/settings/hooks";
import { NEXT_PUBLIC_CLOUD_ENABLED } from "@/lib/constants";
import CloudError from "@/components/errorPages/CloudErrorPage";
import ErrorPage from "@/components/errorPages/ErrorPage";
import { isOptionalEndpointMiss } from "@/lib/fetcher";

/**
 * Renders a fatal error page when core or enterprise settings cannot be
 * fetched. Logged-out 401/403 and Community 404 on optional EE routes are
 * expected and must not replace the login form with this error card.
 */
export function SettingsProvider({
  children,
}: {
  children: React.ReactNode | JSX.Element;
}) {
  const { error } = useSettings();

  if (error && !isOptionalEndpointMiss(error)) {
    return NEXT_PUBLIC_CLOUD_ENABLED ? <CloudError /> : <ErrorPage />;
  }

  return <>{children}</>;
}
