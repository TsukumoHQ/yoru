import { QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { createBrowserRouter, RouterProvider } from "react-router-dom"
import { AuthProvider } from "./auth/AuthProvider"
import { RequireAuth } from "./auth/RequireAuth"
import { AppShell } from "./components/AppShell"
import { Toaster } from "./components/Toaster"
import { AuthCallback } from "./pages/AuthCallback"
import { BillingCancelPage } from "./pages/BillingCancelPage"
import { BillingPage } from "./pages/BillingPage"
import { BillingSuccessPage } from "./pages/BillingSuccessPage"
import { DocsInstallPage } from "./pages/DocsInstallPage"
import { ErrorPage } from "./pages/ErrorPage"
import { GithubCallbackPage } from "./pages/GithubCallbackPage"
import { NotFound } from "./pages/NotFound"
import { OrganizationsPage } from "./pages/OrganizationsPage"
import { PairCliPage } from "./pages/PairCliPage"
import { ProfilePage } from "./pages/ProfilePage"
import { RoutingPage } from "./pages/RoutingPage"
import { TokensPage } from "./pages/TokensPage"
import { WorkspacesPage } from "./pages/WorkspacesPage"
import { SessionDetailPage } from "./pages/SessionDetailPage"
import { SessionsListPage } from "./pages/SessionsListPage"
import { SetupPage } from "./pages/SetupPage"
import { SignInPage } from "./pages/SignInPage"
import { SignUpPage } from "./pages/SignUpPage"
import { SmokePage } from "./pages/SmokePage"
import { WelcomePage } from "./pages/WelcomePage"
import { queryClient } from "./lib/queryClient"

// errorElement on every route branch: component render or loader throws
// land on the Swiss-styled ErrorPage instead of React Router's default
// "Hey developer" placeholder.
const router = createBrowserRouter([
  { path: "/setup", element: <SetupPage />, errorElement: <ErrorPage /> },
  { path: "/signin", element: <SignInPage />, errorElement: <ErrorPage /> },
  { path: "/signup", element: <SignUpPage />, errorElement: <ErrorPage /> },
  { path: "/auth/callback", element: <AuthCallback />, errorElement: <ErrorPage /> },
  { path: "/docs/install", element: <DocsInstallPage />, errorElement: <ErrorPage /> },
  { path: "/__smoke", element: <SmokePage />, errorElement: <ErrorPage /> },
  {
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    errorElement: <ErrorPage />,
    children: [
      { path: "/", element: <SessionsListPage />, errorElement: <ErrorPage /> },
      { path: "/welcome", element: <WelcomePage />, errorElement: <ErrorPage /> },
      { path: "/s/:id", element: <SessionDetailPage />, errorElement: <ErrorPage /> },
      // TODO: nest under a /settings shell once US-21 tabs land.
      { path: "/settings/billing", element: <BillingPage />, errorElement: <ErrorPage /> },
      { path: "/settings/organizations", element: <OrganizationsPage />, errorElement: <ErrorPage /> },
      { path: "/settings/profile", element: <ProfilePage />, errorElement: <ErrorPage /> },
      { path: "/settings/tokens", element: <TokensPage />, errorElement: <ErrorPage /> },
      { path: "/settings/routing", element: <RoutingPage />, errorElement: <ErrorPage /> },
      { path: "/settings/workspaces", element: <WorkspacesPage />, errorElement: <ErrorPage /> },
      { path: "/integrations/github/callback", element: <GithubCallbackPage />, errorElement: <ErrorPage /> },
      { path: "/cli/pair", element: <PairCliPage />, errorElement: <ErrorPage /> },
      { path: "/billing/success", element: <BillingSuccessPage />, errorElement: <ErrorPage /> },
      { path: "/billing/cancel", element: <BillingCancelPage />, errorElement: <ErrorPage /> },
    ],
  },
  { path: "*", element: <NotFound /> },
])

export function Root() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
        <Toaster />
        {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
      </AuthProvider>
    </QueryClientProvider>
  )
}
