#!/usr/bin/env python3
"""Execute the production CUPnPPlayer open/close methods against a recording renderer.

Extracts WaitOnEvent, PlayFile, OpenFile, CloseFile and the destructor from
xbmc/network/upnp/UPnPPlayer.cpp and splices them into a C++ prelude with stub
Platinum/Neptune types. The fake renderer records every command in order and
answers each command inside the next busy-dialog wait. A hook runs inside that
wait, as the GUI/application message pump does, and closes the player or starts a
newer player on the same renderer. The Platinum stack, threads and the real GUI
are not simulated. Requires Python 3 and g++; runs with ASan/UBSan.
"""
import argparse
import os
from pathlib import Path
import re
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
function = runpy.run_path(str(ROOT / 'tools/test-render-slot-publication.py'))['function']

PRELUDE = r'''
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>
using namespace std::chrono_literals;

std::string g_case;
#define CHECK(c) do { if (!(c)) { \
  std::fprintf(stderr, "Assertion failed: [%s] %s (line %d)\n", g_case.c_str(), #c, __LINE__); \
  std::abort(); } } while (0)

typedef int NPT_Result;
typedef unsigned NPT_UInt32;
typedef unsigned NPT_Cardinal;
#define NPT_SUCCESS 0
#define NPT_FAILURE (-1)
#define NPT_FAILED(r) ((r) != NPT_SUCCESS)
#define NPT_SUCCEEDED(r) ((r) == NPT_SUCCESS)
#define NPT_CHECK_LABEL(x, l) do { NPT_Result r_ = (x); if (r_ != NPT_SUCCESS) goto l; } while (0)
#define NPT_CHECK_LABEL_SEVERE(x, l) NPT_CHECK_LABEL(x, l)
#define NPT_CHECK_POINTER_LABEL_SEVERE(p, l) do { if (!(p)) goto l; } while (0)
#define NPT_SET_LOCAL_LOGGER(x)

struct NPT_String : std::string {
  NPT_String() = default;
  NPT_String(const char* s) : std::string(s) {}
  void Insert(const char* s, unsigned pos) { insert(pos, s); }
  void Append(const char* s) { append(s); }
  int Compare(const char* s) const { return compare(s); }
  void Replace(const char*, const char*) {}
  explicit operator const char*() const { return c_str(); }
};
template<class T> struct NPT_Reference {
  std::shared_ptr<T> p;
  NPT_Reference() = default;
  explicit NPT_Reference(T* t) : p(t) {}
  bool IsNull() const { return !p; }
  T& operator*() const { return *p; }
  T* operator->() const { return p.get(); }
};
struct PLT_ProtocolInfo {
  NPT_String s;
  PLT_ProtocolInfo() = default;
  PLT_ProtocolInfo(const NPT_String& v) : s(v) {}
  NPT_String GetContentType() const { return s; }
  NPT_String ToString() const { return s; }
};
struct PLT_MediaItemResource { NPT_String m_Uri; PLT_ProtocolInfo m_ProtocolInfo; };
struct ResourceList {
  std::vector<PLT_MediaItemResource> v;
  NPT_Cardinal GetItemCount() const { return v.size(); }
  PLT_MediaItemResource& operator[](size_t i) { return v[i]; }
};
struct PLT_MediaObject { ResourceList m_Resources; };
struct PLT_TransportInfo { NPT_String cur_transport_state, cur_transport_status, cur_speed; };
struct PLT_Didl {
  static NPT_Result ToDidl(PLT_MediaObject&, const char*, NPT_String&) { return NPT_SUCCESS; }
  static NPT_String FormatTimeStamp(NPT_UInt32) { return "00:00:00"; }
};
const char* didl_header = "<DIDL>";
const char* didl_footer = "</DIDL>";
enum EMediaControllerQuirks { EMEDIACONTROLLERQUIRKS_NONE = 0, EMEDIACONTROLLERQUIRKS_X_MKV = 1 };
struct CThumbLoader { virtual ~CThumbLoader() = default; };
struct CVideoThumbLoader : CThumbLoader {};
struct CMusicThumbLoader : CThumbLoader {};
const int UPnPPlayer = 1;
struct CUPnP {
  static void* GetServer() { return nullptr; }
  static void RegisterUserdata(void*);
  static void UnregisterUserdata(void*);
};
template<class F, class... A> NPT_Reference<PLT_MediaObject> BuildObject(F& file, A&&...) {
  auto* obj = new PLT_MediaObject();
  obj->m_Resources.v.resize(1);
  obj->m_Resources.v[0].m_Uri = file.GetPath().c_str();
  return NPT_Reference<PLT_MediaObject>(obj);
}

struct CFileItem {
  std::string path;
  bool video = true;
  explicit CFileItem(const char* p) : path(p) {}
  const std::string& GetPath() const { return path; }
  bool IsVideoDb() const { return false; }
  bool IsMusicDb() const { return false; }
  bool IsVideo() const { return video; }
  bool IsAudio() const { return !video; }
};
struct CPlayerOptions { double starttime = 0; };
struct IPlayerCallback {
  int started = 0, avstarted = 0, stopped = 0, ended = 0;
  void OnPlayBackStarted(const CFileItem&) { ++started; }
  void OnAVStarted(const CFileItem&) { ++avstarted; }
  void OnPlayBackStopped() { ++stopped; }
  void OnPlayBackEnded() { ++ended; }
  void OnPlayBackPaused() {}
  void OnPlayBackResumed() {}
  int Total() const { return started + avstarted + stopped + ended; }
};
struct LoggerImpl {
  template<class... A> void error(const char*, A&&...) {}
  template<class... A> void debug(const char*, A&&...) {}
  template<class... A> void info(const char*, A&&...) {}
};
using Logger = std::shared_ptr<LoggerImpl>;
struct CDataCacheCore {
  float speed = 1.0f;
  static CDataCacheCore& GetInstance() { static CDataCacheCore c; return c; }
  void Reset() {}
  void SetPlayTimes(int64_t, int64_t, int64_t, int64_t) {}
  float GetSpeed() const { return speed; }
  void SetSpeed(float, float s) { speed = s; }
};
struct CServiceBroker { static CDataCacheCore& GetDataCacheCore() { return CDataCacheCore::GetInstance(); } };

void DeliverPending();
struct CCriticalSection { void lock() {} void unlock() {} };
struct CEvent {
  bool signaled = false;
  void Set() { signaled = true; }
  void Reset() { signaled = false; }
  bool Wait(std::chrono::milliseconds d) {
    if (!signaled && d.count() > 0)
      DeliverPending();
    bool s = signaled;
    signaled = false;
    return s;
  }
};
namespace XbmcThreads {
template<class T = std::chrono::milliseconds> struct EndTime {
  EndTime() = default;
  template<class D> EndTime(D d) : initial(std::chrono::duration_cast<std::chrono::milliseconds>(d)) {}
  void Set(std::chrono::milliseconds d) { initial = d; polls = 0; }
  std::chrono::milliseconds GetInitialTimeoutValue() const { return initial; }
  bool IsTimePast() { return ++polls > 50; }
  std::chrono::milliseconds initial{0};
  int polls = 0;
};
}

// A busy-dialog wait runs the hook (the message pump), then the renderer answers.
int g_waits = 0;
std::function<void(int)> g_waitHook;
struct CGUIDialogBusy {
  static bool WaitOnEvent(CEvent& event) {
    ++g_waits;
    auto hook = g_waitHook;
    if (hook)
      hook(g_waits);
    DeliverPending();
    return event.Wait(0ms);
  }
};

namespace UPNP {
class CUPnPPlayerController;
struct PLT_DeviceDataReference {
  CUPnPPlayerController* owner = nullptr;
  void* AsPointer() { return nullptr; }
};
// The Platinum delegate: the controller answers a command to the userdata of the
// command.
struct PLT_MediaControllerDelegate {
  virtual ~PLT_MediaControllerDelegate() = default;
  virtual void OnSetAVTransportURIResult(NPT_Result, PLT_DeviceDataReference&, void*) {}
  virtual void OnSetNextAVTransportURIResult(NPT_Result, PLT_DeviceDataReference&, void*) {}
  virtual void OnPlayResult(NPT_Result, PLT_DeviceDataReference&, void*) {}
  virtual void OnStopResult(NPT_Result, PLT_DeviceDataReference&, void*) {}
  virtual void OnGetTransportInfoResult(NPT_Result, PLT_DeviceDataReference&, PLT_TransportInfo*,
                                        void*) {}
};
EMediaControllerQuirks GetMediaControllerQuirks(void*) { return EMEDIACONTROLLERQUIRKS_NONE; }

struct PLT_MediaController {
  std::vector<std::string> log;
  struct Pending {
    void* userdata;
    int delay;
    std::function<void(PLT_MediaControllerDelegate&)> answer;
  };
  std::vector<Pending> pending;
  std::function<void(const std::string&)> cmdHook;
  NPT_Result Send(PLT_DeviceDataReference& device, void* userdata, const char* cmd,
                  std::function<void(PLT_MediaControllerDelegate&)> answer, int delay = 0);
  NPT_Result FindBestResource(PLT_DeviceDataReference&, PLT_MediaObject&, NPT_Cardinal& i) {
    i = 0;
    return NPT_SUCCESS;
  }
  NPT_Result GetTransportInfo(PLT_DeviceDataReference&, NPT_UInt32, void* userdata);
  NPT_Result Stop(PLT_DeviceDataReference&, NPT_UInt32, void* userdata);
  NPT_Result SetAVTransportURI(PLT_DeviceDataReference&, NPT_UInt32, const NPT_String&,
                               const NPT_String&, void* userdata);
  NPT_Result SetNextAVTransportURI(PLT_DeviceDataReference& d, NPT_UInt32, const char*, const char*,
                                   void* userdata) {
    return Send(d, userdata, "SetNextAVTransportURI", nullptr);
  }
  NPT_Result Play(PLT_DeviceDataReference&, NPT_UInt32, const char*, void* userdata);
  NPT_Result Seek(PLT_DeviceDataReference& d, NPT_UInt32, const char*, const NPT_String&,
                  void* userdata) { return Send(d, userdata, "Seek", nullptr); }
  NPT_Result GetPositionInfo(PLT_DeviceDataReference& d, NPT_UInt32, void* userdata) {
    return Send(d, userdata, "GetPositionInfo", nullptr);
  }
  NPT_Result GetMediaInfo(PLT_DeviceDataReference& d, NPT_UInt32, void* userdata) {
    return Send(d, userdata, "GetMediaInfo", nullptr);
  }
};

// The renderer of one player. The first GetTransportInfo after a stop reports
// firstState, later ones PLAYING.
class CUPnPPlayerController : public PLT_MediaControllerDelegate {
public:
  CUPnPPlayerController(PLT_MediaController* c, const char* n) : m_control(c), name(n) {
    m_device.owner = this;
  }
  NPT_String GetTransportState() const { return m_trainfo.cur_transport_state; }
  NPT_String GetTransportStatus() const { return m_trainfo.cur_transport_status; }
  void SetTransportInfo(const PLT_TransportInfo& info) { m_trainfo = info; }
  bool pollDue = false;  // the position poll finds the transport poll due
  bool UpdatePositionInfo() { return std::exchange(pollDue, false); }
  PLT_MediaController* m_control;
  PLT_DeviceDataReference m_device;
  NPT_UInt32 m_instance = 0;
  PLT_TransportInfo m_trainfo;  // the state that the worker polls
  std::string name;
  std::string firstState = "STOPPED";
  int infoCalls = 0;
  int stopRejects = 0;  // the next Stop commands that the renderer rejects
  int stopDelay = 0;    // the delivery rounds that the answer of the next Stop waits
  int uriRejects = 0;   // the next SetAVTransportURI commands that the renderer rejects
  int transitioning = 0;  // the later GetTransportInfo answers that report TRANSITIONING
  std::string uri;      // the URI of the last SetAVTransportURI
  std::string nextState;  // the state of the next GetTransportInfo answer, once
};

NPT_Result PLT_MediaController::Send(PLT_DeviceDataReference& device, void* userdata,
                                     const char* cmd,
                                     std::function<void(PLT_MediaControllerDelegate&)> answer,
                                     int delay) {
  log.push_back(device.owner->name + ":" + cmd);
  if (answer)
    pending.push_back({userdata, delay, answer});
  if (cmdHook) {
    auto hook = cmdHook;
    hook(log.back());
  }
  return NPT_SUCCESS;
}
NPT_Result PLT_MediaController::GetTransportInfo(PLT_DeviceDataReference& device, NPT_UInt32,
                                                 void* userdata) {
  auto* r = device.owner;
  PLT_TransportInfo info;
  info.cur_transport_state = !r->nextState.empty() ? std::exchange(r->nextState, "").c_str()
                             : r->infoCalls++ == 0 ? r->firstState.c_str()
                             : r->transitioning > 0 ? (--r->transitioning, "TRANSITIONING")
                                                    : "PLAYING";
  info.cur_transport_status = "OK";
  return Send(device, userdata, "GetTransportInfo", [device, info](PLT_MediaControllerDelegate& d) {
    auto dev = device;
    auto i = info;
    d.OnGetTransportInfoResult(NPT_SUCCESS, dev, &i, nullptr);
  });
}
NPT_Result PLT_MediaController::Stop(PLT_DeviceDataReference& device, NPT_UInt32, void* userdata) {
  auto* r = device.owner;
  // the renderer decides when the command arrives
  const NPT_Result result = r->stopRejects > 0 ? (--r->stopRejects, NPT_FAILURE) : NPT_SUCCESS;
  if (result == NPT_SUCCESS)
    r->infoCalls = 0;
  const int delay = std::exchange(r->stopDelay, 0);
  return Send(device, userdata, "Stop", [device, result](PLT_MediaControllerDelegate& d) {
    auto dev = device;
    d.OnStopResult(result, dev, nullptr);
  }, delay);
}
NPT_Result PLT_MediaController::SetAVTransportURI(PLT_DeviceDataReference& device, NPT_UInt32,
                                                  const NPT_String& uri, const NPT_String&,
                                                  void* userdata) {
  auto* r = device.owner;
  const NPT_Result result = r->uriRejects > 0 ? (--r->uriRejects, NPT_FAILURE) : NPT_SUCCESS;
  if (result == NPT_SUCCESS)
    r->uri = uri;
  return Send(device, userdata, "SetAVTransportURI", [device, result](PLT_MediaControllerDelegate& d) {
    auto dev = device;
    d.OnSetAVTransportURIResult(result, dev, nullptr);
  });
}
NPT_Result PLT_MediaController::Play(PLT_DeviceDataReference& device, NPT_UInt32, const char*,
                                     void* userdata) {
  return Send(device, userdata, "Play", [device](PLT_MediaControllerDelegate& d) {
    auto dev = device;
    d.OnPlayResult(NPT_SUCCESS, dev, nullptr);
  });
}

PLT_MediaController g_ctl;
std::vector<void*> g_userdata;  // the Platinum userdata registry
bool Registered(void* userdata) {
  return std::find(g_userdata.begin(), g_userdata.end(), userdata) != g_userdata.end();
}

class CUPnPCommand;
class CUPnPPlayer {
public:
  CUPnPPlayer(IPlayerCallback& cb, const char* name)
    : m_control(&g_ctl),
      m_delegate(std::make_unique<CUPnPPlayerController>(&g_ctl, name)),
      m_callback(cb),
      m_logger(std::make_shared<LoggerImpl>()) {}
  ~CUPnPPlayer();
  bool OpenFile(const CFileItem& file, const CPlayerOptions& options);
  bool CloseFile(bool reopen = false);
  int PlayFile(const CFileItem& file, const CPlayerOptions& options, XbmcThreads::EndTime<>& timeout,
               uint64_t open);
  int WaitOnEvent(CEvent& event, XbmcThreads::EndTime<>& timeout, uint64_t open);
  void PublishTransportInfo(const CUPnPCommand& info);
  void PollTransportInfo(bool send);
  bool IsRunning() const { return running; }
  void Create() { ++creates; running = true; }
  void StopThread(bool) { running = false; }
  bool IsPaused() const { return false; }
  int64_t GetTime() { return 0; }
  int64_t GetTotalTime() { return 0; }
  // One pass of the worker loop of CUPnPPlayer::Process.
  void WorkerStep();

  PLT_MediaController* m_control;
  std::unique_ptr<CUPnPPlayerController> m_delegate;
  IPlayerCallback& m_callback;
  std::atomic<bool> m_started{false};
  std::atomic<bool> m_stopremote{false};
  std::atomic<uint64_t> m_openSequence{0};
  CCriticalSection m_pollSection;
  std::shared_ptr<CUPnPCommand> m_poll;
  uint64_t m_pollEpoch{0};
  uint64_t m_stateEpoch{0};
  bool m_hasVideo{false};
  bool m_hasAudio{false};
  XbmcThreads::EndTime<> m_updateTimer;
  Logger m_logger;
  int creates = 0;
  bool running = false;
};
}  // namespace UPNP

void CUPnP::RegisterUserdata(void* userdata) { UPNP::g_userdata.push_back(userdata); }
void CUPnP::UnregisterUserdata(void* userdata) {
  auto& v = UPNP::g_userdata;
  v.erase(std::remove(v.begin(), v.end(), userdata), v.end());
}
// One delivery round. Platinum drops the answer for a userdata that is not
// registered.
void DeliverPending() {
  auto v = std::move(UPNP::g_ctl.pending);
  UPNP::g_ctl.pending.clear();
  for (auto& e : v) {
    if (e.delay > 0) {
      --e.delay;
      UPNP::g_ctl.pending.push_back(e);
      continue;
    }
    auto& r = UPNP::g_userdata;
    if (std::find(r.begin(), r.end(), e.userdata) != r.end())
      e.answer(*static_cast<UPNP::PLT_MediaControllerDelegate*>(e.userdata));
  }
}
using namespace UPNP;

@PRODUCTION@

// ---- scenarios ----
const std::vector<std::string> NORMAL = {"GetTransportInfo", "SetAVTransportURI", "Play",
                                         "GetTransportInfo", "GetPositionInfo", "GetMediaInfo"};
CFileItem Item(const char* path = "http://server/a.mkv") { return CFileItem(path); }
void Begin(const std::string& name) {
  g_case = name;
  g_ctl.log.clear();
  g_ctl.pending.clear();
  g_ctl.cmdHook = nullptr;
  g_waitHook = nullptr;
  g_waits = 0;
}
std::vector<std::string> Of(const char* who, std::vector<std::string> v) {
  for (auto& s : v)
    s = std::string(who) + ":" + s;
  return v;
}
std::vector<std::string> Only(const char* who) {
  std::vector<std::string> v;
  const std::string prefix = std::string(who) + ":";
  for (auto& s : g_ctl.log)
    if (s.compare(0, prefix.size(), prefix) == 0)
      v.push_back(s);
  return v;
}
int Count(const std::string& entry) {
  int n = 0;
  for (auto& s : g_ctl.log)
    n += s == entry;
  return n;
}
void ExpectLog(const std::vector<std::string>& want) {
  if (g_ctl.log != want) {
    std::fprintf(stderr, "log mismatch, got:");
    for (auto& s : g_ctl.log)
      std::fprintf(stderr, " %s", s.c_str());
    std::fprintf(stderr, "\n");
    CHECK(g_ctl.log == want);
  }
}
std::vector<std::string> Head(std::vector<std::string> v, size_t n) {
  v.resize(n);
  return v;
}

void normal_open_close() {
  Begin("normal open then close");
  auto want = Of("P", NORMAL);
  IPlayerCallback cb;
  {
    CUPnPPlayer p(cb, "P");
    CHECK(p.OpenFile(Item(), CPlayerOptions()));
    ExpectLog(want);
    CHECK(cb.started == 1 && cb.avstarted == 1 && p.creates == 1 && p.m_started);
    CHECK(p.CloseFile());
    want.push_back("P:Stop");
    ExpectLog(want);
    CHECK(cb.stopped == 1);
  }
  ExpectLog(want);  // the destructor must not stop a second time
  CHECK(cb.stopped == 1);
}

void close_twice() {
  Begin("close twice stops once");
  IPlayerCallback cb;
  {
    CUPnPPlayer p(cb, "P");
    CHECK(p.OpenFile(Item(), CPlayerOptions()));
    CHECK(p.CloseFile());
    CHECK(p.CloseFile());
  }
  CHECK(Count("P:Stop") == 1);
}

void reopen() {
  Begin("reopen after close");
  IPlayerCallback cb;
  {
    CUPnPPlayer p(cb, "P");
    CHECK(p.OpenFile(Item(), CPlayerOptions()));
    CHECK(p.CloseFile(true));
    CHECK(p.OpenFile(Item(), CPlayerOptions()));
    CHECK(cb.started == 2 && cb.avstarted == 2 && p.creates == 2);
  }
  auto want = Of("P", NORMAL);
  want.push_back("P:Stop");
  for (auto& s : Of("P", NORMAL))
    want.push_back(s);
  want.push_back("P:Stop");
  ExpectLog(want);
}

void close_inside_wait() {
  for (size_t n = 1; n <= 4; ++n) {
    Begin("close inside wait " + std::to_string(n));
    IPlayerCallback cb;
    auto want = Of("P", Head(NORMAL, n));
    if (n >= 3)
      want.push_back("P:Stop");  // Play was sent: the renderer is stopped once
    {
      CUPnPPlayer p(cb, "P");
      g_waitHook = [&](int w) {
        if (w != int(n))
          return;
        g_waitHook = nullptr;
        CHECK(p.CloseFile());
      };
      CHECK(!p.OpenFile(Item(), CPlayerOptions()));
      CHECK(cb.Total() == 0 && p.creates == 0 && !p.running);
      ExpectLog(want);
    }
    ExpectLog(want);  // the destructor sends nothing
  }
}

void close_inside_wait_renderer_playing() {
  const std::vector<std::string> flow = {"GetTransportInfo", "Stop", "SetAVTransportURI", "Play"};
  for (size_t n = 2; n <= 4; n += 2) {
    Begin("renderer playing, close inside wait " + std::to_string(n));
    IPlayerCallback cb;
    auto want = Of("P", Head(flow, n));
    if (n >= 3)
      want.push_back("P:Stop");
    {
      CUPnPPlayer p(cb, "P");
      p.m_delegate->firstState = "PLAYING";
      g_waitHook = [&](int w) {
        if (w != int(n))
          return;
        g_waitHook = nullptr;
        CHECK(p.CloseFile());
      };
      CHECK(!p.OpenFile(Item(), CPlayerOptions()));
      CHECK(cb.Total() == 0 && p.creates == 0);
      ExpectLog(want);
    }
    ExpectLog(want);
  }
}

void newer_player_on_same_renderer() {
  for (size_t n : {size_t(1), size_t(3)}) {
    Begin("newer player starts inside wait " + std::to_string(n));
    IPlayerCallback cbA, cbB;
    auto a = std::make_unique<CUPnPPlayer>(cbA, "A");
    auto b = std::make_unique<CUPnPPlayer>(cbB, "B");
    g_waitHook = [&](int w) {
      if (w != int(n))
        return;
      g_waitHook = nullptr;
      CHECK(a->CloseFile());
      CHECK(b->OpenFile(Item(), CPlayerOptions()));
    };
    CHECK(!a->OpenFile(Item(), CPlayerOptions()));
    CHECK(cbA.Total() == 0 && a->creates == 0);
    CHECK(cbB.started == 1 && cbB.avstarted == 1 && b->creates == 1);
    auto wantA = Of("A", Head(NORMAL, n));
    if (n >= 3)
      wantA.push_back("A:Stop");
    CHECK(Only("A") == wantA);
    CHECK(Only("B") == Of("B", NORMAL));
    a.reset();
    CHECK(Only("A") == wantA);  // the old destructor must not stop the newer playback
    CHECK(Count("B:Stop") == 0);
    b.reset();
    CHECK(Count("B:Stop") == 1);
  }
}

// The application reuses a playing remote player for a newer open. A newer open
// that starts inside a wait of an older open on the same player owns the renderer.
void newer_open_on_same_player() {
  for (int n = 1; n <= 4; ++n) {
    Begin("newer open on the same player inside wait " + std::to_string(n));
    IPlayerCallback cb;
    CUPnPPlayer p(cb, "P");
    CHECK(p.OpenFile(Item("http://server/current.mkv"), CPlayerOptions()));
    g_ctl.log.clear();
    g_waits = 0;
    g_waitHook = [&](int w) {
      if (w != n)
        return;
      g_waitHook = nullptr;
      CHECK(p.OpenFile(Item("http://server/newer.mkv"), CPlayerOptions()));
    };
    CHECK(!p.OpenFile(Item("http://server/older.mkv"), CPlayerOptions()));
    // the older open sends no command after the newer open
    CHECK(p.m_delegate->uri == "http://server/newer.mkv");
    CHECK(g_ctl.log.back() == "P:GetMediaInfo");
    CHECK(cb.started == 2 && cb.avstarted == 2 && cb.stopped == 0);
    CHECK(p.CloseFile());
    CHECK(Count("P:Stop") >= 1 && g_ctl.log.back() == "P:Stop");
  }
}

// A rejected Stop leaves the renderer playing for this player: the next close
// stops the renderer again.
void rejected_stop() {
  Begin("rejected stop after an open");
  IPlayerCallback cb;
  {
    CUPnPPlayer p(cb, "P");
    CHECK(p.OpenFile(Item(), CPlayerOptions()));
    p.m_delegate->stopRejects = 1;
    CHECK(!p.CloseFile());
    CHECK(p.CloseFile());
    CHECK(p.CloseFile());
  }
  CHECK(Count("P:Stop") == 2);

  // The answer of Play arrives during the wait of the close for the Stop, and the
  // renderer rejects the Stop later: the answer of Play is not the answer of Stop.
  Begin("late answer of Play during the stop of a close");
  {
    CUPnPPlayer p(cb, "P");
    g_waitHook = [&](int w) {
      if (w != 3)  // the wait for the answer to Play
        return;
      g_waitHook = nullptr;
      p.m_delegate->stopRejects = 1;
      p.m_delegate->stopDelay = 1;
      CHECK(!p.CloseFile());
    };
    CHECK(!p.OpenFile(Item(), CPlayerOptions()));
    CHECK(p.CloseFile());
    CHECK(p.CloseFile());
  }
  CHECK(Count("P:Stop") == 2);

  Begin("rejected stop during an open");
  {
    CUPnPPlayer p(cb, "P");
    g_waitHook = [&](int w) {
      if (w != 3)  // the wait for the answer to Play
        return;
      g_waitHook = nullptr;
      p.m_delegate->stopRejects = 1;
      CHECK(!p.CloseFile());
    };
    CHECK(!p.OpenFile(Item(), CPlayerOptions()));
    CHECK(p.CloseFile());
  }
  CHECK(Count("P:Stop") == 2);
}

// The wait for PLAYING reads the answer of the command that the wait sent, not
// the state that the worker polled.
void transitioning_renderer() {
  Begin("renderer reports TRANSITIONING before PLAYING");
  IPlayerCallback cb;
  CUPnPPlayer p(cb, "P");
  p.m_delegate->transitioning = 1;
  p.m_delegate->m_trainfo.cur_transport_state = "STOPPED";
  p.m_delegate->m_trainfo.cur_transport_status = "ERROR_OCCURED";
  CHECK(p.OpenFile(Item(), CPlayerOptions()));
  CHECK(cb.started == 1);
  ExpectLog(Of("P", {"GetTransportInfo", "SetAVTransportURI", "Play", "GetTransportInfo",
                     "GetTransportInfo", "GetPositionInfo", "GetMediaInfo"}));
}

// An open of a playing player replaces the item: the stop of the renderer during
// the open is not the end of the replaced item. A failed open reports the stop.
void reopen_notifications() {
  for (const bool fail : {false, true}) {
    Begin(fail ? "failed reopen of a playing player" : "reopen of a playing player");
    IPlayerCallback cb;
    CUPnPPlayer p(cb, "P");
    CHECK(p.OpenFile(Item("http://server/current.mkv"), CPlayerOptions()));
    p.m_delegate->m_trainfo.cur_transport_state = "PLAYING";
    p.m_delegate->firstState = "PLAYING";
    p.m_delegate->infoCalls = 0;
    p.m_delegate->uriRejects = fail ? 1 : 0;
    g_waitHook = [&](int) {
      // the worker polled the renderer that the open stopped
      if (Count("P:Stop") == 1) {
        p.m_delegate->m_trainfo.cur_transport_state = "STOPPED";
        p.WorkerStep();
      }
    };
    CHECK(p.OpenFile(Item("http://server/next.mkv"), CPlayerOptions()) == !fail);
    g_waitHook = nullptr;
    CHECK(cb.ended == 0);
    // the open published the state of the renderer: the new item plays
    if (!fail)
      CHECK(p.m_delegate->GetTransportState() == "PLAYING");
    else
      p.m_delegate->m_trainfo.cur_transport_state = "STOPPED";
    p.WorkerStep();
    CHECK(cb.ended == 0);
    CHECK(cb.started == (fail ? 1 : 2) && cb.stopped == (fail ? 1 : 0));
    if (!fail) {
      // the end of the new item
      p.m_delegate->m_trainfo.cur_transport_state = "STOPPED";
      p.WorkerStep();
      p.WorkerStep();
      CHECK(cb.ended == 1 && cb.stopped == 0);
    }
  }
}

// A worker poll sent before an open and answered after the open does not
// replace the state that the open published.
void stale_poll() {
  Begin("poll answered after a reopen");
  IPlayerCallback cb;
  CUPnPPlayer p(cb, "P");
  CHECK(p.OpenFile(Item("http://server/current.mkv"), CPlayerOptions()));
  CHECK(p.m_delegate->GetTransportState() == "PLAYING");
  // the worker sends a poll; the renderer answers STOPPED, two rounds late
  p.m_delegate->nextState = "STOPPED";
  p.m_delegate->pollDue = true;
  p.WorkerStep();
  CHECK(g_ctl.log.back() == "P:GetTransportInfo");
  g_ctl.pending.back().delay = 1;
  p.m_delegate->firstState = "PLAYING";
  p.m_delegate->infoCalls = 0;
  CHECK(p.OpenFile(Item("http://server/next.mkv"), CPlayerOptions()));
  CHECK(g_ctl.pending.empty());  // the poll was answered during the open
  p.WorkerStep();
  CHECK(p.m_delegate->GetTransportState() == "PLAYING" && cb.ended == 0);
  // a poll sent after the open publishes the state
  p.m_delegate->nextState = "STOPPED";
  p.m_delegate->pollDue = true;
  p.WorkerStep();
  DeliverPending();
  p.WorkerStep();
  CHECK(p.m_delegate->GetTransportState() == "STOPPED" && cb.ended == 1);
}

// A command lives until the renderer answers the command, also after the wait
// for the answer ended: the address of the command stays registered, so no
// newer command takes the address while the answer can arrive.
void abandoned_command() {
  Begin("command abandoned by a close");
  IPlayerCallback cb;
  CUPnPPlayer p(cb, "P");
  void* abandoned = nullptr;
  g_waitHook = [&](int w) {
    if (w != 2)  // the wait for the answer to SetAVTransportURI
      return;
    g_waitHook = nullptr;
    g_ctl.pending.back().delay = 2;
    abandoned = g_ctl.pending.back().userdata;
    CHECK(p.CloseFile());
  };
  CHECK(!p.OpenFile(Item(), CPlayerOptions()));
  CHECK(abandoned && Registered(abandoned));
  DeliverPending();
  DeliverPending();
  CHECK(g_ctl.pending.empty() && Registered(abandoned));
  // the answered command is freed later; the player opens again
  CHECK(p.OpenFile(Item(), CPlayerOptions()));
}

void close_during_last_command() {
  // The last command of an open with a start time is Seek, without a wait after
  // the command: a close on another thread after the last wait ends the open.
  Begin("close while the last command is sent");
  IPlayerCallback cb;
  {
    CUPnPPlayer p(cb, "P");
    g_ctl.cmdHook = [&](const std::string& c) {
      if (c == "P:Seek") {
        g_ctl.cmdHook = nullptr;
        CHECK(p.CloseFile());
      }
    };
    CPlayerOptions options;
    options.starttime = 10;
    CHECK(!p.OpenFile(Item(), options));
    CHECK(cb.Total() == 0 && p.creates == 0 && !p.running);
  }
  auto want = Of("P", Head(NORMAL, 4));
  want.push_back("P:Seek");
  want.push_back("P:Stop");
  ExpectLog(want);
}

void attach() {
  Begin("attach to playing renderer");
  {
    IPlayerCallback cb;
    CUPnPPlayer p(cb, "P");
    p.m_delegate->firstState = "PLAYING";
    CHECK(p.OpenFile(Item(""), CPlayerOptions()));
    CHECK(cb.started == 1 && cb.avstarted == 1 && p.creates == 1);
    ExpectLog(Of("P", {"GetTransportInfo", "GetPositionInfo", "GetMediaInfo"}));
    CHECK(p.CloseFile());
    CHECK(Count("P:Stop") == 1);
  }
  CHECK(Count("P:Stop") == 1);

  Begin("attach to stopped renderer");
  {
    IPlayerCallback cb;
    CUPnPPlayer p(cb, "P");
    CHECK(!p.OpenFile(Item(""), CPlayerOptions()));
    CHECK(cb.Total() == 0 && p.creates == 0);
  }
  ExpectLog(Of("P", {"GetTransportInfo"}));

  Begin("attach, close inside wait");
  {
    IPlayerCallback cb;
    CUPnPPlayer p(cb, "P");
    p.m_delegate->firstState = "PLAYING";
    g_waitHook = [&](int) {
      g_waitHook = nullptr;
      CHECK(p.CloseFile());
    };
    CHECK(!p.OpenFile(Item(""), CPlayerOptions()));
    CHECK(cb.Total() == 0 && p.creates == 0);
  }
  ExpectLog(Of("P", {"GetTransportInfo"}));
}

int main(int argc, char** argv) {
  const std::vector<std::pair<const char*, void (*)()>> cases = {
      {"normal_open_close", normal_open_close},
      {"close_twice", close_twice},
      {"reopen", reopen},
      {"close_inside_wait", close_inside_wait},
      {"close_inside_wait_renderer_playing", close_inside_wait_renderer_playing},
      {"newer_player_on_same_renderer", newer_player_on_same_renderer},
      {"newer_open_on_same_player", newer_open_on_same_player},
      {"rejected_stop", rejected_stop},
      {"transitioning_renderer", transitioning_renderer},
      {"reopen_notifications", reopen_notifications},
      {"stale_poll", stale_poll},
      {"abandoned_command", abandoned_command},
      {"close_during_last_command", close_during_last_command},
      {"attach", attach}};
  if (argc > 1) {
    for (auto& c : cases)
      if (std::string(c.first) == argv[1]) {
        c.second();
        return 0;
      }
    return 2;
  }
  for (auto& c : cases)
    c.second();
  std::puts("ok");
}
'''

