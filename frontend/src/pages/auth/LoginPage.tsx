import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export function LoginPage() {
  const { login } = useAuth();
  const { toast } = useToast();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setIsSubmitting(true);
    try {
      await login({ email, password });
      navigate("/ask");
    } catch {
      toast({
        variant: "error",
        title: "Sign in failed",
        description: "Check your email and password and try again.",
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-surface px-6 py-6 shadow-panel">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div>
          <label htmlFor="email" className="mb-1.5 block text-xs font-medium text-ink-muted">
            Work email
          </label>
          <Input
            id="email"
            type="email"
            required
            autoFocus
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
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <Button type="submit" variant="primary" className="w-full" isLoading={isSubmitting}>
          Sign in
        </Button>
      </form>

      <p className="mt-6 text-center text-xs text-ink-subtle">
        Don't have an account?{" "}
        <Link to="/signup" className="font-medium text-accent hover:underline">
          Create one
        </Link>
      </p>
    </div>
  );
}
