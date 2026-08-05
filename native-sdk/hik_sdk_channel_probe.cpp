#include <HCNetSDK.h>

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>

namespace {
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

struct Session {
  LONG user{-1};
  NET_DVR_DEVICEINFO_V40 info{};
  Session() {
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
    user = NET_DVR_Login_V40(&login, &info);
    if (user < 0) fail("NET_DVR_Login_V40");
  }
  ~Session() {
    if (user >= 0) NET_DVR_Logout(user);
    NET_DVR_Cleanup();
  }
  [[noreturn]] void fail(const char* operation) const {
    std::cerr << operation << " failed, HCNetSDK error=" << NET_DVR_GetLastError() << "\n";
    std::exit(70);
  }
};

bool union_has_data(const NET_DVR_GET_STREAM_UNION& value) {
  const auto* bytes = reinterpret_cast<const unsigned char*>(&value);
  for (std::size_t i = 0; i < sizeof(value); ++i) if (bytes[i] != 0) return true;
  return false;
}

void emit_channel(bool& first, int physical, int sdkChannel, const char* kind, bool configured, int online) {
  if (!first) std::cout << ',';
  first = false;
  std::cout << "{\"physical_channel\":" << physical
            << ",\"sdk_channel\":" << sdkChannel
            << ",\"kind\":\"" << kind << "\""
            << ",\"configured\":" << (configured ? "true" : "false")
            << ",\"online\":";
  if (online < 0) std::cout << "null";
  else std::cout << (online ? "true" : "false");
  std::cout << '}';
}
} // namespace

int main() {
  Session sdk;
  const auto& v30 = sdk.info.struDeviceV30;
  const int analogCount = static_cast<int>(v30.byChanNum);
  const int analogStart = static_cast<int>(v30.byStartChan);
  const int loginDigitalCount = static_cast<int>(v30.byIPChanNum) + static_cast<int>(v30.byHighDChanNum) * 256;
  const int loginDigitalStart = static_cast<int>(v30.byStartDChan);

  NET_DVR_IPPARACFG_V40 cfg{};
  cfg.dwSize = sizeof(cfg);
  DWORD returned = 0;
  const BOOL gotCfg = NET_DVR_GetDVRConfig(
    sdk.user,
    NET_DVR_GET_IPPARACFG_V40,
    0,
    &cfg,
    sizeof(cfg),
    &returned
  );

  std::cout << "{\"transport\":\"hcnet-private-sdk\",\"config_available\":"
            << (gotCfg ? "true" : "false") << ",\"channels\":[";
  bool first = true;

  if (gotCfg) {
    const int cfgAnalogCount = static_cast<int>(cfg.dwAChanNum);
    for (int index = 0; index < cfgAnalogCount && index < MAX_CHANNUM_V30; ++index) {
      const bool configured = cfg.byAnalogChanEnable[index] == 1;
      if (!configured) continue;
      emit_channel(first, index + 1, analogStart + index, "analog", true, 1);
    }

    const int digitalCount = static_cast<int>(cfg.dwDChanNum);
    const int digitalStart = static_cast<int>(cfg.dwStartDChan);
    for (int index = 0; index < digitalCount && index < MAX_CHANNUM_V30; ++index) {
      const auto& mode = cfg.struStreamMode[index];
      bool configured = false;
      int online = -1;
      if (mode.byGetStreamType == 0) {
        NET_DVR_IPCHANINFO info{};
        std::memcpy(&info, &mode.uGetStream, sizeof(info));
        const int ipId = static_cast<int>(info.byIPIDHigh) * 256 + static_cast<int>(info.byIPID);
        configured = ipId > 0;
        online = info.byEnable == 1 ? 1 : 0;
      } else if (mode.byGetStreamType == 6) {
        NET_DVR_IPCHANINFO_V40 info{};
        std::memcpy(&info, &mode.uGetStream, sizeof(info));
        configured = info.wIPID > 0;
        online = info.byEnable == 1 ? 1 : 0;
      } else {
        configured = union_has_data(mode.uGetStream);
        online = configured ? -1 : 0;
      }
      if (!configured) continue;
      emit_channel(first, analogCount + index + 1, digitalStart + index, "digital", true, online);
    }
  } else {
    // Older devices may not implement NET_DVR_GET_IPPARACFG_V40. Keep the
    // login counters as compatibility inventory rather than failing discovery.
    for (int index = 0; index < analogCount; ++index) {
      emit_channel(first, index + 1, analogStart + index, "analog", true, -1);
    }
    for (int index = 0; index < loginDigitalCount; ++index) {
      emit_channel(first, analogCount + index + 1, loginDigitalStart + index, "digital", true, -1);
    }
  }
  std::cout << "]}" << std::endl;
  return 0;
}
