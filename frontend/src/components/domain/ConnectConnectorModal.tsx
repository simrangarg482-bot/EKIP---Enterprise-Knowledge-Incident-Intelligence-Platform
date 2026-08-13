import { useState } from "react";
import { Github, MessageSquare, Plus, Trash2 } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { cn } from "@/utils/cn";
import type { GithubRepoConfig } from "@/types/connector";

interface ConnectConnectorModalProps {
  open: boolean;
  onClose: () => void;
  onSubmitGithub: (token: string, repos: GithubRepoConfig[]) => Promise<void>;
  onSubmitSlack: (token: string, channelIds: string[]) => Promise<void>;
  isSubmitting: boolean;
}

type SourceTab = "github" | "slack";

export function ConnectConnectorModal({
  open,
  onClose,
  onSubmitGithub,
  onSubmitSlack,
  isSubmitting,
}: ConnectConnectorModalProps) {
  const [tab, setTab] = useState<SourceTab>("github");

  const [githubToken, setGithubToken] = useState("");
  const [repos, setRepos] = useState<GithubRepoConfig[]>([{ repo: "", ref: "" }]);

  const [slackToken, setSlackToken] = useState("");
  const [channelIds, setChannelIds] = useState<string[]>([""]);

  function resetAndClose() {
    setGithubToken("");
    setRepos([{ repo: "", ref: "" }]);
    setSlackToken("");
    setChannelIds([""]);
    setTab("github");
    onClose();
  }

  async function handleGithubSubmit(event: React.FormEvent) {
    event.preventDefault();
    const cleanedRepos = repos
      .filter((r) => r.repo.trim().length > 0)
      .map((r) => ({ repo: r.repo.trim(), ref: r.ref?.trim() || undefined }));
    if (!githubToken.trim() || cleanedRepos.length === 0) return;
    await onSubmitGithub(githubToken.trim(), cleanedRepos);
    resetAndClose();
  }

  async function handleSlackSubmit(event: React.FormEvent) {
    event.preventDefault();
    const cleanedChannels = channelIds.map((c) => c.trim()).filter(Boolean);
    if (!slackToken.trim() || cleanedChannels.length === 0) return;
    await onSubmitSlack(slackToken.trim(), cleanedChannels);
    resetAndClose();
  }

  return (
    <Modal
      open={open}
      onClose={resetAndClose}
      title="Connect a source"
      description="Credentials are envelope-encrypted at rest and never displayed again after saving."
      className="max-w-xl"
    >
      <div className="mb-4 flex gap-2">
        <button
          type="button"
          onClick={() => setTab("github")}
          className={cn(
            "flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium",
            tab === "github" ? "border-accent-border bg-accent-subtle text-accent" : "border-border text-ink-muted",
          )}
        >
          <Github className="h-4 w-4" />
          GitHub
        </button>
        <button
          type="button"
          onClick={() => setTab("slack")}
          className={cn(
            "flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-medium",
            tab === "slack" ? "border-accent-border bg-accent-subtle text-accent" : "border-border text-ink-muted",
          )}
        >
          <MessageSquare className="h-4 w-4" />
          Slack
        </button>
      </div>

      {tab === "github" && (
        <form onSubmit={handleGithubSubmit} className="flex flex-col gap-3">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-muted">
              Personal access token
            </label>
            <Input
              type="password"
              required
              placeholder="ghp_…"
              value={githubToken}
              onChange={(e) => setGithubToken(e.target.value)}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-muted">Repositories</label>
            <div className="flex flex-col gap-2">
              {repos.map((row, index) => (
                <div key={index} className="flex gap-2">
                  <Input
                    placeholder="owner/repo"
                    value={row.repo}
                    onChange={(e) =>
                      setRepos((prev) =>
                        prev.map((r, i) => (i === index ? { ...r, repo: e.target.value } : r)),
                      )
                    }
                    className="flex-1"
                  />
                  <Input
                    placeholder="branch (default: main)"
                    value={row.ref ?? ""}
                    onChange={(e) =>
                      setRepos((prev) =>
                        prev.map((r, i) => (i === index ? { ...r, ref: e.target.value } : r)),
                      )
                    }
                    className="w-40"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="Remove repository"
                    onClick={() => setRepos((prev) => prev.filter((_, i) => i !== index))}
                    disabled={repos.length === 1}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mt-2 gap-1.5"
              onClick={() => setRepos((prev) => [...prev, { repo: "", ref: "" }])}
            >
              <Plus className="h-3.5 w-3.5" />
              Add repository
            </Button>
          </div>

          <Button type="submit" variant="primary" isLoading={isSubmitting} className="mt-2">
            Connect GitHub
          </Button>
        </form>
      )}

      {tab === "slack" && (
        <form onSubmit={handleSlackSubmit} className="flex flex-col gap-3">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-muted">Bot token</label>
            <Input
              type="password"
              required
              placeholder="xoxb-…"
              value={slackToken}
              onChange={(e) => setSlackToken(e.target.value)}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-muted">Channel IDs</label>
            <div className="flex flex-col gap-2">
              {channelIds.map((channelId, index) => (
                <div key={index} className="flex gap-2">
                  <Input
                    placeholder="C0123456789"
                    value={channelId}
                    onChange={(e) =>
                      setChannelIds((prev) => prev.map((c, i) => (i === index ? e.target.value : c)))
                    }
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="Remove channel"
                    onClick={() => setChannelIds((prev) => prev.filter((_, i) => i !== index))}
                    disabled={channelIds.length === 1}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mt-2 gap-1.5"
              onClick={() => setChannelIds((prev) => [...prev, ""])}
            >
              <Plus className="h-3.5 w-3.5" />
              Add channel
            </Button>
          </div>

          <Button type="submit" variant="primary" isLoading={isSubmitting} className="mt-2">
            Connect Slack
          </Button>
        </form>
      )}
    </Modal>
  );
}
