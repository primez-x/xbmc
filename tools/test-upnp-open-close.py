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
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <memory>
#include <string>
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
  static void UnregisterUserdata(void*);
};
template<class... A> NPT_Reference<PLT_MediaObject> BuildObject(A&&...) {
  auto* obj = new PLT_MediaObject();
  obj->m_Resources.v.resize(1);
  obj->m_Resources.v[0].m_Uri = "http://server/item";
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
  int started = 0, avstarted = 0, stopped = 0;
  void OnPlayBackStarted(const CFileItem&) { ++started; }
  void OnAVStarted(const CFileItem&) { ++avstarted; }
  void OnPlayBackStopped() { ++stopped; }
  int Total() const { return started + avstarted + stopped; }
};
struct LoggerImpl {
  template<class... A> void error(const char*, A&&...) {}
  template<class... A> void debug(const char*, A&&...) {}
};
using Logger = std::shared_ptr<LoggerImpl>;
struct CDataCacheCoreStub { void Reset() {} };
struct CServiceBroker { static CDataCacheCoreStub& GetDataCacheCore() { static CDataCacheCoreStub c; return c; } };

void DeliverPending();
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
struct PLT_MediaControllerDelegate { virtual ~PLT_MediaControllerDelegate() = default; };
struct PLT_DeviceDataReference { void* AsPointer() { return nullptr; } };
EMediaControllerQuirks GetMediaControllerQuirks(void*) { return EMEDIACONTROLLERQUIRKS_NONE; }

struct PLT_MediaController {
  std::vector<std::string> log;
  struct Pending { CUPnPPlayerController* owner; std::function<void(CUPnPPlayerController&)> answer; };
  std::vector<Pending> pending;
  std::function<void(const std::string&)> cmdHook;
  NPT_Result Send(PLT_MediaControllerDelegate* d, const char* cmd,
                  std::function<void(CUPnPPlayerController&)> answer);
  NPT_Result FindBestResource(PLT_DeviceDataReference&, PLT_MediaObject&, NPT_Cardinal& i) {
    i = 0;
    return NPT_SUCCESS;
  }
  NPT_Result GetTransportInfo(PLT_DeviceDataReference&, NPT_UInt32, PLT_MediaControllerDelegate* d);
  NPT_Result Stop(PLT_DeviceDataReference&, NPT_UInt32, PLT_MediaControllerDelegate* d);
  NPT_Result SetAVTransportURI(PLT_DeviceDataReference&, NPT_UInt32, const NPT_String&,
                               const NPT_String&, PLT_MediaControllerDelegate* d);
  NPT_Result Play(PLT_DeviceDataReference&, NPT_UInt32, const char*, PLT_MediaControllerDelegate* d);
  NPT_Result Seek(PLT_DeviceDataReference&, NPT_UInt32, const char*, const NPT_String&,
                  PLT_MediaControllerDelegate* d) { return Send(d, "Seek", nullptr); }
  NPT_Result GetPositionInfo(PLT_DeviceDataReference&, NPT_UInt32, PLT_MediaControllerDelegate* d) {
    return Send(d, "GetPositionInfo", nullptr);
  }
  NPT_Result GetMediaInfo(PLT_DeviceDataReference&, NPT_UInt32, PLT_MediaControllerDelegate* d) {
    return Send(d, "GetMediaInfo", nullptr);
  }
};

class CUPnPPlayerController : public PLT_MediaControllerDelegate {
public:
  CUPnPPlayerController(PLT_MediaController* c, const char* n) : m_control(c), name(n) {}
  NPT_String GetTransportState() const { return m_trainfo.cur_transport_state; }
  NPT_String GetTransportStatus() const { return m_trainfo.cur_transport_status; }
  PLT_MediaController* m_control;
  PLT_DeviceDataReference m_device;
  NPT_UInt32 m_instance = 0;
  NPT_Result m_resstatus = NPT_SUCCESS;
  CEvent m_resevent, m_traevnt;
  PLT_TransportInfo m_trainfo;
  std::string name;
  // The first GetTransportInfo after a stop reports firstState, later ones PLAYING.
  std::string firstState = "STOPPED";
  int infoCalls = 0;
};

