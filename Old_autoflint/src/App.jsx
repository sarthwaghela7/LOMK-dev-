import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import { BufferingLoader } from "./components/BufferingLoader";
import Layout from "./components/Layout";
import Login from "./auth/Login";
import Dashboard from "./pages/Dashboard";
import CallRecords from "./pages/CallRecords";
import ReportAnalysis from "./pages/ReportAnalysis";
import Settings from "./pages/Settings";
import UserManagement from "./pages/UserManagement";
import CreateUser from "./pages/CreateUser";
import AnalysisManagement from "./pages/AnalysisManagement";
import Transcript from "./pages/Transcript";
import PendingCases from "./pages/PendingCases";
import SuccessCases from "./pages/SuccessCases";
import ClientFAQ from "./pages/ClientFAQ";
import Business from "./pages/Business";


function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="route-loading">
        <BufferingLoader text="Authenticating..." size="lg" />
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={!user ? <Login /> : <Navigate to="/" />} />
      <Route element={user ? <Layout /> : <Navigate to="/login" />}>
        <Route index element={<Dashboard />} />
        <Route path="calls" element={<CallRecords />} />
        <Route path="reports" element={<ReportAnalysis />} />
        <Route path="transcript" element={<Transcript />} />
        <Route path="settings" element={<Settings />} />
        <Route path="analysis-settings" element={<AnalysisManagement />} />
        <Route path="users/create" element={<CreateUser />} />
        <Route path="users" element={<UserManagement />} />
        <Route path="pending-cases" element={<PendingCases />} />
        <Route path="success-cases" element={<SuccessCases />} />
        <Route path="faq" element={<ClientFAQ />} />
        <Route path="business" element={<Business />} />
      </Route>
    </Routes>
  );
}

export default App;