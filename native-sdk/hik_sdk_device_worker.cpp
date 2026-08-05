#include <HCNetSDK.h>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <poll.h>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <sys/stat.h>
#include <unistd.h>

namespace {
std::atomic<bool> g_stop{false};

void on_signal(int) { g_stop = true; }

std::string env_required(const char* name) {
  const char* value = std::getenv(name);
  if (!value || !*value) {
    std::cerr << "missing required environment variable: " << name << "\n";
    std::exit(64);
  }
  return value;
}

int env_int(const char* name, int fallback) {
  const char* value = std::getenv(name);
  if (!value || !*value) return fallback;
  try { return std::stoi(value); } catch (...) { return fallback; }
}

bool parse_iso_utc(const std::string& raw, NET_DVR_TIME& out) {
  std::tm tm{};
  std::istringstream input(raw);
  input >> std::get_time(&tm, "%Y-%m-%dT%H:%M:%S");
  if (input.fail()) return false;
  out = {};
  out.dwYear = tm.tm_year + 1900;
  out.dwMonth = tm.tm_mon + 1;
  out.dwDay = tm.tm_mday;
  out.dwHour = tm.tm_hour;
  out.dwMinute = tm.tm_min;
  out.dwSecond = tm.tm_sec;
  return true;
}

std::string now_iso_utc() {
  const auto now = std::chrono::system_clock::now();
  const std::time_t value = std::chrono::system_clock::to_time_t(now);
  std::tm tm{};
  gmtime_r(&value, &tm);
  std::ostringstream out;
  out << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
  return out.str();
}

const char* alarm_type_name(DWORD type) {
  switch (type) {
    case 0: return "io_alarm";
    case 1: return "disk_full";
    case 2: return "video_loss";
    case 3: return "motion";
    case 4: return "disk_unformatted";
    case 5: return "disk_error";
    case 6: return "tamper";
    case 7: return "video_standard_mismatch";
    case 8: return "illegal_access";
    case 9: return "video_signal_abnormal";
    case 10: return "recording_exception";
    case 11: return "scene_change";
    case 12: return "array_exception";
    case 13: return "resolution_mismatch";
    case 15: return "smart_detection";
    case 16: return "poe_exception";
    case 19: return "audio_loss";
    default: return "hikvision_alarm";
  }
}

void emit_alarm_json(LONG command, const char* eventType, int physicalChannel, DWORD alarmType, DWORD eventCode = 0) {
  std::cout
      << "{\"command\":" << command
      << ",\"event_type\":\"" << eventType << "\""
      << ",\"event_state\":\"active\""
      << ",\"physical_channel\":" << physicalChannel
      << ",\"alarm_type\":" << alarmType
      << ",\"event_code\":" << eventCode
      << ",\"occurred_at\":\"" << now_iso_utc() << "\"}"
      << std::endl;
}

void CALLBACK alarm_callback(LONG command, NET_DVR_ALARMER*, char* alarmInfo, DWORD size, void*) {
  if (!alarmInfo || !size) return;
  if (command == COMM_ALARM_V30 && size >= sizeof(NET_DVR_ALARMINFO_V30)) {
    const auto* info = reinterpret_cast<const NET_DVR_ALARMINFO_V30*>(alarmInfo);
    bool emitted = false;
    for (int index = 0; index < MAX_CHANNUM_V30; ++index) {
      if (info->byChannel[index] != 1) continue;
      emit_alarm_json(command, alarm_type_name(info->dwAlarmType), index + 1, info->dwAlarmType);
      emitted = true;
    }
    if (!emitted && info->dwAlarmType == 0) emit_alarm_json(command, "io_alarm", 0, info->dwAlarmType);
    return;
  }
#ifdef COMM_ALARM_RULE
  if (command == COMM_ALARM_RULE && size >= sizeof(NET_VCA_RULE_ALARM)) {
    const auto* info = reinterpret_cast<const NET_VCA_RULE_ALARM*>(alarmInfo);
    int channel = static_cast<int>(info->struDevInfo.byIvmsChannel);
    if (channel <= 0) channel = static_cast<int>(info->struDevInfo.byChannel);
    emit_alarm_json(command, "vca_rule", channel, 0, info->struRuleInfo.dwEventType);
    return;
  }
#endif
}

struct StreamOutput {
  int fd{-1};
  std::string label;
};

struct LiveSink {
  int physicalChannel{0};
  int sdkChannel{0};
  int streamType{0};
  std::string fifoPath;
  StreamOutput output;
  LONG handle{-1};
};

struct PlaybackSink {
  std::string sessionId;
  int sdkChannel{0};
  std::string fifoPath;
  StreamOutput output;
  LONG handle{-1};
};

bool write_all_fd(int fd, const BYTE* data, DWORD size) {
  std::size_t offset = 0;
  while (offset < size && !g_stop.load()) {
    const ssize_t written = ::write(fd, data + offset, size - offset);
    if (written < 0) {
      if (errno == EINTR) continue;
      if (errno == EPIPE) return false;
      return false;
    }
    offset += static_cast<std::size_t>(written);
  }
  return offset == size;
}

void CALLBACK grouped_stream_callback(LONG, DWORD dataType, BYTE* buffer, DWORD size, void* user) {
  auto* output = static_cast<StreamOutput*>(user);
  if (!output || output->fd < 0 || !buffer || !size) return;
  if (dataType != NET_DVR_SYSHEAD && dataType != NET_DVR_STREAMDATA) return;
  if (!write_all_fd(output->fd, buffer, size)) {
    std::cerr << "stream fifo write failed " << output->label << " errno=" << errno << "\n";
  }
}

class SdkDevice {
 public:
  SdkDevice() {
    const char* libDir = std::getenv("HIK_SDK_LIB_DIR");
    if (libDir && *libDir) {
      NET_DVR_LOCAL_SDK_PATH sdkPath{};
      std::strncpy(sdkPath.sPath, libDir, sizeof(sdkPath.sPath) - 1);
      NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, &sdkPath);
    }
    if (!NET_DVR_Init()) fail("NET_DVR_Init");
    NET_DVR_SetConnectTime(3000, 1);
    NET_DVR_SetReconnect(5000, TRUE);

