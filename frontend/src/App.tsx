import { useEffect, useState } from "react";
import ChatContainer from "./components/ChatContainer";
import KnowledgeBasePage from "./components/KnowledgeBasePage";

type Route = "chat" | "knowledge";

/** 轻量 hash 路由：不引入 react-router，未知 hash 回退对话页。 */
export function readHashRoute(hash: string): Route {
  return hash === "#/knowledge" ? "knowledge" : "chat";
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => readHashRoute(window.location.hash));

  useEffect(() => {
    if (window.location.hash !== "#/chat" && window.location.hash !== "#/knowledge") {
      window.history.replaceState(null, "", "#/chat");
    }
    const onHashChange = () => setRoute(readHashRoute(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return route === "knowledge" ? <KnowledgeBasePage /> : <ChatContainer />;
}
