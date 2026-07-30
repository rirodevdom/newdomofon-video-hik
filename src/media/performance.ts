import type { HikvisionChannel, HikvisionStreamSettings } from '../types.js';

function normalizedCodec(value: string | null | undefined): string {
  return String(value || '').toUpperCase().replace(/[^A-Z0-9]+/g, '');
}

export function isH264Codec(value: string | null | undefined): boolean {
  const codec = normalizedCodec(value);
  return codec.includes('H264') || codec.includes('AVC');
}

function primaryStream(channel: HikvisionChannel): HikvisionStreamSettings | undefined {
  return channel.streams.find((stream) => stream.id === channel.primary_stream_id)
    || channel.streams.find((stream) => stream.enabled !== false)
    || channel.streams[0];
}

export function selectLiveStreamId(channel: HikvisionChannel, policy = 'auto'): string {
  const available = channel.streams.filter((stream) => stream.enabled !== false);
  const primary = primaryStream(channel);
  if (!available.length) return primary?.id || channel.primary_stream_id;
  if (policy === 'primary') return primary?.id || available[0]!.id;

  // Fast live playback should avoid eight simultaneous H.265 -> H.264 CPU
  // transcodes when the NVR already exposes an H.264 secondary stream.
  const primaryH264 = primary && isH264Codec(primary.video_codec) ? primary : undefined;
  const h264Sub = available.find((stream) => stream.stream_type === 'sub' && isH264Codec(stream.video_codec));
  const anyH264 = available.find((stream) => isH264Codec(stream.video_codec));
  return (primaryH264 || h264Sub || anyH264 || primary || available[0])!.id;
}

export function archiveFrameRate(channel: HikvisionChannel): number {
  return primaryStream(channel)?.frame_rate || 25;
}

export function shouldCopyArchiveVideo(channel: HikvisionChannel): boolean {
  return isH264Codec(primaryStream(channel)?.video_codec);
}

export function shouldCopyArchiveAudio(channel: HikvisionChannel): boolean {
  return normalizedCodec(primaryStream(channel)?.audio_codec).includes('AAC');
}