    NET_DVR_USER_LOGIN_INFO login{};
    NET_DVR_DEVICEINFO_V40 info{};
    const std::string host = env_required("HIK_SDK_HOST");
    const std::string username = env_required("HIK_SDK_USERNAME");
    const std::string password = env_required("HIK_SDK_PASSWORD");
    std::strncpy(login.sDeviceAddress, host.c_str(), sizeof(login.sDeviceAddress) - 1);
    std::strncpy(login.sUserName, username.c_str(), sizeof(login.sUserName) - 1);
    std::strncpy(login.sPassword, password.c_str(), sizeof(login.sPassword) - 1);
    login.wPort = static_cast<WORD>(env_int("HIK_SDK_PORT", 8000));
    login.bUseAsynLogin = FALSE;
    login.byLoginMode = 0;
    login.byUseUTCTime = 1;
    userId_ = NET_DVR_Login_V40(&login, &info);
    if (userId_ < 0) fail("NET_DVR_Login_V40");
  }

  ~SdkDevice() {
    if (userId_ >= 0) NET_DVR_Logout(userId_);
    NET_DVR_Cleanup();
  }

  LONG user_id() const { return userId_; }

  [[noreturn]] void fail(const char* operation) const {
    std::cerr << operation << " failed, HCNetSDK error=" << NET_DVR_GetLastError() << "\n";
    std::exit(70);
  }

 private:
  LONG userId_{-1};
};

