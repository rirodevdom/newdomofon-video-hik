#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_types(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "  playback_uri: string;\n  track_id: string;",
        "  playback_uri: string;\n  original_playback_uri?: string;\n  track_id: string;",
        "device archive original URI type",
    )
    path.write_text(text, encoding="utf-8")


def patch_device_archive(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old_request = '''function requestXml(searchId: string, trackId: string, start: Date, end: Date, position: number): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription>
  <searchID>${searchId}</searchID>
  <trackList><trackID>${trackId}</trackID></trackList>
  <timeSpanList><timeSpan><startTime>${formatIsapiTime(start)}</startTime><endTime>${formatIsapiTime(end)}</endTime></timeSpan></timeSpanList>
  <maxResults>${config.archiveSearchPageSize}</maxResults>
  <searchResultPosition>${position}</searchResultPosition>
  <metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>`;
}'''
    new_request = '''type ArchiveSearchProfile = {
  positionTag: 'searchResultPostion' | 'searchResultPosition';
  metadataDescriptor: string;
};

const ARCHIVE_SEARCH_PROFILES: ArchiveSearchProfile[] = [
  // DS-H208QA / firmware V4.21.100 and other legacy RaCM devices use the
  // historical ISAPI typo "Postion" in CMSearchDescription.
  { positionTag: 'searchResultPostion', metadataDescriptor: '//recordType.meta.std-cgi.com' },
  // Keep compatibility with newer firmwares that corrected the field name.
  { positionTag: 'searchResultPosition', metadataDescriptor: '//recordType.meta.std-cgi.com' },
  // Some models only expose continuous recording through the timing subtype.
  { positionTag: 'searchResultPostion', metadataDescriptor: '//recordType.meta.std-cgi.com/timing' }
];

export function buildArchiveSearchRequestXml(
  searchId: string,
  trackId: string,
  start: Date,
  end: Date,
  position: number,
  profile: ArchiveSearchProfile = ARCHIVE_SEARCH_PROFILES[0]!
): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription>
  <searchID>${searchId}</searchID>
  <trackList><trackID>${trackId}</trackID></trackList>
  <timeSpanList><timeSpan><startTime>${formatIsapiTime(start)}</startTime><endTime>${formatIsapiTime(end)}</endTime></timeSpan></timeSpanList>
  <contentTypeList><contentType>video</contentType></contentTypeList>
  <maxResults>${config.archiveSearchPageSize}</maxResults>
  <${profile.positionTag}>${position}</${profile.positionTag}>
  <metadataList><metadataDescriptor>${profile.metadataDescriptor}</metadataDescriptor></metadataList>
</CMSearchDescription>`;
}'''
    text = replace_once(text, old_request, new_request, "legacy archive search profiles")

    old_normalize = '''function normalizePlaybackUri(raw: string, device: HikvisionDeviceConfig, trackId: string, start: Date, end: Date): string {
  if (!raw) return playbackRtspFallback(device, trackId, start, end);
  try {
    const url = new URL(raw);
    if (url.protocol !== 'rtsp:') return playbackRtspFallback(device, trackId, start, end);
    if (['0.0.0.0', '127.0.0.1', 'localhost'].includes(url.hostname.toLowerCase())) url.hostname = device.host;
    if (!url.port) url.port = String(device.rtsp_port);
    if (!url.searchParams.has('starttime')) url.searchParams.set('starttime', start.toISOString().replace(/[-:]/g, '').replace(/\\.\\d{3}Z$/, 'Z'));
    if (!url.searchParams.has('endtime')) url.searchParams.set('endtime', end.toISOString().replace(/[-:]/g, '').replace(/\\.\\d{3}Z$/, 'Z'));
    return injectRtspCredentials(url.toString(), device);
  } catch {
    return playbackRtspFallback(device, trackId, start, end);
  }
}'''
    new_normalize = '''function compactPlaybackTime(date: Date): string {
  return date.toISOString().replace(/[-:]/g, '').replace(/\\.\\d{3}Z$/, 'Z');
}

function normalizeOriginalPlaybackUri(raw: string, device: HikvisionDeviceConfig): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== 'rtsp:') return null;
    if (['0.0.0.0', '127.0.0.1', 'localhost'].includes(url.hostname.toLowerCase())) url.hostname = device.host;
    if (!url.port) url.port = String(device.rtsp_port);
    return injectRtspCredentials(url.toString(), device);
  } catch {
    return null;
  }
}

export function normalizePlaybackUri(
  raw: string,
  device: HikvisionDeviceConfig,
  trackId: string,
  start: Date,
  end: Date
): string {
  const normalized = normalizeOriginalPlaybackUri(raw, device);
  if (!normalized) return playbackRtspFallback(device, trackId, start, end);
  try {
    const url = new URL(normalized);
    // A search result describes the containing recording file. For devices that
    // advertise playback-by-UTC, replace those file boundaries with the exact
    // seek window selected by the user.
    url.searchParams.set('starttime', compactPlaybackTime(start));
    url.searchParams.set('endtime', compactPlaybackTime(end));
    return url.toString();
  } catch {
    return playbackRtspFallback(device, trackId, start, end);
  }
}'''
    text = replace_once(text, old_normalize, new_normalize, "exact UTC playback URI")

    text = replace_once(
        text,
        "      playback_uri: normalizePlaybackUri(rawUri, device, trackId, start, end),\n      track_id: trackId",
        "      playback_uri: normalizePlaybackUri(rawUri, device, trackId, start, end),\n      original_playback_uri: normalizeOriginalPlaybackUri(rawUri, device) || undefined,\n      track_id: trackId",
        "preserve original playback URI",
    )

    old_search = '''async function searchTrack(
  client: IsapiClient,
  device: HikvisionDeviceConfig,
  trackId: string,
  start: Date,
  end: Date
): Promise<DeviceArchiveItem[]> {
  const searchId = crypto.randomUUID();
  const result: DeviceArchiveItem[] = [];
  for (let page = 0; page < config.archiveSearchMaxPages; page += 1) {
    const position = page * config.archiveSearchPageSize;
    const body = requestXml(searchId, trackId, start, end, position);
    let xml: string;
    try {
      xml = await client.post('/ISAPI/ContentMgmt/search', body);
    } catch {
      xml = await client.post('/ISAPI/ContentMgmt/search/', body);
    }
    const items = parseSearch(xml, device, trackId);
    result.push(...items);
    if (items.length < config.archiveSearchPageSize) break;
  }
  return result;
}'''
    new_search = '''async function searchTrackProfile(
  client: IsapiClient,
  device: HikvisionDeviceConfig,
  trackId: string,
  start: Date,
  end: Date,
  profile: ArchiveSearchProfile
): Promise<DeviceArchiveItem[]> {
  const searchId = crypto.randomUUID();
  const result: DeviceArchiveItem[] = [];
  for (let page = 0; page < config.archiveSearchMaxPages; page += 1) {
    const position = page * config.archiveSearchPageSize;
    const body = buildArchiveSearchRequestXml(searchId, trackId, start, end, position, profile);
    let xml: string;
    try {
      xml = await client.post('/ISAPI/ContentMgmt/search', body);
    } catch {
      xml = await client.post('/ISAPI/ContentMgmt/search/', body);
    }
    const items = parseSearch(xml, device, trackId);
    result.push(...items);
    if (items.length < config.archiveSearchPageSize) break;
  }
  return result;
}

async function searchTrack(
  client: IsapiClient,
  device: HikvisionDeviceConfig,
  trackId: string,
  start: Date,
  end: Date
): Promise<DeviceArchiveItem[]> {
  const errors: string[] = [];
  for (const profile of ARCHIVE_SEARCH_PROFILES) {
    try {
      const items = await searchTrackProfile(client, device, trackId, start, end, profile);
      if (items.length) return items;
    } catch (error) {
      errors.push(`${profile.positionTag}/${profile.metadataDescriptor}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  if (errors.length === ARCHIVE_SEARCH_PROFILES.length) {
    throw new Error(`All Hikvision archive search profiles failed: ${errors.join(' | ')}`);
  }
  return [];
}'''
    text = replace_once(text, old_search, new_search, "fallback archive search profiles")

    old_candidates = '''  const candidates = items
    .filter((item) => new Date(item.end) >= start && new Date(item.start) <= end)
    .map((item) => item.playback_uri);
  for (const trackId of channel.archive_track_ids) candidates.push(playbackRtspFallback(device, trackId, start, end));
  return [...new Set(candidates)];'''
    new_candidates = '''  const matching = items.filter((item) => new Date(item.end) >= start && new Date(item.start) <= end);
  const candidates = matching.map((item) => item.playback_uri);
  // Generic UTC playback is the preferred fallback for devices that advertise
  // isSupportPlaybackByUTC=true.
  for (const trackId of channel.archive_track_ids) candidates.push(playbackRtspFallback(device, trackId, start, end));
  // Keep the original file-bound URI last for incompatible older devices. It
  // must never win before the exact UTC candidates.
  for (const item of matching) {
    if (item.original_playback_uri) candidates.push(item.original_playback_uri);
  }
  return [...new Set(candidates)];'''
    text = replace_once(text, old_candidates, new_candidates, "ordered exact UTC playback candidates")

    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_types(root / "src/types.ts")
    patch_device_archive(root / "src/archive/deviceArchive.ts")
    print("Legacy Hikvision archive search and exact UTC playback prepared")


if __name__ == "__main__":
    main()
