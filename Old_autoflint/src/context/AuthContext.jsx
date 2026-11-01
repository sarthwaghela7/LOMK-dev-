import { createContext, useContext, useState, useEffect } from "react";
import { getCurrentUser, API_BASE_URL } from "../data/api";

const AuthContext = createContext(null);
const CALLS_STORAGE_KEYS = ["call_records_filters", "call_records_data"];

function clearCallReportStorage() {
  CALLS_STORAGE_KEYS.forEach((key) => localStorage.removeItem(key));
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("autoflint_token");
    if (token) {
      getCurrentUser(token)
        .then((data) => setUser(data))
        .catch(() => {
          localStorage.removeItem("autoflint_token");
          setUser(null);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = (token, userData) => {
    localStorage.setItem("autoflint_token", token);
    clearCallReportStorage();
    setUser(userData);
  };

  const logout = async () => {
    const token = localStorage.getItem("autoflint_token");
    try {
      if (token) {
        await fetch(`${API_BASE_URL}/auth/logout`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
      }
    } catch (err) {
      console.error("Logout endpoint error:", err);
    } finally {
      localStorage.removeItem("autoflint_token");
      clearCallReportStorage();
      setUser(null);
      window.location.href = "/login";
    }
  };

  return (
    <AuthContext.Provider value={{ user, login, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
