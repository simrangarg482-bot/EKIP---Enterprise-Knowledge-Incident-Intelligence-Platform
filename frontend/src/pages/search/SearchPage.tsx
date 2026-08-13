import { useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, BookOpen, Github, MessageSquare, Search as SearchIcon } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SearchBar } from "@/components/data/SearchBar";
import { Badge } from "@/components/ui/Badge";
import { LoadingState } from "@/components/ui/LoadingState";
import { EmptyState } from "@/components/ui/EmptyState";
import { useDebounce } from "@/hooks/useDebounce";
import { globalSearch } from "@/api/search";
import type { SearchResult, SearchResultType } from "@/types/search";
import { formatRelativeTime } from "@/utils/date";

const TYPE_META: Record<SearchResultType, { label: string; icon: LucideIcon }> = {
  incident: { label: "Incidents", icon: AlertCircle },
  knowledge: { label: "Knowledge", icon: BookOpen },
  slack: { label: "Slack", icon: MessageSquare },
  github: { label: "GitHub", icon: Github },
};

const TYPE_ORDER: SearchResultType[] = ["incident", "knowledge", "slack", "github"];

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") ?? "");
  const debouncedQuery = useDebounce(query, 300);

  const searchQuery = useQuery({
    queryKey: ["search", debouncedQuery],
    queryFn: () => globalSearch(debouncedQuery),
    enabled: debouncedQuery.trim().length > 0,
  });

  const grouped = useMemo(() => {
    const results = searchQuery.data ?? [];
    const groups: Record<SearchResultType, SearchResult[]> = {
      incident: [],
      knowledge: [],
      slack: [],
      github: [],
    };
    for (const result of results) groups[result.type].push(result);
    return groups;
  }, [searchQuery.data]);

  function handleChange(value: string) {
    setQuery(value);
    setParams(value ? { q: value } : {});
  }

  const hasQuery = debouncedQuery.trim().length > 0;
  const totalResults = searchQuery.data?.length ?? 0;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Search" description="Unified search across incidents, knowledge, Slack, and GitHub." />

      <SearchBar
        value={query}
        onChange={handleChange}
        placeholder="Search EKIP…"
        autoFocus
        className="max-w-2xl"
      />

      {!hasQuery && (
        <EmptyState
          icon={SearchIcon}
          title="Search across your engineering environment"
          description="Try an incident ID, error message, service name, or a question."
        />
      )}

      {hasQuery && searchQuery.isLoading && <LoadingState label="Searching…" />}

      {hasQuery && !searchQuery.isLoading && totalResults === 0 && (
        <EmptyState title="No results found" description="Try a different search term." />
      )}

      {hasQuery && !searchQuery.isLoading && totalResults > 0 && (
        <div className="flex flex-col gap-6">
          {TYPE_ORDER.filter((type) => grouped[type].length > 0).map((type) => {
            const { label, icon: Icon } = TYPE_META[type];
            return (
              <section key={type}>
                <div className="mb-2 flex items-center gap-2 border-b border-border pb-2">
                  <Icon className="h-4 w-4 text-ink-muted" />
                  <h2 className="text-sm font-semibold text-ink">{label}</h2>
                  <Badge tone="neutral">{grouped[type].length}</Badge>
                </div>
                <ul className="flex flex-col gap-1">
                  {grouped[type].map((result) => (
                    <SearchResultRow key={result.id} result={result} />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SearchResultRow({ result }: { result: SearchResult }) {
  const href =
    result.type === "incident"
      ? `/incidents/${result.id}`
      : result.type === "knowledge"
        ? `/knowledge/${result.id}`
        : result.url ?? "#";

  return (
    <li>
      <Link
        to={href}
        className="flex flex-col gap-1 rounded-md px-3 py-2.5 hover:bg-slate-50"
      >
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-medium text-ink">{result.title}</p>
          {result.timestamp && (
            <span className="shrink-0 text-xs text-ink-subtle">{formatRelativeTime(result.timestamp)}</span>
          )}
        </div>
        <p className="line-clamp-2 text-sm text-ink-muted">{result.snippet}</p>
        <span className="text-xs text-ink-subtle">{result.source}</span>
      </Link>
    </li>
  );
}
