import { useEffect, useState } from "react";
import { getDashboardFrontDeskAgents, getFDUsers } from "../data/api.js";
import { canApproveDependencies, isRestrictedAgent } from "../data/accessControl.js";
import { usePersistentTableState } from "../data/persistentTableState.js";
import Leads from "./Leads";
import Tasks from "./Tasks";
import SMLeads from "./SMLeads";
import MissedCalls from "./MissedCalls";
import AssignedFollowUps from "./AssignedFollowUps";
import { ASSIGNING_TABS, DEFAULT_ASSIGNING_TAB, assigningPageTitle, badgeText, validAssigningTab } from "../data/assigningTabs.js";
import { useAssignmentCounts } from "../data/useAssignmentCounts.js";

// "Assigning the Calls": the pages where calls are assigned, as tabs of one page. Each tab is the existing page, unchanged;
// only where it lives changed. A tab is mounted the first time it is opened and then kept (hidden when another tab is
// shown), so switching tabs never reloads the page and each tab keeps its own filters and edits.
const TAB_PAGES = {
  leads: Leads,
  general: Tasks,
  "sm-leads": SMLeads,
  "follow-ups": AssignedFollowUps,
  "missed-calls": MissedCalls,
};

export default function AssigningCalls({ isActive = true, requestedTab, ...pageProps }) {
  const { user } = pageProps;
  // ONE Agent filter for every tab, for everyone (applied as soon as an agent is picked): every signed-in user sees every record on every tab (agents view only), and can
  // narrow all of them to one agent. Every tab then shows that agent's records and keeps showing them when you switch tabs. Each tab
  // keeps its own dates and other filters. Managers read the agent list from the user directory; agents from the minimal agent directory.
  const canChooseAgent = true;
  const manager = canApproveDependencies(user);
  const agentView = isRestrictedAgent(user);
  // An agent sees how many records are still waiting on them in each tab (their own, from the server).
  const pendingCounts = useAssignmentCounts(agentView);
  const [agents, setAgents] = useState([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [agentId, setAgentId] = usePersistentTableState("AssigningCalls.agentId", "");
  useEffect(() => {
    if (!canChooseAgent) return;
    (manager ? getFDUsers() : getDashboardFrontDeskAgents())
      .then((data) => setAgents((data || []).filter((item) => String(item.role || "agent").toLowerCase() === "agent")))
      .catch(() => setAgents([]))
      .finally(() => setAgentsLoaded(true));
  }, [canChooseAgent, manager]);
  // A remembered selection that is no longer in the list (deactivated agent) would hold every tab blank: drop it.
  useEffect(() => {
    if (agentsLoaded && agentId && !agents.some((item) => item.id === agentId)) setAgentId("");
  }, [agentsLoaded, agents, agentId]);
  const sharedAgent = canChooseAgent && agentId ? { id: agentId, name: agents.find((item) => item.id === agentId)?.name || "" } : null;
  // The applied agent must be known by name before the tabs filter by it (Leads and Follow Ups filter by name).
  const sharedAgentReady = !agentId || Boolean(sharedAgent?.name);
  const [tab, setTab] = useState(DEFAULT_ASSIGNING_TAB);
  const [opened, setOpened] = useState(() => new Set([DEFAULT_ASSIGNING_TAB]));

  const show = (key) => {
    const next = validAssigningTab(key);
    setTab(next);
    setOpened((current) => (current.has(next) ? current : new Set([...current, next])));
  };

  // A notification (or an old bookmark) asks for a tab: { tab, nonce } so asking for the same tab twice still works.
  useEffect(() => {
    if (requestedTab?.tab) show(requestedTab.tab);
  }, [requestedTab?.tab, requestedTab?.nonce]);

  return (
    <section className="assigning-calls-page">
      <div className="leads-heading assigning-calls-heading">
        <h1>{assigningPageTitle(agentView)}</h1>
      </div>
      {canChooseAgent && (
        <div className="assigning-agent-bar" role="group" aria-label="Agent filter for every tab">
          <label className="calls-filter-group calls-inline-filter">
            <span className="calls-toolbar-label">Agent</span>
            <select className="calls-filter-select" value={agentId} onChange={(event) => setAgentId(event.target.value)}>
              <option value="">All agents</option>
              {agents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          {agentId && <button type="button" className="table-action" onClick={() => setAgentId("")}>Show all agents</button>}
          <span className={`assigning-agent-note ${agentId ? "on" : ""}`} role="status">
            {agentId ? `Showing ${sharedAgent?.name || "the selected agent"}'s records in every tab` : "Showing all agents in every tab"}
          </span>
        </div>
      )}
      <div className="calls-stage-view-tabs assigning-calls-tabs" role="tablist" aria-label={`${assigningPageTitle(agentView)} sections`}>
        {ASSIGNING_TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`table-action assigning-calls-tab ${tab === key ? "active" : ""}`}
            onClick={() => show(key)}
          >
            {label}
            {agentView && badgeText(pendingCounts[key]) && (
              <span className="assigning-tab-badge" aria-label={`${pendingCounts[key]} pending`}>{badgeText(pendingCounts[key])}</span>
            )}
          </button>
        ))}
      </div>
      {ASSIGNING_TABS.map(({ key }) => {
        const Page = TAB_PAGES[key];
        return opened.has(key) ? (
          <div key={key} role="tabpanel" hidden={tab !== key} className="assigning-calls-panel">
            {sharedAgentReady && <Page {...pageProps} sharedAgent={sharedAgent} isActive={isActive && tab === key} />}
          </div>
        ) : null;
      })}
    </section>
  );
}
