import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { api, call } from "./api/client";
import { useAuth } from "./auth/auth";
import { Loading } from "./components/ui";
import { BrandProvider, useBrand } from "./lib/brand";
import { usePublishMode } from "./lib/queries";
import { ApprovalsPage } from "./pages/Approvals";
import { AnalyticsPage } from "./pages/Analytics";
import { AutomationsPage } from "./pages/Automations";
import { BrandPage } from "./pages/Brand";
import { CalendarPage } from "./pages/Calendar";
import { CampaignDetailPage } from "./pages/CampaignDetail";
import { CampaignNewPage } from "./pages/CampaignNew";
import { CampaignsPage } from "./pages/Campaigns";
import { ContentDetailPage } from "./pages/ContentDetail";
import { ContentPage } from "./pages/Content";
import { DashboardPage } from "./pages/Dashboard";
import { ExperimentDetailPage, ExperimentsPage } from "./pages/Experiments";
import { LoginPage } from "./pages/Login";
import { NotFoundPage, UnavailablePage } from "./pages/Misc";
import { PlatformsPage } from "./pages/Platforms";
import { PublicationsPage } from "./pages/Publications";
import { SourcesPage } from "./pages/Sources";

const NAV: { group: string; links: { to: string; label: string }[] }[] = [
  {
    group: "Work",
    links: [
      { to: "/", label: "Overview" },
      { to: "/approvals", label: "Approvals" },
      { to: "/content", label: "Content" },
      { to: "/calendar", label: "Calendar" },
      { to: "/publications", label: "Publishing" },
      { to: "/campaigns", label: "Campaigns" },
    ],
  },
  {
    group: "Insights",
    links: [
      { to: "/analytics", label: "Analytics" },
      { to: "/experiments", label: "Experiments" },
    ],
  },
  {
    group: "Setup",
    links: [
      { to: "/sources", label: "Sources" },
      { to: "/brand", label: "Brand profile" },
      { to: "/platforms", label: "Platforms" },
    ],
  },
  {
    group: "System",
    links: [
      { to: "/models", label: "Models" },
      { to: "/automations", label: "Automations" },
      { to: "/settings", label: "Settings" },
    ],
  },
];

function ApprovalCount() {
  const { brandId } = useBrand();
  const q = useQuery({
    queryKey: ["approvals", brandId],
    queryFn: () => call(api.GET("/api/v1/approvals", { params: { query: brandId ? { brand_id: brandId } : {} } })),
    enabled: Boolean(brandId),
  });
  const n = q.data?.items.length ?? 0;
  return n ? (
    <span className="count" aria-label={`${n} waiting`}>
      {n}
    </span>
  ) : null;
}

function PublishModePill() {
  const mode = usePublishMode();
  if (mode === undefined) return null;
  return mode === "MOCK" ? (
    <span className="mode-pill" title="Only the mock publisher is enabled: nothing reaches a real platform.">
      Mock publishing
    </span>
  ) : (
    <span className="mode-pill mode-off" title="No publisher is available on this server; scheduled jobs fail.">
      Publishing off
    </span>
  );
}

function Shell() {
  const { me, signOut } = useAuth();
  const { brands, brandId, setBrandId } = useBrand();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);

  const firstRender = useRef(true);
  useEffect(() => {
    setMenuOpen(false);
    // After in-app navigation, move focus to the new page (screen readers announce it); on
    // first load leave focus alone so the skip link is the first Tab stop.
    if (firstRender.current) firstRender.current = false;
    else mainRef.current?.focus();
  }, [location.pathname]);

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <button
          type="button"
          className="btn btn-ghost menu-button"
          aria-expanded={menuOpen}
          aria-controls="primary-nav"
          onClick={() => setMenuOpen((v) => !v)}
        >
          Menu
        </button>
        <span className="logo">OmniGrowth</span>
        <label className="brand-select">
          <span className="sr-only">Brand</span>
          <select value={brandId ?? ""} onChange={(e) => setBrandId(e.target.value)} disabled={!brands.length}>
            {brands.length ? null : <option value="">No brands yet</option>}
            {brands.map((b) => (
              <option key={b.id} value={b.id}>
                {b.display_name}
              </option>
            ))}
          </select>
        </label>
        <PublishModePill />
        <span className="user small">
          {me?.email} · {me?.role.toLowerCase()}
        </span>
        <button type="button" className="btn btn-ghost" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>
      <nav id="primary-nav" className={`sidebar${menuOpen ? " open" : ""}`} aria-label="Primary">
        {NAV.map((g) => (
          <div key={g.group} className="nav-group">
            <div className="nav-group-title">{g.group}</div>
            <ul>
              {g.links.map((l) => (
                <li key={l.to}>
                  <NavLink to={l.to} end={l.to === "/"}>
                    {l.label}
                    {l.to === "/approvals" ? <ApprovalCount /> : null}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <main id="main" ref={mainRef} tabIndex={-1} className="main">
        <Outlet />
      </main>
    </div>
  );
}

function RequireAuth() {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="Restoring your session…" />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return (
    <BrandProvider>
      <Shell />
    </BrandProvider>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<DashboardPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="content" element={<ContentPage />} />
        <Route path="content/:assetId" element={<ContentDetailPage />} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="publications" element={<PublicationsPage />} />
        <Route path="campaigns" element={<CampaignsPage />} />
        <Route path="campaigns/new" element={<CampaignNewPage />} />
        <Route path="campaigns/:campaignId" element={<CampaignDetailPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="experiments/:experimentId" element={<ExperimentDetailPage />} />
        <Route path="sources" element={<SourcesPage />} />
        <Route path="brand" element={<BrandPage />} />
        <Route path="platforms" element={<PlatformsPage />} />
        <Route
          path="models"
          element={
            <UnavailablePage title="Models">
              Model routing is configured in <code>config/models.yaml</code> on the server: planning and writing use one local model in LM Studio, with no cloud fallback. There is no API to view or change it from here yet.
            </UnavailablePage>
          }
        />
        <Route path="automations" element={<AutomationsPage />} />
        <Route
          path="settings"
          element={
            <UnavailablePage title="Settings">
              User management and system settings have no API yet. Owners create users with the command line (<code>poe create-owner</code>).
            </UnavailablePage>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
