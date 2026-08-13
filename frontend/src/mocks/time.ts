const NOW = Date.now();

export function minutesAgo(minutes: number): string {
  return new Date(NOW - minutes * 60_000).toISOString();
}

export function hoursAgo(hours: number): string {
  return minutesAgo(hours * 60);
}

export function daysAgo(days: number): string {
  return hoursAgo(days * 24);
}