# Each control removes one new check from the production text. A rig that still
# passes would not prove the check.
CONTROLS = [
    ('wait does not check the open sequence',
     'if (open != m_openSequence)\n  {\n    m_logger->debug', 'if (false)\n  {\n    m_logger->debug'),
    ('Play does not mark the renderer as started',
     '    m_stopremote = true;\n    NPT_CHECK_LABEL_SEVERE(WaitOnEvent', '    NPT_CHECK_LABEL_SEVERE(WaitOnEvent'),
    ('OpenFile does not start a new open',
     'const uint64_t open = ++m_openSequence;', 'const uint64_t open = m_openSequence;'),
    ('OpenFile does not check the open sequence before starting',
     'if (open != m_openSequence)\n    goto failed;', 'if (false)\n    goto failed;'),
    ('CloseFile does not end the open',
     '  ++m_openSequence;\n  NPT_CHECK_POINTER', '  NPT_CHECK_POINTER'),
    ('CloseFile can stop the renderer twice',
     '    m_stopremote = false;\n  }', '  }'),
    ('CloseFile drops the renderer before the stop succeeds',
     'if (m_stopremote)\n  {\n    const auto stop', 'if (m_stopremote.exchange(false))\n  {\n    const auto stop'),
    ('all commands share one answer',
     'CEvent m_event;\n  NPT_Result m_status = NPT_FAILURE;',
     'static inline CEvent m_event;\n  static inline NPT_Result m_status = NPT_FAILURE;'),
    ('a command is freed before the answer arrives',
     'return commands->emplace_back(std::make_shared<CUPnPCommand>());',
     'return std::make_shared<CUPnPCommand>();'),
    ('the open does not publish the state of the renderer',
     '  m_delegate->SetTransportInfo(info.m_trainfo);\n  ++m_stateEpoch;', '  ++m_stateEpoch;'),
    ('a poll sent before the state of an open replaces the state',
     'if (m_pollEpoch == m_stateEpoch)', 'if (true)'),
    ('a reopen keeps the end notification of the replaced item',
     'const bool reopen = m_started.exchange(false);', 'const bool reopen = m_started;'),
    ('a failed reopen does not report the stop of the replaced item',
     'if (reopen && !m_started)\n', 'if (reopen && false)\n'),
    ('the worker reports the end twice',
     'if (m_delegate->GetTransportState() == "STOPPED" && m_started.exchange(false))',
     'if (m_delegate->GetTransportState() == "STOPPED" && m_started)'),
]


