import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { SendHorizontal, Sparkles, MessageCircleQuestion } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { ErrorState } from "@/components/ui/ErrorState";
import { ChatMessage } from "@/components/domain/ChatMessage";
import { askQuestion, getQuestionHistory } from "@/api/ask";
import type { ApiError } from "@/types/common";
import type { ChatTurn } from "@/types/ask";
import { formatDateTime } from "@/utils/date";
import { formatPercent } from "@/utils/format";

let turnCounter = 0;

export function AskPage() {
  const [tab, setTab] = useState<"chat" | "history">("chat");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [query, setQuery] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const historyQuery = useQuery({
    queryKey: ["ask-history"],
    queryFn: () => getQuestionHistory(50, 0),
    enabled: tab === "history",
  });

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const question = query.trim();
    if (!question || isSubmitting) return;

    const turnId = `turn-${++turnCounter}`;
    setTurns((prev) => [...prev, { id: turnId, question, isPending: true }]);
    setQuery("");
    setIsSubmitting(true);
    requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }));

    try {
      const response = await askQuestion(question);
      setTurns((prev) =>
        prev.map((turn) => (turn.id === turnId ? { ...turn, isPending: false, response } : turn)),
      );
    } catch (err) {
      const apiError = err as ApiError;
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === turnId
            ? { ...turn, isPending: false, error: apiError?.message ?? "Something went wrong." }
            : turn,
        ),
      );
    } finally {
      setIsSubmitting(false);
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }));
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <PageHeader
        title="Ask EKIP"
        description="Ask a natural-language question grounded in your ingested incidents, code, and conversations."
      />

      <Tabs
        items={[
          { key: "chat", label: "Chat" },
          { key: "history", label: "History" },
        ]}
        activeKey={tab}
        onChange={(key) => setTab(key as "chat" | "history")}
      />

      {tab === "chat" && (
        <div className="flex flex-1 flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto scrollbar-thin">
            {turns.length === 0 ? (
              <EmptyState
                icon={MessageCircleQuestion}
                title="Ask your first question"
                description='Try something like "What changed recently in the payments service?" or "Have we seen this error before?"'
              />
            ) : (
              <div className="flex flex-col gap-6 pb-4">
                {turns.map((turn) => (
                  <ChatMessage key={turn.id} turn={turn} />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="mt-2 flex items-center gap-2 border-t border-border pt-3">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask EKIP anything about your systems…"
              autoFocus
              disabled={isSubmitting}
              className="flex-1"
            />
            <Button type="submit" variant="primary" isLoading={isSubmitting} disabled={!query.trim()}>
              <SendHorizontal className="h-4 w-4" />
              Ask
            </Button>
          </form>
        </div>
      )}

      {tab === "history" && (
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {historyQuery.isLoading && <LoadingState label="Loading history…" />}
          {historyQuery.isError && <ErrorState onRetry={() => historyQuery.refetch()} />}
          {historyQuery.data && historyQuery.data.length === 0 && (
            <EmptyState
              icon={Sparkles}
              title="No questions yet"
              description="Questions you ask EKIP will show up here."
            />
          )}
          {historyQuery.data && historyQuery.data.length > 0 && (
            <ul className="flex flex-col gap-1.5">
              {historyQuery.data.map((entry) => (
                <li key={entry.id}>
                  <button
                    type="button"
                    onClick={() => {
                      const question = entry.inputSummary?.query;
                      if (question) {
                        setQuery(question);
                        setTab("chat");
                      }
                    }}
                    className="flex w-full flex-col gap-1 rounded-md border border-border bg-white px-3 py-2.5 text-left hover:border-accent-border hover:bg-accent-subtle"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className="truncate text-sm font-medium text-ink">
                        {entry.inputSummary?.query ?? "(no query recorded)"}
                      </p>
                      <span className="shrink-0 text-xs text-ink-subtle">
                        {formatDateTime(entry.startedAt)}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-ink-muted">
                      <span className="capitalize">{entry.status}</span>
                      {entry.confidenceScore !== null && (
                        <>
                          <span className="text-ink-subtle">·</span>
                          <span>{formatPercent(entry.confidenceScore)} confidence</span>
                        </>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
