// De aanroepen van live tv (api/app/routers/iptv.py).
import { api } from "../api";

export interface Slot {
  id: number;
  start: string;
  stop: string;
  title: string;
  subtitle: string | null;
  category: string | null;
  episode: string | null;
}

export interface Programme extends Slot {
  channel: string;
  description: string | null;
}

export interface Channel {
  id: string;
  key: string;
  name: string;
  number: number | null;
  group: string | null;
  xmltv_id: string | null;
  logo: string | null;
  favorite: boolean;
  now: Slot | null;
  next: Slot | null;
}

export interface Channels {
  configured: boolean;
  channels: Channel[];
  groups: string[];
}

export interface Guide {
  start: string;
  stop: string;
  programmes: Record<string, Slot[]>;
}

const BASE = "/api/v1/iptv";

export const getChannels = () => api.request<Channels>("GET", `${BASE}/channels`);
export const getGuide = (start: Date, hours: number) =>
  api.request<Guide>("GET", `${BASE}/guide?${new URLSearchParams({ start: start.toISOString(), hours: String(hours) })}`);
export const getProgramme = (id: number) => api.request<Programme>("GET", `${BASE}/programmes/${id}`);
export const setFavorite = (key: string, on: boolean) =>
  api.request<void>(on ? "PUT" : "DELETE", `${BASE}/favorites/${encodeURIComponent(key)}`);
