const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 31536000],
  ["month", 2592000],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
];

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function formatRelativeTime(iso: string): string {
  const diffSeconds = (new Date(iso).getTime() - Date.now()) / 1000;
  const absSeconds = Math.abs(diffSeconds);

  for (const [unit, secondsInUnit] of UNITS) {
    if (absSeconds >= secondsInUnit || unit === "second") {
      const value = Math.round(diffSeconds / secondsInUnit);
      return rtf.format(value, unit);
    }
  }
  return rtf.format(0, "second");
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