def production():
    source = (ROOT / 'xbmc/network/upnp/UPnPPlayer.cpp').read_text()
    header = (ROOT / 'xbmc/network/upnp/UPnPPlayer.h').read_text()
    command = source.index('class CUPnPCommand : public PLT_MediaControllerDelegate')
    command = source[command:source.index('\n};\n', command) + 4]
    command += function(source, 'static std::shared_ptr<CUPnPCommand> NewCommand()')
    signatures = ['int CUPnPPlayer::WaitOnEvent(', 'int CUPnPPlayer::PlayFile(', 'bool CUPnPPlayer::OpenFile(',
                  'bool CUPnPPlayer::CloseFile(', 'CUPnPPlayer::~CUPnPPlayer()',
                  'void CUPnPPlayer::PublishTransportInfo(', 'void CUPnPPlayer::PollTransportInfo(']
    body = '\n'.join(function(source, signature) for signature in signatures)
    # The worker: one pass of the Process loop.
    process = function(source, 'void CUPnPPlayer::Process()')
    poll = '    PollTransportInfo(m_delegate->UpdatePositionInfo());\n'
    assert poll in process
    worker = poll + function(process, '    if (m_started)')
    body += '\nvoid CUPnPPlayer::WorkerStep()\n{\n' + worker + '\n}\n'
    code = PRELUDE.replace('@PRODUCTION@', 'namespace UPNP {\n' + command + body + '\n}  // namespace UPNP')
    return code, header


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--negative-controls', action='store_true')
    args = parser.parse_args()
    code, header = production()
    run(code)
    # The header must declare the state the production methods use.
    assert 'std::atomic<uint64_t> m_openSequence' in header, 'm_openSequence must be atomic (CloseFile runs on another thread)'
    assert 'std::atomic<bool> m_stopremote' in header, 'm_stopremote must be atomic'
    assert 'std::atomic<bool> m_started' in header, 'm_started must be atomic (the worker thread reads the flag)'
    assert 'int WaitOnEvent(CEvent& event, XbmcThreads::EndTime<>& timeout, uint64_t open);' in header
    if args.negative_controls:
        for label, before, after in CONTROLS:
            assert before in code, label
            run(code.replace(before, after, 1), True, label)
    print('UPnP open/close: PASS (production methods; ASan/UBSan; recording renderer)')


CASES = ['normal_open_close', 'close_twice', 'reopen', 'close_inside_wait',
         'close_inside_wait_renderer_playing', 'newer_player_on_same_renderer',
         'newer_open_on_same_player', 'rejected_stop', 'transitioning_renderer', 'reopen_notifications', 'stale_poll', 'abandoned_command',
         'close_during_last_command', 'attach']


def run(code, expect_failure=False, label=''):
    with tempfile.TemporaryDirectory(prefix='upnp-open-close-') as temporary:
        out = Path(temporary)
        (out / 'test.cpp').write_text(code)
        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
                        str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
        failures = {}
        for name in CASES:
            result = subprocess.run([str(out / 'test'), name], capture_output=True, text=True,
                                    timeout=20)
            if result.returncode != 0:
                assert 'Assertion' in result.stderr, result.stderr
                failures[name] = result.stderr.strip().splitlines()[-1]
        if expect_failure:
            assert failures, 'negative control passed: ' + label
            print('Negative control rejected at runtime:', label)
        else:
            print('case results:', 'PASS' if not failures else 'FAIL')
        for name, message in failures.items():
            print('  FAIL', name, '->', message)
        assert expect_failure or not failures


if __name__ == '__main__':
    main()
