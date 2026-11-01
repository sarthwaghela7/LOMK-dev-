import { useEffect, useRef, useState } from "react";
import Dashboard from "./pages/Dashboard";
import ProcessCalls from "./pages/Calls";
import CaseClosing from "./pages/CaseClosing";
import Dependencies from "./pages/Dependencies";
import Settings from "./pages/Settings";
import AssigningCalls from "./pages/AssigningCalls";
import CallUpdates from "./pages/CallUpdates";
import CallCountReconciliation from "./pages/CallCountReconciliation";
import UnansweredCalls from "./pages/UnansweredCalls";
import Todo from "./pages/Todo";
import VisaUpdates from "./pages/VisaUpdates";
import CaseTypesEQ from "./pages/CaseTypesEQ";
import FAQ from "./pages/FAQ";
import ReviewCall from "./pages/ViewCall";
import Login from "./pages/Login";
import {
  getMyAssignment,
  getMyTaskNotifications,
  getTaskNotificationSummary,
  markAllTaskNotificationsRead,
  markTaskNotificationRead,
} from "./data/api.js";
import { NOTIFICATION_FILTERS, emptyNotificationMessage, filterNotifications } from "./data/notificationFilters.js";
import {
  canAccessSettings,
  canApproveDependencies,
  isAdmin,
  isRestrictedAgent,
} from "./data/accessControl.js";
import { confirmLeaveIfUnsaved, pendingNavigationWork, submitPendingNavigationWork } from "./data/unsavedChanges.js";
import UnsubmittedChangesDialog from "./components/UnsubmittedChangesDialog.jsx";
import ClosureNotificationHost from "./components/ClosureNotificationHost.jsx";
import { requestCallFocus } from "./data/callFocus.js";
import { ASSIGNING_PAGE, assigningPageTitle, legacyPageTab, tabForNotification } from "./data/assigningTabs.js";
import "./App.css";
import "./styles/flintdesk-theme.css";

const pages = {
  dashboard: Dashboard,
  "process-calls": ProcessCalls,
  "assigning-calls": AssigningCalls,
  "unanswered-calls": UnansweredCalls,
  "call-updates": CallUpdates,
  todo: Todo,
  "case-types-eq": CaseTypesEQ,
  "visa-updates": VisaUpdates,
  faq: FAQ,
  "case-closing": CaseClosing,
  "call-count-reconciliation": CallCountReconciliation,
  dependencies: Dependencies,
  settings: Settings,
};

const FD_TOKEN_KEY = "flintdesk_token";
const FD_USER_KEY = "flintdesk_user";
const FD_PAGE_KEY = "flintdesk_active_page";
const NOTIFICATION_POLL_INTERVAL_MS = 60_000;

function clearAuthSession() {
  localStorage.removeItem(FD_TOKEN_KEY);
  localStorage.removeItem(FD_USER_KEY);
  localStorage.removeItem(FD_PAGE_KEY);
}

function isLikelyJwt(token) {
  if (!token || typeof token !== "string") return false;
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  try {
    const encodedPayload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(
      atob(
        encodedPayload.padEnd(
          encodedPayload.length + ((4 - (encodedPayload.length % 4)) % 4),
          "=",
        ),
      ),
    );
    const isExpired =
      typeof payload?.exp === "number" && payload.exp * 1000 <= Date.now();
    return (
      !isExpired &&
      payload &&
      typeof payload === "object" &&
      (payload.fd_user_id || payload.email || payload.role)
    );
  } catch {
    return false;
  }
}

const restoredUser = () => {
  const token = localStorage.getItem(FD_TOKEN_KEY);
  if (!token || !isLikelyJwt(token)) {
    clearAuthSession();
    return null;
  }
  try {
    const savedUser = JSON.parse(localStorage.getItem(FD_USER_KEY) || "null");
    return savedUser && savedUser.id ? savedUser : null;
  } catch {
    clearAuthSession();
    return null;
  }
};

