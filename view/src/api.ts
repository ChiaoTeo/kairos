import type { TimelineData, TimelineInstanceIndex } from "./types";

export async function loadInstances(): Promise<TimelineInstanceIndex> {
  const response = await fetch("/api/instances", { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<TimelineInstanceIndex>;
}

export async function loadTimeline(path?: string | null): Promise<TimelineData> {
  const query = path ? `?${new URLSearchParams({ path })}` : "";
  const response = await fetch(`/api/timeline${query}`, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<TimelineData>;
}
