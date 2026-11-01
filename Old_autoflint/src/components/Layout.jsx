import { Outlet } from "react-router-dom";
import { useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import Sidebar from "./Sidebar";

function Layout() {
  const location = useLocation();
  const isCallsPage = location.pathname.startsWith("/calls");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    setIsSidebarOpen(false);
  }, [location.pathname]);

  return (
    <div className="app-layout">
      <Sidebar isOpen={isSidebarOpen} />
      <div className="main-area">
        <header className="app-header">
          <button
            type="button"
            className="app-header-brand"
            onClick={() => setIsSidebarOpen((prev) => !prev)}
          >
            AutoFlint
          </button>
        </header>
        <main className={`page-content ${isCallsPage ? "calls-page" : ""}`}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default Layout;