NPT_Result PLT_MediaController::Send(PLT_MediaControllerDelegate* d, const char* cmd,
                                     std::function<void(CUPnPPlayerController&)> answer) {
  auto* c = static_cast<CUPnPPlayerController*>(d);
  log.push_back(c->name + ":" + cmd);
  if (answer)
    pending.push_back({c, answer});
  if (cmdHook) {
    auto hook = cmdHook;
    hook(log.back());
  }
  return NPT_SUCCESS;
}
NPT_Result PLT_MediaController::GetTransportInfo(PLT_DeviceDataReference&, NPT_UInt32,
                                                 PLT_MediaControllerDelegate* d) {
  auto* c = static_cast<CUPnPPlayerController*>(d);
  const bool first = c->infoCalls++ == 0;
  return Send(d, "GetTransportInfo", [first](CUPnPPlayerController& p) {
    p.m_trainfo.cur_transport_state = first ? p.firstState.c_str() : "PLAYING";
    p.m_trainfo.cur_transport_status = "OK";
    p.m_traevnt.Set();
  });
}
NPT_Result PLT_MediaController::Stop(PLT_DeviceDataReference&, NPT_UInt32,
                                     PLT_MediaControllerDelegate* d) {
  return Send(d, "Stop", [](CUPnPPlayerController& p) {
    p.infoCalls = 0;
    p.m_resstatus = NPT_SUCCESS;
    p.m_resevent.Set();
  });
}
NPT_Result PLT_MediaController::SetAVTransportURI(PLT_DeviceDataReference&, NPT_UInt32,
                                                  const NPT_String&, const NPT_String&,
                                                  PLT_MediaControllerDelegate* d) {
  return Send(d, "SetAVTransportURI", [](CUPnPPlayerController& p) {
    p.m_resstatus = NPT_SUCCESS;
    p.m_resevent.Set();
  });
}
NPT_Result PLT_MediaController::Play(PLT_DeviceDataReference&, NPT_UInt32, const char*,
                                     PLT_MediaControllerDelegate* d) {
  return Send(d, "Play", [](CUPnPPlayerController& p) {
    p.m_resstatus = NPT_SUCCESS;
    p.m_resevent.Set();
  });
}

PLT_MediaController g_ctl;

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
  int PlayFile(const CFileItem& file, const CPlayerOptions& options, XbmcThreads::EndTime<>& timeout);
@WAITDECL@
  bool IsRunning() const { return running; }
  void Create() { ++creates; running = true; }
  void StopThread(bool) { running = false; }

  PLT_MediaController* m_control;
  std::unique_ptr<CUPnPPlayerController> m_delegate;
  IPlayerCallback& m_callback;
  bool m_started = false;
  @STOPTYPE@ m_stopremote{false};
  std::atomic<bool> m_closed{false};
  bool m_hasVideo{false};
  bool m_hasAudio{false};
  XbmcThreads::EndTime<> m_updateTimer;
  Logger m_logger;
  int creates = 0;
  bool running = false;
};
}  // namespace UPNP

void CUPnP::UnregisterUserdata(void* delegate) {
  // Platinum drops the answers of an unregistered delegate.
  auto& p = UPNP::g_ctl.pending;
  for (size_t i = 0; i < p.size();)
    p[i].owner == delegate ? (void)p.erase(p.begin() + i) : (void)++i;
}
void DeliverPending() {
  auto v = std::move(UPNP::g_ctl.pending);
  UPNP::g_ctl.pending.clear();
  for (auto& e : v)
    e.answer(*e.owner);
}
using namespace UPNP;

@PRODUCTION@