std::vector<std::unique_ptr<LiveSink>> load_live_config(const std::string& path) {
  std::ifstream input(path);
  if (!input) {
    std::cerr << "cannot open HIK_SDK_DEVICE_LIVE_CONFIG: " << path << "\n";
    std::exit(64);
  }
  std::vector<std::unique_ptr<LiveSink>> result;
  std::string line;
  while (std::getline(input, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream row(line);
    std::string physicalRaw, sdkRaw, streamRaw, fifo;
    if (!std::getline(row, physicalRaw, '\t') || !std::getline(row, sdkRaw, '\t') || !std::getline(row, streamRaw, '\t') || !std::getline(row, fifo)) {
      std::cerr << "invalid live config row: " << line << "\n";
      std::exit(64);
    }
    auto sink = std::make_unique<LiveSink>();
    sink->physicalChannel = std::stoi(physicalRaw);
    sink->sdkChannel = std::stoi(sdkRaw);
    sink->streamType = std::stoi(streamRaw);
    sink->fifoPath = fifo;
    sink->output.label = "physical=" + physicalRaw + " sdk=" + sdkRaw;
    result.push_back(std::move(sink));
  }
  return result;
}

bool ensure_fifo(const std::string& fifoPath, StreamOutput& output, const std::string& label) {
  struct stat st{};
  if (::lstat(fifoPath.c_str(), &st) == 0) {
    if (!S_ISFIFO(st.st_mode)) {
      std::cerr << "stream path exists but is not fifo: " << fifoPath << "\n";
      return false;
    }
  } else if (errno == ENOENT) {
    if (::mkfifo(fifoPath.c_str(), 0600) != 0) {
      std::cerr << "mkfifo failed: " << fifoPath << " errno=" << errno << "\n";
      return false;
    }
  } else {
    std::cerr << "lstat failed: " << fifoPath << " errno=" << errno << "\n";
    return false;
  }
  output.fd = ::open(fifoPath.c_str(), O_RDWR | O_CLOEXEC);
  output.label = label;
  if (output.fd < 0) {
    std::cerr << "open fifo failed: " << fifoPath << " errno=" << errno << "\n";
    return false;
  }
  return true;
}

void stop_playback(std::map<std::string, std::unique_ptr<PlaybackSink>>& playbacks, const std::string& sessionId) {
  auto it = playbacks.find(sessionId);
  if (it == playbacks.end()) return;
  auto& sink = *it->second;
  if (sink.handle >= 0) NET_DVR_StopPlayBack(sink.handle);
  if (sink.output.fd >= 0) ::close(sink.output.fd);
  std::cerr << "HCNetSDK grouped playback stopped session=" << sessionId << "\n";
  playbacks.erase(it);
}

bool start_playback(
  SdkDevice& sdk,
  std::map<std::string, std::unique_ptr<PlaybackSink>>& playbacks,
  const std::string& sessionId,
  int sdkChannel,
  const std::string& startRaw,
  const std::string& endRaw,
  const std::string& fifoPath
) {
  stop_playback(playbacks, sessionId);
  NET_DVR_TIME start{};
  NET_DVR_TIME end{};
  if (!parse_iso_utc(startRaw, start) || !parse_iso_utc(endRaw, end)) {
    std::cerr << "HCNetSDK grouped playback failed session=" << sessionId << " invalid_time\n";
    return false;
  }
  auto sink = std::make_unique<PlaybackSink>();
  sink->sessionId = sessionId;
  sink->sdkChannel = sdkChannel;
  sink->fifoPath = fifoPath;
  if (!ensure_fifo(fifoPath, sink->output, "playback session=" + sessionId + " sdk=" + std::to_string(sdkChannel))) return false;

  sink->handle = NET_DVR_PlayBackByTime(sdk.user_id(), sdkChannel, &start, &end, 0);
  if (sink->handle < 0) {
    std::cerr << "HCNetSDK grouped playback failed session=" << sessionId
              << " stage=NET_DVR_PlayBackByTime error=" << NET_DVR_GetLastError() << "\n";
    ::close(sink->output.fd);
    return false;
  }
  if (!NET_DVR_SetPlayDataCallBack_V40(sink->handle, grouped_stream_callback, &sink->output)) {
    const DWORD error = NET_DVR_GetLastError();
    NET_DVR_StopPlayBack(sink->handle);
    ::close(sink->output.fd);
    std::cerr << "HCNetSDK grouped playback failed session=" << sessionId
              << " stage=NET_DVR_SetPlayDataCallBack_V40 error=" << error << "\n";
    return false;
  }
  DWORD outLen = 0;
  if (!NET_DVR_PlayBackControl_V40(sink->handle, NET_DVR_PLAYSTART, nullptr, 0, nullptr, &outLen)) {
    const DWORD error = NET_DVR_GetLastError();
    NET_DVR_StopPlayBack(sink->handle);
    ::close(sink->output.fd);
    std::cerr << "HCNetSDK grouped playback failed session=" << sessionId
              << " stage=NET_DVR_PLAYSTART error=" << error << "\n";
    return false;
  }
  std::cerr << "HCNetSDK grouped playback started session=" << sessionId
            << " sdk=" << sdkChannel << " start=" << startRaw << " end=" << endRaw << "\n";
  playbacks.emplace(sessionId, std::move(sink));
  return true;
}

std::vector<std::string> split_tabs(const std::string& line) {
  std::vector<std::string> fields;
  std::istringstream input(line);
  std::string field;
  while (std::getline(input, field, '\t')) fields.push_back(field);
  return fields;
}

} // namespace

