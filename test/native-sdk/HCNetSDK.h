#pragma once

#include <cstddef>
#include <cstdint>

using BYTE = unsigned char;
using WORD = unsigned short;
using DWORD = unsigned int;
using LONG = long;
using BOOL = int;

#ifndef CALLBACK
#define CALLBACK
#endif

constexpr BOOL TRUE = 1;
constexpr BOOL FALSE = 0;
constexpr DWORD NET_SDK_INIT_CFG_SDK_PATH = 2;
constexpr LONG COMM_ALARM_V30 = 0x4000;
constexpr int MAX_CHANNUM_V30 = 64;
constexpr DWORD NET_DVR_SYSHEAD = 1;
constexpr DWORD NET_DVR_STREAMDATA = 2;
constexpr DWORD NET_DVR_PLAYSTART = 1;

struct NET_DVR_TIME {
  DWORD dwYear{};
  DWORD dwMonth{};
  DWORD dwDay{};
  DWORD dwHour{};
  DWORD dwMinute{};
  DWORD dwSecond{};
};

struct NET_DVR_ALARMER {};

struct NET_DVR_ALARMINFO_V30 {
  DWORD dwAlarmType{};
  BYTE byChannel[MAX_CHANNUM_V30]{};
};

struct NET_DVR_LOCAL_SDK_PATH {
  char sPath[256]{};
};

struct NET_DVR_USER_LOGIN_INFO {
  char sDeviceAddress[129]{};
  char sUserName[64]{};
  char sPassword[64]{};
  WORD wPort{};
  BOOL bUseAsynLogin{};
  BYTE byLoginMode{};
  BYTE byUseUTCTime{};
};

struct NET_DVR_DEVICEINFO_V40 {};

struct NET_DVR_SETUPALARM_PARAM {
  DWORD dwSize{};
  BYTE byLevel{};
  BYTE byAlarmInfoType{};
  BYTE byRetAlarmTypeV40{};
};

struct NET_DVR_PREVIEWINFO {
  LONG lChannel{};
  DWORD dwStreamType{};
  DWORD dwLinkMode{};
  void* hPlayWnd{};
  BOOL bBlocked{};
};

using MSGCallBack = void (CALLBACK *)(LONG, NET_DVR_ALARMER*, char*, DWORD, void*);
using RealDataCallBack = void (CALLBACK *)(LONG, DWORD, BYTE*, DWORD, void*);

inline BOOL NET_DVR_SetSDKInitCfg(DWORD, void*) { return TRUE; }
inline BOOL NET_DVR_Init() { return TRUE; }
inline BOOL NET_DVR_SetConnectTime(DWORD, DWORD) { return TRUE; }
inline BOOL NET_DVR_SetReconnect(DWORD, BOOL) { return TRUE; }
inline LONG NET_DVR_Login_V40(NET_DVR_USER_LOGIN_INFO*, NET_DVR_DEVICEINFO_V40*) { return 1; }
inline BOOL NET_DVR_Logout(LONG) { return TRUE; }
inline BOOL NET_DVR_Cleanup() { return TRUE; }
inline DWORD NET_DVR_GetLastError() { return 0; }
inline BOOL NET_DVR_SetDVRMessageCallBack_V50(LONG, MSGCallBack, void*) { return TRUE; }
inline LONG NET_DVR_SetupAlarmChan_V41(LONG, NET_DVR_SETUPALARM_PARAM*) { return 1; }
inline BOOL NET_DVR_CloseAlarmChan_V30(LONG) { return TRUE; }
inline LONG NET_DVR_RealPlay_V40(LONG, NET_DVR_PREVIEWINFO*, RealDataCallBack, void*) { return 1; }
inline BOOL NET_DVR_StopRealPlay(LONG) { return TRUE; }
inline LONG NET_DVR_PlayBackByTime(LONG, LONG, NET_DVR_TIME*, NET_DVR_TIME*, void*) { return 1; }
inline BOOL NET_DVR_SetPlayDataCallBack_V40(LONG, RealDataCallBack, void*) { return TRUE; }
inline BOOL NET_DVR_PlayBackControl_V40(LONG, DWORD, void*, DWORD, void*, DWORD*) { return TRUE; }
inline BOOL NET_DVR_StopPlayBack(LONG) { return TRUE; }
