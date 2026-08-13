import { Link2 } from "lucide-react";
import type { Citation } from "@/types/ask";

export function AskCitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;

  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle">Sources</p>
      <ul className="flex flex-col gap-1.5">
        {citations.map((citation) => (
          <li key={citation.chunkId}>
            <a
              href={citation.sourceUrl ?? "#"}
              target={citation.sourceUrl ? "_blank" : undefined}
              rel="noreferrer"
              className="flex items-start gap-2 rounded-md border border-border bg-white px-2.5 py-1.5 text-xs text-ink hover:border-accent-border hover:bg-accent-subtle"
            >
              <Link2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-muted" />
              <span className="line-clamp-2">{citation.excerpt}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
