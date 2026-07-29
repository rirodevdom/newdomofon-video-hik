import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

process.env.HIK_NODE_TOKEN ||= 'test-control-token-0123456789';
process.env.HIK_NODE_MEDIA_SECRET ||= 'test-media-secret-0123456789';
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
process.env.HIK_NODE_ROOT ||= '/tmp/newdomofon-video-hik-test';

const responses: Record<string, string> = {
  '/ISAPI/System/deviceInfo': '<DeviceInfo><deviceName>NVR</deviceName><model>DS-Test</model><serialNumber>ABC</serialNumber></DeviceInfo>',
  '/ISAPI/System/capabilities': '<DeviceCap><SysCap><isSupportMetadata>true</isSupportMetadata></SysCap></DeviceCap>',
  '/ISAPI/ContentMgmt/InputProxy/channels/status': `
    <InputProxyChannelStatusList>
      <InputProxyChannelStatus><id>1</id><name>Front</name><online>true</online><enabled>true</enabled><streamingProxyChannelId>101</streamingProxyChannelId></InputProxyChannelStatus>
      <InputProxyChannelStatus><id>2</id><name>Back</name><online>false</online><enabled>true</enabled><streamingProxyChannelId>201</streamingProxyChannelId></InputProxyChannelStatus>
    </InputProxyChannelStatusList>`,
  '/ISAPI/ContentMgmt/InputProxy/channels': '<InputProxyChannelList/>',
  '/ISAPI/Streaming/channels': `
    <StreamingChannelList>
      <StreamingChannel><id>101</id><channelName>Front main</channelName><enabled>true</enabled><videoInputChannelID>1</videoInputChannelID><Video><videoCodecType>H.265</videoCodecType><videoResolutionWidth>2560</videoResolutionWidth><videoResolutionHeight>1440</videoResolutionHeight><maxFrameRate>2500</maxFrameRate><maxBitRate>4096</maxBitRate><GovLength>50</GovLength></Video><Audio><audioCompressionType>G.711ulaw</audioCompressionType></Audio></StreamingChannel>
      <StreamingChannel><id>102</id><channelName>Front sub</channelName><enabled>true</enabled><videoInputChannelID>1</videoInputChannelID><Video><videoCodecType>H.264</videoCodecType><videoResolutionWidth>640</videoResolutionWidth><videoResolutionHeight>360</videoResolutionHeight><maxFrameRate>1500</maxFrameRate><maxBitRate>512</maxBitRate></Video></StreamingChannel>
      <StreamingChannel><id>201</id><channelName>Back main</channelName><enabled>true</enabled><videoInputChannelID>2</videoInputChannelID><Video><videoCodecType>H.264</videoCodecType></Video></StreamingChannel>
    </StreamingChannelList>`
};

test('discovers physical channels and stream settings', async (t) => {
  const server = http.createServer((req, res) => {
    const body = responses[req.url || ''];
    if (!body) {
      res.statusCode = 404;
      res.end('not found');
      return;
    }
    res.setHeader('Content-Type', 'application/xml');
    res.end(body);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const address = server.address();
  assert.ok(address && typeof address === 'object');

  const { discoverHikvisionDevice } = await import('../src/isapi/discovery.js');
  const result = await discoverHikvisionDevice({
    id: 'nvr-1',
    name: 'NVR',
    host: '127.0.0.1',
    scheme: 'http',
    isapi_port: address.port,
    rtsp_port: 554,
    username: 'admin',
    password: 'secret',
    archive_storage: 'device',
    retention_days: 30,
    enabled: true,
    reject_unauthorized_tls: true
  });

  assert.equal(result.channels.length, 2);
  assert.equal(result.channels[0]?.id, 'nvr-1:1');
  assert.equal(result.channels[0]?.primary_stream_id, '101');
  assert.equal(result.channels[0]?.streams.length, 2);
  assert.equal(result.channels[0]?.streams[0]?.video_codec, 'H.265');
  assert.equal(result.channels[0]?.streams[0]?.frame_rate, 25);
  assert.equal(result.channels[1]?.online, false);
});
