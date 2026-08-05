import { EventEmitter } from 'node:events';

export interface NativeRuntimeAlarm {
  command?: number;
  event_type?: string;
  event_state?: string;
  physical_channel?: number;
  alarm_type?: number;
  event_code?: number;
  occurred_at?: string;
}

const bus = new EventEmitter();
bus.setMaxListeners(64);

export function emitNativeRuntimeAlarm(deviceId: string, alarm: NativeRuntimeAlarm): void {
  bus.emit('alarm', deviceId, alarm);
}

export function onNativeRuntimeAlarm(listener: (deviceId: string, alarm: NativeRuntimeAlarm) => void): () => void {
  bus.on('alarm', listener);
  return () => bus.off('alarm', listener);
}
