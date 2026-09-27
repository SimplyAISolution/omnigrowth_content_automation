import { Link } from "react-router-dom";
import { EmptyState, ErrorPanel, Loading, PageHeader, RoleButton, StatusBadge } from "../components/ui";
import { formatDate } from "../lib/format";
import { useCampaigns } from "../lib/queries";
import { CAMPAIGN_STATUS, PLATFORM_LABEL, humanize } from "../lib/status";
import { useNavigate } from "react-router-dom";

export function CampaignsPage() {
  const q = useCampaigns();
  const navigate = useNavigate();
  return (
    <>
      <PageHeader
        title="Campaigns"
        description="A campaign turns a source text into research, a strategy, content ideas and a 7-day plan, then platform-native content."
        actions={
          <RoleButton role="EDITOR" className="btn btn-primary" onClick={() => navigate("/campaigns/new")}>
            New campaign
          </RoleButton>
        }
      />
      {q.isLoading ? (
        <Loading />
      ) : q.error ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title="No campaigns yet">Start with “New campaign” and paste an article, notes or a transcript.</EmptyState>
      ) : (
        <table className="table">
          <caption className="sr-only">Campaigns</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">State</th>
              <th scope="col">Goal</th>
              <th scope="col">Platforms</th>
              <th scope="col">Starts</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((c) => (
              <tr key={c.id}>
                <td>
                  <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
                </td>
                <td>
                  <StatusBadge info={CAMPAIGN_STATUS[c.status]} />
                </td>
                <td>{humanize(c.objective)}</td>
                <td>{[c.primary_platform, ...c.secondary_platforms].map((p) => PLATFORM_LABEL[p]).join(", ")}</td>
                <td>{formatDate(c.start_date)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
