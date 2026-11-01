import { useAuth } from "../context/AuthContext";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  User,
  Mail,
  Shield,
  LogOut,
  Trash2,
} from "lucide-react";

function Settings() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState("account");

  const handleClearData = () => {
    if (window.confirm("This will clear all saved call records and filters. Continue?")) {
      localStorage.removeItem("call_records_filters");
      localStorage.removeItem("call_records_data");
      alert("Saved data cleared.");
    }
  };

  const tabs = [
    { id: "account", label: "Account", icon: User },
    { id: "data", label: "Data & Privacy", icon: Trash2 },
  ];

  return (
    <div className="settings-page">
      <div className="settings-header">
        <h1>Settings</h1>
      </div>

      <div className="settings-layout">
        {/* Sidebar */}
        <aside className="settings-sidebar">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                className={`settings-tab ${activeTab === tab.id ? "active" : ""}`}
                onClick={() => setActiveTab(tab.id)}
              >
                <Icon size={18} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </aside>

        {/* Content */}
        <div className="settings-content">
          {/* ACCOUNT TAB */}
          {activeTab === "account" && (
            <section className="settings-section">
              <h2>Account</h2>
              <div className="settings-card">
                <div className="setting-row">
                  <div className="setting-icon"><User size={20} /></div>
                  <div className="setting-info">
                    <span className="setting-label">Name</span>
                    <span className="setting-value">{user?.name || "N/A"}</span>
                  </div>
                </div>
                <div className="setting-divider" />
                <div className="setting-row">
                  <div className="setting-icon"><Mail size={20} /></div>
                  <div className="setting-info">
                    <span className="setting-label">Email</span>
                    <span className="setting-value">{user?.email || "N/A"}</span>
                  </div>
                </div>
                <div className="setting-divider" />
                <div className="setting-row">
                  <div className="setting-icon"><Shield size={20} /></div>
                  <div className="setting-info">
                    <span className="setting-label">Role</span>
                    <span className={`role-badge ${user?.is_superuser ? "superuser" : ""}`}>
                      {user?.is_superuser ? "Super User" : "User"}
                    </span>
                  </div>
                </div>
              </div>


              <div className="settings-card danger">
                <h3>Session</h3>
                <button className="btn-logout" onClick={logout}>
                  <LogOut size={16} />
                  Sign Out
                </button>
              </div>
            </section>
          )}

          {/* DATA & PRIVACY TAB */}
          {activeTab === "data" && (
            <section className="settings-section">
              <h2>Data & Privacy</h2>
              <div className="settings-card">
                <div className="setting-row">
                  <div className="setting-info">
                    <span className="setting-label">Saved Call Records</span>
                    <span className="setting-value">
                      Filters and fetched calls are saved locally so they persist when you navigate away.
                    </span>
                  </div>
                </div>
                <div className="setting-divider" />
                <div className="setting-row">
                  <div className="setting-info">
                    <span className="setting-label">Clear Local Data</span>
                    <span className="setting-value">
                      Remove all saved filters and call records from this device.
                    </span>
                  </div>
                  <button className="btn-danger-outline" onClick={handleClearData}>
                    <Trash2 size={14} />
                    Clear
                  </button>
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

export default Settings;
