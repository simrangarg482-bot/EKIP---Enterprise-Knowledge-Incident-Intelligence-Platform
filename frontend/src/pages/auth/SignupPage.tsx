import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { ApiError } from "@/types/common";

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function SignupPage() {
  const { signup } = useAuth();
  const { toast } = useToast();
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [organizationSlug, setOrganizationSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function handleOrganizationNameChange(value: string) {
    setOrganizationName(value);
    if (!slugTouched) setOrganizationSlug(slugify(value));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setIsSubmitting(true);
    try {
      await signup({
        email,
        password,
        displayName,
        organizationName,
        organizationSlug: organizationSlug || slugify(organizationName),
      });
      navigate("/ask");
    } catch (err) {
      const apiError = err as ApiError;
      const message =
        typeof apiError?.detail === "string" ? apiError.detail : "Please check your details and try again.";
      toast({ variant: "error", title: "Sign up failed", description: message });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-surface px-6 py-6 shadow-panel">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div>
          <label htmlFor="displayName" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Your name
          </label>
          <Input
            id="displayName"
            required
            autoFocus
            placeholder="Jane Doe"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="email" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Work email
          </label>
          <Input
            id="email"
            type="email"
            required
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="password" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Password
          </label>
          <Input
            id="password"
            type="password"
            required
            minLength={8}
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="organizationName" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Organization name
          </label>
          <Input
            id="organizationName"
            required
            placeholder="Acme Inc"
            value={organizationName}
            onChange={(e) => handleOrganizationNameChange(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="organizationSlug" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Organization URL slug
          </label>
          <Input
            id="organizationSlug"
            required
            pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
            placeholder="acme-inc"
            value={organizationSlug}
            onChange={(e) => {
              setSlugTouched(true);
              setOrganizationSlug(slugify(e.target.value));
            }}
          />
        </div>
        <Button type="submit" variant="primary" className="w-full" isLoading={isSubmitting}>
          Create account
        </Button>
      </form>

      <p className="mt-6 text-center text-xs text-ink-subtle">
        Already have an account?{" "}
        <Link to="/login" className="font-medium text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
