import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { PageHeader, Unavailable } from "../components/ui";

export function UnavailablePage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <PageHeader title={title} />
      <Unavailable>{children}</Unavailable>
    </>
  );
}

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" />
      <p>
        This address does not match a page. <Link to="/">Go to the overview</Link>.
      </p>
    </>
  );
}