int main() {
  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);
  std::signal(SIGPIPE, SIG_IGN);

  SdkDevice sdk;
  auto sinks = load_live_config(env_required("HIK_SDK_DEVICE_LIVE_CONFIG"));
  std::map<std::string, std::unique_ptr<PlaybackSink>> playbacks;

  if (!NET_DVR_SetDVRMessageCallBack_V50(0, alarm_callback, nullptr)) {
    std::cerr << "NET_DVR_SetDVRMessageCallBack_V50 failed, HCNetSDK error=" << NET_DVR_GetLastError() << "\n";
  }
  NET_DVR_SETUPALARM_PARAM alarmParam{};
  alarmParam.dwSize = sizeof(alarmParam);
  alarmParam.byLevel = 1;
  alarmParam.byAlarmInfoType = 1;
  alarmParam.byRetAlarmTypeV40 = 0;
  LONG alarmHandle = NET_DVR_SetupAlarmChan_V41(sdk.user_id(), &alarmParam);
  if (alarmHandle < 0) {
    std::cerr << "NET_DVR_SetupAlarmChan_V41 failed, HCNetSDK error=" << NET_DVR_GetLastError() << "\n";
  }

  int started = 0;
  for (auto& sink : sinks) {
    if (!ensure_fifo(sink->fifoPath, sink->output, sink->output.label)) continue;
    NET_DVR_PREVIEWINFO preview{};
    preview.lChannel = sink->sdkChannel;
    preview.dwStreamType = sink->streamType;
    preview.dwLinkMode = 0;
    preview.hPlayWnd = 0;
    preview.bBlocked = TRUE;
    sink->handle = NET_DVR_RealPlay_V40(sdk.user_id(), &preview, grouped_stream_callback, &sink->output);
    if (sink->handle < 0) {
      std::cerr << "NET_DVR_RealPlay_V40 failed physical=" << sink->physicalChannel
                << " sdk=" << sink->sdkChannel << " HCNetSDK error=" << NET_DVR_GetLastError() << "\n";
      continue;
    }
    ++started;
    std::cerr << "HCNetSDK grouped live started physical=" << sink->physicalChannel
              << " sdk=" << sink->sdkChannel << " stream=" << sink->streamType << "\n";
  }

  if (started == 0 && !sinks.empty()) {
    std::cerr << "no grouped live handles could be started\n";
    return 70;
  }

  while (!g_stop.load()) {
    pollfd input{};
    input.fd = STDIN_FILENO;
    input.events = POLLIN;
    const int ready = ::poll(&input, 1, 250);
    if (ready < 0) {
      if (errno == EINTR) continue;
      std::cerr << "grouped command poll failed errno=" << errno << "\n";
      break;
    }
    if (ready == 0 || !(input.revents & POLLIN)) continue;
    std::string line;
    if (!std::getline(std::cin, line)) continue;
    const auto fields = split_tabs(line);
    if (fields.empty()) continue;
    if (fields[0] == "PLAYBACK" && fields.size() == 6) {
      int sdkChannel = 0;
      try { sdkChannel = std::stoi(fields[2]); } catch (...) { sdkChannel = 0; }
      if (sdkChannel <= 0) {
        std::cerr << "invalid grouped playback sdk channel session=" << fields[1] << "\n";
        continue;
      }
      start_playback(sdk, playbacks, fields[1], sdkChannel, fields[3], fields[4], fields[5]);
      continue;
    }
    if (fields[0] == "STOP_PLAYBACK" && fields.size() == 2) {
      stop_playback(playbacks, fields[1]);
      continue;
    }
    std::cerr << "unknown grouped command: " << line << "\n";
  }

  for (auto it = playbacks.begin(); it != playbacks.end();) {
    const std::string sessionId = it->first;
    ++it;
    stop_playback(playbacks, sessionId);
  }
  for (auto& sink : sinks) {
    if (sink->handle >= 0) NET_DVR_StopRealPlay(sink->handle);
    if (sink->output.fd >= 0) ::close(sink->output.fd);
  }
  if (alarmHandle >= 0) NET_DVR_CloseAlarmChan_V30(alarmHandle);
  return 0;
}
