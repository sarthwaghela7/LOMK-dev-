const normalizeRole = (user) => String(user?.role || "").trim().toLowerCase().replace(/[_-]/g, " ");

export const isAdmin = (user) => ["admin", "superuser", "super user"].includes(normalizeRole(user));
export const isTeamLead = (user) => Boolean(user?.is_team_lead) || ["team lead", "teamlead"].includes(normalizeRole(user));
export const isSubjectMatterExpert = (user) => ["subject matter expert", "sme"].includes(normalizeRole(user));

// These roles share dependency-review authority; the API must independently
// validate the same rule so a client cannot bypass the visible controls.
export const canApproveDependencies = (user) => isAdmin(user) || isTeamLead(user) || isSubjectMatterExpert(user);
export const canManageVisaUpdates = (user) => isAdmin(user) || isTeamLead(user) || isSubjectMatterExpert(user);
export const canAccessSettings = (user) => isAdmin(user) || isTeamLead(user) || isSubjectMatterExpert(user);
// Priority Cases: Team Leads, Subject Matter Experts and Admins (the API enforces the same set).
export const canAccessPriorityCases = (user) => isAdmin(user) || isTeamLead(user) || isSubjectMatterExpert(user);
export const isRestrictedAgent = (user) => !canApproveDependencies(user);
