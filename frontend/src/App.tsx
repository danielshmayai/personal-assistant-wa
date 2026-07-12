import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { SessionProvider, useSession } from "./lib/session";
import { FullPageSpinner } from "./components/ui";
import Login from "./pages/Login";
import Onboarding from "./pages/Onboarding";
import Dashboard from "./pages/Dashboard";
import Chat from "./pages/Chat";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import Nutrition from "./pages/Nutrition";
import Fitness from "./pages/Fitness";
import ModulePlaceholder from "./pages/ModulePlaceholder";

function Guarded({ children }: { children: React.ReactNode }) {
  const { me, loading } = useSession();
  if (loading) return <FullPageSpinner />;
  if (!me) return <Navigate to="/login" replace />;
  if (!me.tenant.onboarded) return <Navigate to="/onboarding" replace />;
  return <>{children}</>;
}

function OnboardingGuard() {
  const { me, loading } = useSession();
  if (loading) return <FullPageSpinner />;
  if (!me) return <Navigate to="/login" replace />;
  if (me.tenant.onboarded) return <Navigate to="/" replace />;
  return <Onboarding />;
}

export default function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/onboarding" element={<OnboardingGuard />} />
          <Route
            element={
              <Guarded>
                <Dashboard />
              </Guarded>
            }
          >
            <Route path="/" element={<Chat />} />
            <Route path="/nutrition" element={<Nutrition />} />
            <Route path="/fitness" element={<Fitness />} />
            <Route
              path="/smart-home"
              element={
                <ModulePlaceholder
                  icon="🏠"
                  title="Smart Home"
                  examples={["כבה את האור בסלון", "מה הסטטוס של המזגן?", "הדלק את הדוד"]}
                />
              }
            />
            <Route path="/settings" element={<Settings />} />
            <Route path="/admin" element={<Admin />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  );
}
