import type { SearchResult } from "@/types/search";
import { hoursAgo, minutesAgo } from "@/mocks/time";

export const mockSearchResults: SearchResult[] = [
  {
    id: "inc-1024",
    type: "incident",
    title: "INC-1024 — Payment API returning 500 errors",
    snippet: "Payment API failures after deployment of payment-service v2.14.0. Error rate climbing, currently under investigation.",
    source: "Incidents",
    timestamp: minutesAgo(12),
  },
  {
    id: "inc-984",
    type: "incident",
    title: "INC-984 — Checkout 500s after promo-code service deploy",
    snippet: "Historical incident with a matching error signature in CheckoutOrchestrator.ApplyDiscount.",
    source: "Incidents",
    timestamp: hoursAgo(31 * 24),
  },
  {
    id: "doc-1",
    type: "knowledge",
    title: "Payment API Deployment Runbook",
    snippet: "Rollback procedure: revert to the previous stable tag and verify checkout error rate returns below 1%...",
    source: "GitHub",
    timestamp: hoursAgo(2),
  },
  {
    id: "doc-4",
    type: "knowledge",
    title: "Incident INC-984 — Promo Code Null Reference (Postmortem)",
    snippet: "Root cause was a null discount configuration object for campaigns missing promo_rules entries...",
    source: "Slack",
    timestamp: hoursAgo(31 * 24),
  },
  {
    id: "slack-1",
    type: "slack",
    title: "#payments",
    snippet: "\"We noticed similar 500s last month right after a promo campaign launch — check promo_rules first.\"",
    source: "#payments",
    timestamp: hoursAgo(30 * 24),
  },
  {
    id: "gh-1",
    type: "github",
    title: "payment-service / CheckoutOrchestrator.cs",
    snippet: "public decimal ApplyDiscount(Order order, PromoRule rule) { return order.Total * rule.Multiplier; }",
    source: "GitHub / payment-service",
    timestamp: hoursAgo(3),
  },
];
