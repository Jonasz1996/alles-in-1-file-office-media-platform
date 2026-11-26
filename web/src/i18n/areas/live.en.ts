import type { Translation } from "../types";
import type nl from "./live.nl";

const en: Translation<typeof nl> = {
  title: "Live TV",
  link: "Live TV",
  empty: "There are no channels yet.",
  emptyAdmin: "Set up a channel list (m3u) and a guide under Admin → Media → Live TV.",
  emptyUser: "An administrator sets up live TV.",
  search: "Search channel",
  allGroups: "All groups",
  group: "Group",
  favorites: "Favourites only",
  favorite: "Favourite",
  unfavorite: "Remove favourite",
  now: "Now",
  next: "Next",
  noGuide: "No guide for this channel",
  pick: "Pick a channel.",
  tabs: {
    channels: "Channels",
    guide: "Guide",
  },
  guide: {
    earlier: "Earlier",
    later: "Later",
    nowButton: "Now",
    watch: "Watch",
    close: "Close",
  },
  state: {
    connecting: "Connecting to {name}…",
    playing: "{name}",
    retrying: "The stream dropped; reconnecting…",
    ended: "The stream stopped.",
    unsupported: "This browser cannot play this channel.",
    busy: "All tuners are in use. Now playing: {channels}.",
    failed: "This channel does not play right now: {reason}",
  },
  again: "Try again",
  fullscreen: "Full screen",
  channelUp: "Next channel",
  channelDown: "Previous channel",
};

export default en;