export default function App() {
  // Leads, SM Leads, Missed Calls and Assign Calls and Task are now tabs of "Assigning the Calls"; a saved old page name opens it.
  const savedPage = () => {
    const saved = localStorage.getItem(FD_PAGE_KEY) || "dashboard";
    return legacyPageTab(saved) ? ASSIGNING_PAGE : saved;
  };
  const [activePage, setActivePage] = useState(savedPage);
  const [visitedPages, setVisitedPages] = useState(() => new Set([savedPage()]));
  // { tab, nonce }: which tab of Assigning the Calls a notification or link asks for.
  const [assigningRequest, setAssigningRequest] = useState(null);
  const [reviewCall, setReviewCall] = useState(null);
  const [user, setUser] = useState(restoredUser);
  const [assignment, setAssignment] = useState(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [notificationFilter, setNotificationFilter] = useState("all");
  const [filteredNotifications, setFilteredNotifications] = useState([]);
  const [notificationListState, setNotificationListState] = useState({ loading: false, error: "" });
  const [markingAllRead, setMarkingAllRead] = useState(false);
  const notificationViewRef = useRef({ open: false, filter: "all" });
  useEffect(() => { notificationViewRef.current = { open: notificationsOpen, filter: notificationFilter }; }, [notificationsOpen, notificationFilter]);
  const [notificationToasts, setNotificationToasts] = useState([]);
  const seenNotificationIds = useRef(new Set());
  const safeActivePage =
    (activePage === "settings" && !canAccessSettings(user)) ||
    (activePage === "call-count-reconciliation" && !isAdmin(user))
      ? "dashboard"
      : activePage;
  // Existing pages use this flag for their broader reviewer controls. SMEs and
  // Team Leads intentionally share those controls; Settings stays admin-only.
  const isTeamLead = canApproveDependencies(user);

  // Leaving the Calls page while Case Status changes still need Submit: one confirmation at a time, and the destination
  // waits here until the agent chooses. Everything else keeps the existing "unsaved changes" confirm.
  const [leavePrompt, setLeavePrompt] = useState(null);   // { rows, unsavedCount, busy, failed, proceed }
  const leaveBusy = useRef(false);                         // blocks a double click before React re-renders
  const guardedLeave = (proceed) => {
    if (leavePrompt) return;
    if (safeActivePage === "process-calls") {
      const pending = pendingNavigationWork("calls");
      if (pending.rows.length) {
        setLeavePrompt({ rows: pending.rows, unsavedCount: pending.unsavedCount, busy: false, failed: [], proceed });
        return;
      }
    }
    if (!confirmLeaveIfUnsaved()) return;
    proceed();
  };
  const stayOnPage = () => { if (!leaveBusy.current) setLeavePrompt(null); };
  const submitAndLeave = async () => {
    if (leaveBusy.current || !leavePrompt) return;
    leaveBusy.current = true;
    const { proceed } = leavePrompt;
    setLeavePrompt((current) => (current ? { ...current, busy: true, failed: [] } : current));
    let outcome;
    try { outcome = await submitPendingNavigationWork("calls"); } catch { outcome = { submitted: [], failed: [{ ref: "Submit", error: "Could not submit. Nothing was changed." }] }; }
    leaveBusy.current = false;
    if (!outcome.failed.length) { setLeavePrompt(null); proceed(); return; }   // every call submitted: now leave
    const fresh = pendingNavigationWork("calls");                              // stay: show what is still pending and why
    setLeavePrompt((current) => (current ? { ...current, busy: false, failed: outcome.failed, rows: fresh.rows.length ? fresh.rows : current.rows, unsavedCount: fresh.unsavedCount } : current));
  };

  const handleLogin = (data) => {
    localStorage.setItem(FD_TOKEN_KEY, data.access_token);
    localStorage.setItem(FD_USER_KEY, JSON.stringify(data.user));
    localStorage.setItem(FD_PAGE_KEY, "dashboard");
    setUser(data.user);
  };

  const handleLogout = () => {
    clearAuthSession();
    setUser(null);
    setAssignment(null);
    setProfileOpen(false);
    setNotificationsOpen(false);
    setNotifications([]);
    setUnreadCount(0);
    setNotificationFilter("all");
    setFilteredNotifications([]);
  };

  useEffect(() => {
    if (!user) return;
    getMyAssignment()
      .then(setAssignment)
      .catch(() => setAssignment(null));
  }, [user]);

  useEffect(() => {
    if (user) localStorage.setItem(FD_PAGE_KEY, safeActivePage);
  }, [safeActivePage, user]);

  useEffect(() => {
    if (!user) return;
    setVisitedPages((current) =>
      current.has(safeActivePage)
        ? current
        : new Set([...current, safeActivePage]),
    );
  }, [safeActivePage, user]);

  useEffect(() => {
    if (!user) return undefined;
    let cancelled = false;
    let refreshInFlight = false;
    const refreshNotifications = async () => {
      if (cancelled || refreshInFlight || document.visibilityState === "hidden")
        return;
      refreshInFlight = true;
      try {
        const [rows, summary] = await Promise.all([
          getMyTaskNotifications(),
          getTaskNotificationSummary().catch(() => null),
        ]);
        if (!cancelled) {
          const next = rows || [];
          setUnreadCount(summary ? summary.unread : next.filter((item) => !item.read_at).length);
          const view = notificationViewRef.current;
          if (view.open && view.filter !== "all") {
            getMyTaskNotifications(view.filter)
              .then((filtered) => { if (!cancelled && notificationViewRef.current.filter === view.filter) setFilteredNotifications(filtered || []); })
              .catch(() => {});
          }
          const isInitialLoad = seenNotificationIds.current.size === 0;
          const newItems = next.filter((item) => !seenNotificationIds.current.has(item.id));
          next.forEach((item) => seenNotificationIds.current.add(item.id));
          setNotifications(next);
          if (!isInitialLoad && newItems.length) {
            setNotificationToasts((current) => [...current, ...newItems].slice(-3));
            window.dispatchEvent(new Event("flintdesk-data-changed"));
          }
        }
      } catch {
        // Retain the last successful list and wait for the next scheduled refresh.
      } finally {
        refreshInFlight = false;
      }
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refreshNotifications();
    };
    refreshNotifications();
    const timer = window.setInterval(
      refreshNotifications,
      NOTIFICATION_POLL_INTERVAL_MS,
    );
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [user]);

  useEffect(() => {
    if (!notificationToasts.length) return undefined;
    const timer = window.setTimeout(() => setNotificationToasts((items) => items.slice(1)), 7000);
    return () => window.clearTimeout(timer);
  }, [notificationToasts]);

  // Loads the list for one filter from the server (the server enforces recipient + read state).
  const loadNotificationView = async (filter) => {
    setNotificationListState({ loading: true, error: "" });
    try {
      const [rows, summary] = await Promise.all([
        getMyTaskNotifications(filter),
        getTaskNotificationSummary().catch(() => null),
      ]);
      if (notificationViewRef.current.filter !== filter) return; // a newer selection superseded this one
      if (filter === "all") setNotifications(rows || []); else setFilteredNotifications(rows || []);
      if (summary) setUnreadCount(summary.unread);
      setNotificationListState({ loading: false, error: "" });
    } catch (exception) {
      if (notificationViewRef.current.filter !== filter) return;
      setNotificationListState({ loading: false, error: exception?.message || "Unable to load notifications." });
    }
  };

  const openNotifications = () => {
    const nextOpen = !notificationsOpen;
    setNotificationsOpen(nextOpen);
    setProfileOpen(false);
    notificationViewRef.current = { open: nextOpen, filter: notificationFilter };
    if (nextOpen) loadNotificationView(notificationFilter);
  };

  const selectNotificationFilter = (filter) => {
    setNotificationFilter(filter);
    notificationViewRef.current = { open: true, filter };
    loadNotificationView(filter);
  };

  const markAllNotificationsRead = async () => {
    setMarkingAllRead(true);
    try {
      await markAllTaskNotificationsRead();
      const now = new Date().toISOString();
      setNotifications((current) => current.map((item) => (item.read_at ? item : { ...item, read_at: now })));
      setUnreadCount(0);
      await loadNotificationView(notificationFilter);
    } catch (exception) {
      setNotificationListState({ loading: false, error: exception?.message || "Unable to mark notifications as read." });
    } finally {
      setMarkingAllRead(false);
    }
  };

  const openTaskNotification = async (notification) => {
    if (!notification.read_at) {
      try {
        await markTaskNotificationRead(notification.id);
        const readAt = new Date().toISOString();
        const markRead = (item) => (item.id === notification.id ? { ...item, read_at: readAt } : item);
        setNotifications((current) => current.map(markRead));
        setFilteredNotifications((current) => (notificationFilter === "unread"
          ? current.filter((item) => item.id !== notification.id)
          : current.map(markRead)));
        setUnreadCount((count) => Math.max(0, count - 1));
      } catch {
        /* keep navigation available */
      }
    }
    if (notification.notification_type === "non_responsiveness_assignment") {
      // Non-Responsive Call assignments still deep-link into their table on
      // the Assign Calls and Task page. Missed Call assignments now have
      // their own dedicated Missed Calls page (see targetPage below), so
      // this focus mechanism no longer applies to them.
      sessionStorage.setItem(
        "flintdesk_task_notification_focus",
        "non_responsiveness",
      );
      window.dispatchEvent(new Event("flintdesk-task-notification-focus"));
    }
    const assigningTab = tabForNotification(notification);
    const targetPage =
      notification.source_page === "dependencies" ||
      notification.notification_type?.startsWith("dependency_")
        ? "dependencies"
        : assigningTab
          ? ASSIGNING_PAGE
          : notification.source_page === "settings"
            ? "dashboard"
            : ASSIGNING_PAGE;
    guardedLeave(() => {
      setNotificationsOpen(false);
      setReviewCall(null);
      if (targetPage === ASSIGNING_PAGE) setAssigningRequest({ tab: assigningTab || "general", nonce: Date.now() });
      setActivePage(targetPage);
    });
  };

  // Closure notification "Done": open the Calls page on the exact call record that was changed.
  const openCallOnCallsPage = (target) => {
    const open = () => {
      setNotificationsOpen(false);
      setReviewCall(null);
      setActivePage("process-calls");
      requestCallFocus(target);
    };
    if (safeActivePage === "process-calls") open(); else guardedLeave(open);
  };

  if (!user) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className={`page ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <ClosureNotificationHost user={user} onOpenCall={openCallOnCallsPage} />
      {leavePrompt && (
        <UnsubmittedChangesDialog
          rows={leavePrompt.rows}
          unsavedCount={leavePrompt.unsavedCount}
          busy={leavePrompt.busy}
          failed={leavePrompt.failed}
          onStay={stayOnPage}
          onSubmitAndLeave={submitAndLeave}
        />
      )}
      <header className="topbar">
        <span className="brand">
          <span className="brand-flint">Flint</span>
          <span>Desk</span>
        </span>
        <div className="topbar-user-actions">
          {assignment && (
            <div
              className="context-pill"
              title={`Welcome ${user.name}, you will be working with number ${assignment.number}, extension ${assignment.extension} today.`}
            >
              <span className="context-pill-dot" aria-hidden="true" />
              <span className="context-pill-text">
                Welcome <strong>{user.name}</strong>, you will be working with number{" "}
                <strong>{assignment.number}</strong>, extension{" "}
                <strong>{assignment.extension}</strong> today.
              </span>
            </div>
          )}
          <div className="profile-menu-wrap">
            <button
              className="nm-icon"
              aria-label="Open notifications"
              title="Notifications"
              onClick={openNotifications}
            >
              <i className="fa-solid fa-bell" />
            </button>
            {unreadCount > 0 && <span className="notification-unread-dot" aria-label={`${unreadCount} unread notifications`} />}
            {notificationsOpen && (
              <div
                className="profile-popover task-notification-popover"
                role="dialog"
                aria-label="Notifications"
              >
                <div className="profile-popover-heading">
                  <strong>Notifications</strong>
                  <span>{unreadCount} unread</span>
                </div>
                <div className="task-notification-toolbar">
                  <div className="task-notification-tabs" role="tablist" aria-label="Filter notifications">
                    {NOTIFICATION_FILTERS.map((option) => (
                      <button
                        key={option.key}
                        type="button"
                        role="tab"
                        aria-selected={notificationFilter === option.key}
                        className={`task-notification-tab ${notificationFilter === option.key ? "active" : ""}`}
                        onClick={() => selectNotificationFilter(option.key)}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="task-notification-markall"
                    disabled={markingAllRead || unreadCount === 0}
                    onClick={markAllNotificationsRead}
                  >
                    {markingAllRead ? "Marking…" : "Mark all as read"}
                  </button>
                </div>
                <div className="task-notification-list">
                  {(() => {
                    const shown = notificationFilter === "all" ? notifications : filterNotifications(filteredNotifications, notificationFilter);
                    const { loading, error } = notificationListState;
                    return (
                      <>
                        {error && <p className="task-notification-error" role="alert">{error}</p>}
                        {!shown.length && loading && !error && <p className="muted" role="status">Loading notifications…</p>}
                        {!shown.length && !loading && !error && <p className="muted">{emptyNotificationMessage(notificationFilter)}</p>}
                        {shown.map((item) => (
                          <button
                            type="button"
                            className={`task-notification-item ${item.read_at ? "" : "unread"}`}
                            key={item.id}
                            onClick={() => openTaskNotification(item)}
                          >
                            <strong>{item.title}</strong>
                            <span>{item.message}</span>
                          </button>
                        ))}
                      </>
                    );
                  })()}
                </div>
              </div>
            )}
          </div>
          <div className="profile-menu-wrap">
            <button
              className="nm-icon"
              aria-label="Open profile"
              title={`Open profile (${user.name})`}
              onClick={() => setProfileOpen((open) => !open)}
            >
              <i className="fa-solid fa-user" />
            </button>
            {profileOpen && (
              <div
                className="profile-popover"
                role="dialog"
                aria-label="Profile details"
              >
                <div className="profile-popover-heading">
                  <strong>{user.name}</strong>
                  <span>
                    {user.role || (isTeamLead ? "Team lead" : "Agent")}
                  </span>
                </div>
                <dl className="profile-details">
                  <div>
                    <dt>Email</dt>
                    <dd>{user.email || "-"}</dd>
                  </div>
                  <div>
                    <dt>Extension assigned</dt>
                    <dd>{assignment?.extension || "None"}</dd>
                  </div>
                  <div>
                    <dt>Number assigned</dt>
                    <dd>{assignment?.number || "-"}</dd>
                  </div>
                </dl>
                <button
                  type="button"
                  className="profile-signout-button"
                  onClick={handleLogout}
                >
                  <i className="fa-solid fa-right-from-bracket" /> Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className="notification-toast-stack" aria-live="polite">
        {notificationToasts.map((item) => (
          <button type="button" className="notification-toast" key={item.id} onClick={() => openTaskNotification(item)}>
            <strong>{item.title}</strong><span>{item.message}</span>
          </button>
        ))}
      </div>

      <div className="body">
        <Sidebar
          activePage={activePage}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((collapsed) => !collapsed)}
          onNavigate={(page) => {
            const go = () => {
              setReviewCall(null);
              setActivePage(
                (page === "settings" && !canAccessSettings(user)) ||
                  (page === "call-count-reconciliation" && !isAdmin(user))
                  ? "dashboard"
                  : page,
              );
            };
            if (page === safeActivePage) go(); else guardedLeave(go);
          }}
          user={user}
        />
        <main className="content dashboard-content">
          {reviewCall && (
            <ReviewCall call={reviewCall} onBack={() => setReviewCall(null)} />
          )}
          {Object.entries(pages).map(([pageKey, PageComponent]) =>
            visitedPages.has(pageKey) &&
            (pageKey !== "call-count-reconciliation" || isAdmin(user)) ? (
              <section
                key={pageKey}
                hidden={Boolean(reviewCall) || safeActivePage !== pageKey}
              >
                <PageComponent
                  isActive={!reviewCall && safeActivePage === pageKey}
                  onReview={setReviewCall}
                  onNavigate={(page) => {
                    const go = () => {
                      setReviewCall(null);
                      setActivePage(page);
                    };
                    if (page === safeActivePage) go(); else guardedLeave(go);
                  }}
                  user={user}
                  assignment={assignment}
                  isTeamLead={isTeamLead}
                  canEditSettings={isAdmin(user)}
                  {...(pageKey === ASSIGNING_PAGE ? { requestedTab: assigningRequest } : {})}
                />
              </section>
            ) : null,
          )}
        </main>
      </div>
    </div>
  );
}

function Sidebar({ activePage, collapsed, onToggle, onNavigate, user }) {
  const navItems = [
    ["fa-gauge", "Dashboard", "dashboard"],
    ["fa-arrow-down", "Calls", "process-calls"],
    ["fa-users", assigningPageTitle(isRestrictedAgent(user)), "assigning-calls"],
    ["fa-phone-slash", "Non Responsive Calls", "unanswered-calls"],
    ["fa-clipboard-list", "Call Updates", "call-updates"],
    ["fa-table-columns", "Agent To-Do", "todo"],
    ["fa-link", "Doubts", "dependencies"],
    ["fa-table-list", "Case Types + EQ", "case-types-eq"],
    ["fa-passport", "Visa Update", "visa-updates"],
    ["fa-circle-question", "FAQ", "faq"],
    ...(isAdmin(user)
      ? [["fa-scale-balanced", "Call Count Check", "call-count-reconciliation"]]
      : []),
    ...(canAccessSettings(user) ? [["fa-gear", "Settings", "settings"]] : []),
  ];
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <div>
        <div className="sidebar-heading">
          <p className="sidebar-section-title">Navigation</p>
          <button
            type="button"
            className="sidebar-toggle nm-icon square"
            onClick={onToggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden="true"
            >
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M9 4v16" />
            </svg>
          </button>
        </div>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {navItems.map(([icon, label, page]) => (
            <button
              key={page}
              onClick={() => onNavigate(page)}
              className={`nm-nav-item ${activePage === page ? "active" : ""} ${page === "unanswered-calls" ? "nm-nav-item-compact" : ""}`}
              title={collapsed ? label : undefined}
            >
              <i className={`fa-solid ${icon}`} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
      </div>
    </aside>
  );
}
