// Foutcodes van media (fase 5, bouwontwerp §3.4): de bibliotheken, afspelen, de stroom, de
// worker, de import en de providers. De Nederlandse tekst komt van de server; dit is de Engelse.
// Bestaande codes (not_found, no_room, rate_limited, guest, read_only) staan in hun eigen
// catalogus. {naam} wordt ingevuld uit de params.
export default {
  media_off: { en: "Media is not available on this server" },
  media_worker_down: { en: "The media worker cannot be reached right now" },
  media_ffmpeg_missing: { en: "ffmpeg is missing or not working in the media worker" },
  media_probe_pending: { en: "This file is still being prepared; try again in a moment" },
  media_unavailable: { en: "This file cannot be played" },
  media_unsupported: { en: "This file cannot be played on this device" },
  media_hdr: { en: "This HDR video cannot be converted" },
  media_busy: { en: "The server is too busy to play this right now" },
  media_session_gone: { en: "This playback session no longer exists" },
  media_access_gone: { en: "You no longer have access to this video" },
  media_timeout: { en: "Converting is taking too long" },
  media_transcode_failed: { en: "Converting this video failed" },
  media_public_transcode: { en: "Converting is not allowed through a public link" },
  media_not_owner: { en: "Only the owner of the folder can make it a library" },
  media_library_nested: { en: "This folder is inside or above another library" },
  media_library_not_folder: { en: "A library has to be a folder" },
  media_library_limit: { en: "You already have {max} libraries" },
  media_import_path: { en: "This path is not inside the import folder" },
  media_import_busy: { en: "An import is already running" },
  media_bench_busy: { en: "Measuring is only possible when nothing is being converted" },
  media_provider_off: { en: "Online lookup is turned off" },
  media_provider_auth: { en: "The provider key does not work" },
  media_provider_unavailable: { en: "The provider cannot be reached" },
  media_provider_rate: { en: "The provider is slowing down requests; try again soon" },
  media_playlist_full: { en: "A playlist has at most {max} tracks" },
} satisfies Record<string, { en: string }>;
