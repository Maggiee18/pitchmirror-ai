import { useCallback, useEffect, useState } from "react";
import Home from "./components/Home";
import Presenter from "./components/Presenter";
import ReportView from "./components/ReportView";

type Route = { view: "home" } | { view: "present"; id: string } | { view: "report"; id: string };

function readRoute(): Route {
  const p = new URLSearchParams(location.search);
  const id = p.get("s");
  if (id && /^s_[0-9a-f]{10}$/.test(id)) return p.get("v") === "report" ? { view: "report", id } : { view: "present", id };
  return { view: "home" };
}

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);

  useEffect(() => {
    const onPop = () => setRoute(readRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const go = useCallback((r: Route) => {
    const url = r.view === "home" ? "/" : r.view === "present" ? `/?s=${r.id}` : `/?s=${r.id}&v=report`;
    history.pushState(null, "", url);
    setRoute(r);
  }, []);

  if (route.view === "present")
    return <Presenter key={route.id} sessionId={route.id} onHome={() => go({ view: "home" })} onReport={() => go({ view: "report", id: route.id })} />;
  if (route.view === "report")
    return (
      <ReportView
        key={route.id}
        sessionId={route.id}
        onHome={() => go({ view: "home" })}
        onPracticeAgain={() => go({ view: "present", id: route.id })}
        onOpenSession={(id) => go({ view: "present", id })}
        onOpenReport={(id) => go({ view: "report", id })}
      />
    );
  return <Home onSession={(id) => go({ view: "present", id })} />;
}
