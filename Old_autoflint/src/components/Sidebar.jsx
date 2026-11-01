import { NavLink } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import {
  BarChart3,
  PhoneCall,
  Phone,
  FileText,
  Settings,
  Users,
  SlidersHorizontal,
  FileAudio,
  Clock,
  Award,
  HelpCircle,
  Briefcase,
} from "lucide-react";

function Sidebar({ isOpen }) {
  const { user, logout } = useAuth();

  const navItems = [
    { path: "/", label: "Dashboard", icon: BarChart3 },
    { path: "/calls", label: "Call Records", icon: Phone },
    // { path: "/analysis", label: "Call Analysis", icon: PhoneCall },
    { path: "/reports", label: "Report Analysis", icon: FileText },
    { path: "/transcript", label: "Transcript", icon: FileAudio },
    { path: "/analysis-settings", label: "Analysis Settings", icon: SlidersHorizontal },
    { path: "/pending-cases", label: "Pending Cases", icon: Clock },
    { path: "/success-cases", label: "Success Cases", icon: Award },
    { path: "/faq", label: "Client FAQ", icon: HelpCircle },
    { path: "/business", label: "Business", icon: Briefcase },
    { path: "/settings", label: "Settings", icon: Settings },
  ];

  if (user?.is_superuser) {
    navItems.push({ path: "/users", label: "User Management", icon: Users });
  }

  return (
    <>
      <aside className={`sidebar ${isOpen ? "open" : "hidden"}`}>
        <nav className="sidebar-nav">
          {navItems.map((item) => {
            const Icon = item.icon;

            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
                end={item.path === "/"}
              >
                <span className="nav-icon" aria-hidden="true">
                  <Icon size={16} />
                </span>
                <span className="nav-label">{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <button onClick={logout} className="logout-btn" title="Logout">
            <span className="logout-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                <polyline points="16 17 21 12 16 7"></polyline>
                <line x1="21" y1="12" x2="9" y2="12"></line>
              </svg>
            </span>
            <span className="logout-text">Logout</span>
          </button>
        </div>
      </aside>
    </>
  );
}

export default Sidebar;