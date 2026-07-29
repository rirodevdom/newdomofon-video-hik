import https from 'node:https';
import http from 'node:http';
import { config } from '../config.js';
import type { HikvisionDeviceConfig } from '../types.js';
import { digestAuthorization, parseDigestChallenge } from './digest.js';

export interface IsapiResponse {
  status: number;
  headers: http.IncomingHttpHeaders;
  body: string;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: string;
  accept?: string;
  contentType?: string;
}

function requestOnce(
  device: HikvisionDeviceConfig,
  path: string,
  options: RequestOptions,
  authorization?: string
): Promise<IsapiResponse> {
  const method = options.method || 'GET';
  const protocol = device.scheme === 'https' ? https : http;
  const body = options.body || '';
  const headers: Record<string, string> = {
    accept: options.accept || 'application/xml,text/xml,application/json,*/*',
    'user-agent': 'NewDomofon-Hikvision-Node/0.1'
  };
  if (body) {
    headers['content-type'] = options.contentType || 'application/xml; charset=UTF-8';
    headers['content-length'] = String(Buffer.byteLength(body));
  }
  if (authorization) headers.authorization = authorization;

  return new Promise((resolve, reject) => {
    const req = protocol.request({
      protocol: `${device.scheme}:`,
      hostname: device.host,
      port: device.isapi_port,
      method,
      path,
      headers,
      timeout: config.requestTimeoutMs,
      ...(device.scheme === 'https' ? { rejectUnauthorized: device.reject_unauthorized_tls } : {})
    }, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (chunk) => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
      res.on('end', () => resolve({
        status: res.statusCode || 0,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8')
      }));
    });
    req.on('timeout', () => req.destroy(new Error(`ISAPI request timeout after ${config.requestTimeoutMs} ms`)));
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

function basicAuthorization(device: HikvisionDeviceConfig): string {
  return `Basic ${Buffer.from(`${device.username}:${device.password}`).toString('base64')}`;
}

export class IsapiClient {
  constructor(private readonly device: HikvisionDeviceConfig) {}

  async request(path: string, options: RequestOptions = {}): Promise<IsapiResponse> {
    const method = options.method || 'GET';
    let response = await requestOnce(this.device, path, options, basicAuthorization(this.device));
    if (response.status === 401) {
      const challenge = String(response.headers['www-authenticate'] || '');
      if (/^Digest\s/i.test(challenge)) {
        const authorization = digestAuthorization(
          parseDigestChallenge(challenge),
          method,
          path,
          this.device.username,
          this.device.password
        );
        response = await requestOnce(this.device, path, options, authorization);
      }
    }
    if (response.status < 200 || response.status >= 300) {
      const message = response.body.replace(/\s+/g, ' ').slice(0, 500);
      throw new Error(`ISAPI ${method} ${path} returned HTTP ${response.status}: ${message}`);
    }
    return response;
  }

  async get(path: string): Promise<string> {
    return (await this.request(path)).body;
  }

  async post(path: string, body: string): Promise<string> {
    return (await this.request(path, { method: 'POST', body })).body;
  }
}
