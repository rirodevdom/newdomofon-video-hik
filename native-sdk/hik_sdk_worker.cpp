#include <HCNetSDK.h>

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
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

std::string json_escape(const std::string& input) {
  std::ostringstream out;
  for (unsigned char ch : input) {
    switch (ch) {
      case '\\': out << "\\\\"; break;
      case '"': out << "\\\""; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (ch < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(ch) << std::dec;
        } else {
          out << static_cast<char>(ch);
        }
    }
  }
  return out.str();
}

std::string c_string(const BYTE* value, std::size_t size) {
  std::size_t len = 0;
  while (len < size && value[len] != 0) ++len;
  return std::string(reinterpret_cast<const char*>(value), len);
}

NET_DVR_TIME parse_iso_utc(const std::string& raw) {
  std::tm tm{};
  std::istringstream input(raw);
  input >> std::get_time(&tm, "%Y-%m-%dT%H:%M:%S");
  if (input.fail()) {
    std::cerr << "invalid ISO time: " << raw << "\n";
    std::exit(64);
  }
  NET_DVR_TIME out{};
  out.dwYear = tm.tm_year + 1900;
  out.dwMonth = tm.tm_mon + 1;
  out.dwDay = tm.tm_mday;
  out.dwHour = tm.tm_hour;
  out.dwMinute = tm.tm_min;
  out.dwSecond = tm.tm_sec;
  return out;
}

std::string iso_utc(const NET_DVR_TIME& value) {
  std::ostringstream out;
  out << std::setfill('0')
      << std::setw(4) << value.dwYear << '-'
      << std::setw(2) << value.dwMonth << '-'
      << std::setw(2) << value.dwDay << 'T'
      << std::setw(2) << value.dwHour << ':'
      << std::setw(2) << value.dwMinute << ':'
      << std::setw(2) << value.dwSecond << 'Z';
  return out.str();
}

bool write_all_stdout(const BYTE* data, DWORD size) {
  std::size_t offset = 0;
  while (offset < size && !g_stop.load()) {
    const ssize_t written = ::write(STDOUT_FILENO, data + offset, size - offset);
    if (written < 0) {
      if (errno == EINTR) continue;
      return false;
    }
    offset += static_cast<std::size_t>(written);
  }
  return offset == size;
}

void CALLBACK stream_callback(LONG, DWORD dataType, BYTE* buffer, DWORD size, void*) {
  if (!buffer || !size) return;
  if (dataType == NET_DVR_SYSHEAD || dataType == NET_DVR_STREAMDATA) {
    if (!write_all_stdout(buffer, size)) g_stop = true;
  }
}

void CALLBACK alarm_callback(LONG command, NET_DVR_ALARMER*, char*, DWORD size, void*) {
  std::cout << "{\"command\":" << command << ",\"size\":" << size << "}" << std::endl;
}

class SdkSession {
 public:
  SdkSession() {
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
    login.byLoginMode = 0; // Hikvision private Device Network SDK protocol, never ISAPI.
    login.byUseUTCTime = 1;

    userId_ = NET_DVR_Login_V40(&login, &info);
    if (userId_ < 0) fail("NET_DVR_Login_V40");
    info_ = info;
  }

  ~SdkSession() {
    if (userId_ >= 0) NET_DVR_Logout(userId_);
    NET_DVR_Cleanup();
  }

  LONG user_id() const { return userId_; }
  const NET_DVR_DEVICEINFO_V40& info() const { return info_; }

  [[noreturn]] void fail(const char* operation) const {
    const DWORD code = NET_DVR_GetLastError();
    std::cerr << operation << " failed, HCNetSDK error=" << code << "\n";
    std::exit(70);
  }

 private:
  LONG userId_{-1};
  NET_DVR_DEVICEINFO_V40 info_{};
};

int logical_stream_type() {
  const int value = env_int("HIK_SDK_STREAM_TYPE", 0);
  return value < 0 ? 0 : value;
}

void mode_probe(SdkSession& sdk) {
  const auto& v30 = sdk.info().struDeviceV30;
  const int digitalCount = static_cast<int>(v30.byIPChanNum) + static_cast<int>(v30.byHighDChanNum) * 256;
  std::cout
      << "{\"ok\":true"
      << ",\"transport\":\"hcnet-private-sdk\""
      << ",\"serial\":\"" << json_escape(c_string(v30.sSerialNumber, sizeof(v30.sSerialNumber))) << "\""
      << ",\"analog_start\":" << static_cast<int>(v30.byStartChan)
      << ",\"analog_count\":" << static_cast<int>(v30.byChanNum)
      << ",\"digital_start\":" << static_cast<int>(v30.byStartDChan)
      << ",\"digital_count\":" << digitalCount
      << ",\"main_proto\":" << static_cast<int>(v30.byMainProto)
      << ",\"sub_proto\":" << static_cast<int>(v30.bySubProto)
      << "}" << std::endl;
}

