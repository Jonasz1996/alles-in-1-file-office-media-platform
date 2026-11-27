// Live tv (IPTV): de fouten van /api/v1/iptv en /api/v1/admin/iptv
export default {
  iptv_busy: { en: "All tuners are in use" },
  iptv_upstream: { en: "The provider does not deliver this channel right now" },
  iptv_playlist: { en: "This is not a usable m3u channel list" },
  iptv_unreachable: { en: "The channel list cannot be fetched" },
} satisfies Record<string, { en: string }>;