// ---- scenarios ----
const std::vector<std::string> NORMAL = {"GetTransportInfo", "SetAVTransportURI", "Play",
                                         "GetTransportInfo", "GetTransportInfo",
                                         "GetPositionInfo", "GetMediaInfo"};
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
    // A late answer of the first session does not answer a command of the second.
    p.m_delegate->m_traevnt.Set();
    p.m_delegate->m_resevent.Set();
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

void close_during_last_command() {
  Begin("close while the last command is sent");
  IPlayerCallback cb;
  int infos = 0;
  {
    CUPnPPlayer p(cb, "P");
    g_ctl.cmdHook = [&](const std::string& c) {
      if (c == "P:GetTransportInfo" && ++infos == 3) {
        g_ctl.cmdHook = nullptr;
        CHECK(p.CloseFile());
      }
    };
    CHECK(!p.OpenFile(Item(), CPlayerOptions()));
    CHECK(cb.Total() == 0 && p.creates == 0 && !p.running);
  }
  auto want = Of("P", Head(NORMAL, 5));
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
    ('wait does not check the closed state',
     'if (m_closed)\n  {\n    m_logger->debug', 'if (false)\n  {\n    m_logger->debug'),
    ('Play does not mark the renderer as started',
     '  m_stopremote = true;\n  NPT_CHECK_LABEL_SEVERE(WaitOnEvent', '  NPT_CHECK_LABEL_SEVERE(WaitOnEvent'),
    ('OpenFile does not reset the closed state',
     'timeout(10s);\n  m_closed = false;', 'timeout(10s);'),
    ('OpenFile does not check the closed state before starting',
     'if (m_closed)\n    goto failed;', 'if (false)\n    goto failed;'),
    ('CloseFile does not set the closed state',
     '  m_closed = true;\n  NPT_CHECK_POINTER', '  NPT_CHECK_POINTER'),
    ('CloseFile can stop the renderer twice',
     'if (m_stopremote.exchange(false))', 'if (m_stopremote)'),
]


def production():
    source = (ROOT / 'xbmc/network/upnp/UPnPPlayer.cpp').read_text()
    header = (ROOT / 'xbmc/network/upnp/UPnPPlayer.h').read_text()
    wait = re.search(r'(?:static NPT_Result |int CUPnPPlayer::)WaitOnEvent\(', source).group(0)
    signatures = [wait, 'int CUPnPPlayer::PlayFile(', 'bool CUPnPPlayer::OpenFile(',
                  'bool CUPnPPlayer::CloseFile(', 'CUPnPPlayer::~CUPnPPlayer()']
    body = '\n'.join(function(source, signature) for signature in signatures)
    stop = 'std::atomic<bool>' if 'std::atomic<bool> m_stopremote' in header else 'bool'
    waitdecl = ('int WaitOnEvent(CEvent& event, XbmcThreads::EndTime<>& timeout);'
                if 'CUPnPPlayer::WaitOnEvent(' in wait else '')
    code = (PRELUDE.replace('@WAITDECL@', waitdecl).replace('@STOPTYPE@', stop)
            .replace('@PRODUCTION@', 'namespace UPNP {\n' + body + '\n}  // namespace UPNP'))
    return code, header


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--negative-controls', action='store_true')
    args = parser.parse_args()
    code, header = production()
    run(code)
    # The header must declare the state the production methods use.
    assert 'std::atomic<bool> m_closed' in header, 'm_closed must be atomic (CloseFile runs on another thread)'
    assert 'std::atomic<bool> m_stopremote' in header, 'm_stopremote must be atomic'
    assert 'int WaitOnEvent(' in header
    if args.negative_controls:
        for label, before, after in CONTROLS:
            assert before in code, label
            run(code.replace(before, after, 1), True, label)
    print('UPnP open/close: PASS (production methods; ASan/UBSan; recording renderer)')


CASES = ['normal_open_close', 'close_twice', 'reopen', 'close_inside_wait',
         'close_inside_wait_renderer_playing', 'newer_player_on_same_renderer',
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