void mode_ranges(SdkSession& sdk) {
  NET_DVR_FILECOND_V40 cond{};
  cond.lChannel = env_int("HIK_SDK_CHANNEL", 1);
  cond.dwFileType = 0xff;
  cond.dwIsLocked = 0xff;
  cond.struStartTime = parse_iso_utc(env_required("HIK_SDK_START"));
  cond.struStopTime = parse_iso_utc(env_required("HIK_SDK_END"));
  cond.byQuickSearch = 0;
  cond.byStreamType = static_cast<BYTE>(logical_stream_type());

  LONG handle = NET_DVR_FindFile_V40(sdk.user_id(), &cond);
  if (handle < 0) sdk.fail("NET_DVR_FindFile_V40");

  std::vector<std::string> rows;
  for (;;) {
    NET_DVR_FINDDATA_V40 item{};
    const LONG status = NET_DVR_FindNextFile_V40(handle, &item);
    if (status == NET_DVR_FILE_SUCCESS) {
      std::ostringstream row;
      row << "{\"start\":\"" << iso_utc(item.struStartTime)
          << "\",\"end\":\"" << iso_utc(item.struStopTime)
          << "\",\"file_type\":" << static_cast<int>(item.byFileType)
          << ",\"stream_type\":" << static_cast<int>(item.byStreamType)
          << ",\"file_index\":" << item.dwFileIndex
          << "}";
      rows.push_back(row.str());
      continue;
    }
    if (status == NET_DVR_ISFINDING) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      continue;
    }
    if (status == NET_DVR_FILE_NOFIND || status == NET_DVR_NOMOREFILE) break;
    NET_DVR_FindClose(handle);
    sdk.fail("NET_DVR_FindNextFile_V40");
  }
  NET_DVR_FindClose(handle);

  std::cout << "{\"items\":[";
  for (std::size_t i = 0; i < rows.size(); ++i) {
    if (i) std::cout << ',';
    std::cout << rows[i];
  }
  std::cout << "]}" << std::endl;
}

void mode_live(SdkSession& sdk) {
  NET_DVR_PREVIEWINFO preview{};
  preview.lChannel = env_int("HIK_SDK_CHANNEL", 1);
  preview.dwStreamType = logical_stream_type();
  preview.dwLinkMode = 0; // SDK private TCP stream where device supports it; no RTSP URL is used.
  preview.hPlayWnd = 0;
  preview.bBlocked = TRUE;

  LONG handle = NET_DVR_RealPlay_V40(sdk.user_id(), &preview, stream_callback, nullptr);
  if (handle < 0) sdk.fail("NET_DVR_RealPlay_V40");
  while (!g_stop.load()) std::this_thread::sleep_for(std::chrono::milliseconds(200));
  NET_DVR_StopRealPlay(handle);
}

void mode_playback(SdkSession& sdk) {
  NET_DVR_TIME start = parse_iso_utc(env_required("HIK_SDK_START"));
  NET_DVR_TIME end = parse_iso_utc(env_required("HIK_SDK_END"));
  const LONG channel = env_int("HIK_SDK_CHANNEL", 1);

  LONG handle = NET_DVR_PlayBackByTime(sdk.user_id(), channel, &start, &end, 0);
  if (handle < 0) sdk.fail("NET_DVR_PlayBackByTime");
  if (!NET_DVR_SetPlayDataCallBack_V40(handle, stream_callback, nullptr)) {
    NET_DVR_StopPlayBack(handle);
    sdk.fail("NET_DVR_SetPlayDataCallBack_V40");
  }
  DWORD outLen = 0;
  if (!NET_DVR_PlayBackControl_V40(handle, NET_DVR_PLAYSTART, nullptr, 0, nullptr, &outLen)) {
    NET_DVR_StopPlayBack(handle);
    sdk.fail("NET_DVR_PLAYSTART");
  }

  std::string line;
  while (!g_stop.load()) {
    if (std::cin.rdbuf()->in_avail() > 0 && std::getline(std::cin, line)) {
      if (line == "stop") break;
      if (line.rfind("seek ", 0) == 0) {
        NET_DVR_TIME target = parse_iso_utc(line.substr(5));
        DWORD ignored = 0;
        if (!NET_DVR_PlayBackControl_V40(handle, NET_DVR_PLAYSETTIME, &target, sizeof(target), nullptr, &ignored)) {
          std::cerr << "NET_DVR_PLAYSETTIME failed, HCNetSDK error=" << NET_DVR_GetLastError() << "\n";
        }
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  NET_DVR_StopPlayBack(handle);
}

void mode_events(SdkSession& sdk) {
  if (!NET_DVR_SetDVRMessageCallBack_V50(0, alarm_callback, nullptr)) sdk.fail("NET_DVR_SetDVRMessageCallBack_V50");
  NET_DVR_SETUPALARM_PARAM setup{};
  setup.dwSize = sizeof(setup);
  setup.byLevel = 1;
  setup.byAlarmInfoType = 1;
  LONG alarm = NET_DVR_SetupAlarmChan_V41(sdk.user_id(), &setup);
  if (alarm < 0) sdk.fail("NET_DVR_SetupAlarmChan_V41");
  while (!g_stop.load()) std::this_thread::sleep_for(std::chrono::milliseconds(250));
  NET_DVR_CloseAlarmChan_V30(alarm);
}
} // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: hik-sdk-worker <probe|ranges|live|playback|events>\n";
    return 64;
  }
  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);
  std::signal(SIGPIPE, SIG_IGN);

  SdkSession sdk;
  const std::string mode = argv[1];
  if (mode == "probe") mode_probe(sdk);
  else if (mode == "ranges") mode_ranges(sdk);
  else if (mode == "live") mode_live(sdk);
  else if (mode == "playback") mode_playback(sdk);
  else if (mode == "events") mode_events(sdk);
  else {
    std::cerr << "unknown mode: " << mode << "\n";
    return 64;
  }
  return 0;
}
