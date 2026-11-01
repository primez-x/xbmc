#!/usr/bin/env python3
"""RetroPlayer open and close with production methods and recording stubs.

A newer item can retire the game player while the open of the game waits in a
dialog: the game client selection with the emulator install, the savestate
question, or a wake dialog of a network share during the initialization of the
game client, the open of the game, the load of the savestate or the request of
the achievements. The router closes the player each frame until the close
completes, and opens the newer item after the close completes. The open owns
the item until the open returns: a close during the open (also from another
thread) cancels the open and completes only after the open released the item.
The game clients are shared (the add-on cache), so the newer game uses the game
client after the retired open released the game client. The close of a player
reports the end only of a started game, and a close without an open item
changes nothing: the retired player reports no end that stops the newer item,
and the close in the destructor of the retired player keeps the render
callbacks of a newer game.
"""
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
function = runpy.run_path(str(ROOT / 'tools/test-render-slot-publication.py'))['function']

PRELUDE = r'''
#include <atomic>
#include <cassert>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
using CCriticalSection = std::recursive_mutex;
constexpr int LOGINFO=0, LOGERROR=1, LOGDEBUG=2, TMSG_SWITCHTOFULLSCREEN=3;
struct CLog {template<class... A> static void Log(A&&...) {}};
struct CURL {static std::string GetRedacted(const std::string& s){return s;}};
struct StringUtils {template<class... A> static std::string Format(A&&...){return {};}};
struct Localize {std::string Get(int){return {};}} g_localizeStrings;
static std::function<bool()> savestateDialog;
// A dialog that processes messages during a step of the open, in the order of the open
enum {SELECTION,INITIALIZE,GAME_OPEN,SAVESTATE_QUESTION,ACHIEVEMENTS,PLAYBACK,STEPS};
static int waitStep=-1;static std::function<void()> waitHook;
static void Wait(int step){if(step==waitStep&&waitHook)std::exchange(waitHook,nullptr)();}
static int savestates=0,questions=0;
struct CGUIDialogYesNo {template<class... A> static bool ShowAndGetInput(A&&...){
 ++questions;Wait(SAVESTATE_QUESTION);return savestateDialog?std::exchange(savestateDialog,nullptr)():true;}};
struct GameTag {std::string GetGameClient()const{return "game.test";}};
struct CFileItem {std::string GetPath()const{return "game.rom";} GameTag* GetGameInfoTag(){static GameTag g;return &g;}};
struct CPlayerOptions {};
static std::function<void()> startedHook;
struct Callback {int started=0,ended=0;void OnPlayBackStarted(const CFileItem&){++started;if(startedHook)std::exchange(startedHook,nullptr)();}
 void OnAVStarted(const CFileItem&){} void OnPlayBackEnded(){++ended;}};
struct GameSettings {bool AutosaveEnabled(){return false;} std::string GetRAUsername(){return {};}
 std::string GetRAToken(){return {};}};
struct GameServices {::GameSettings& GameSettings(){static ::GameSettings s;return s;}};
struct Dummy {template<class... A> explicit Dummy(A&&...){} Dummy()=default;void ResetInfo(){}
 void SetDataCache(void*){} void SetVideoFps(float){} void StartAgentManager(){} void StopAgentManager(){}
 std::string CreateSavestate(bool){++savestates;return {};} void EnableRichPresence(){Wait(ACHIEVEMENTS);} };
struct CRPProcessInfo: Dummy {static std::unique_ptr<CRPProcessInfo> CreateInstance(){return std::make_unique<CRPProcessInfo>();}};
using CGUIGameMessenger=Dummy;using CRPRenderManager=Dummy;using CRPStreamManager=Dummy;
using CRetroPlayerInput=Dummy;using CCheevos=Dummy;using CGUIPlaybackControl=Dummy;using CRetroPlayerAutoSave=Dummy;
static int gameOpens=0,gameCloses=0;
struct CGameClient {bool playing=false,loaded=false;bool Initialize(){Wait(INITIALIZE);loaded=true;return true;}
 template<class... A> bool OpenFile(A&&...){Wait(GAME_OPEN);++gameOpens;playing=true;return true;}
 template<class... A> bool OpenStandalone(A&&...){++gameOpens;playing=true;return true;}
 std::string ID(){return "game.test";}std::string Name(){return "test";}float GetFrameRate(){return 60;}
 void CloseFile(){++gameCloses;playing=false;}void Unload(){loaded=false;playing=false;}
};
// The add-on manager returns the one cached game client of an add-on to every player.
static std::shared_ptr<CGameClient>& CachedClient(){static auto client=std::make_shared<CGameClient>();return client;}
namespace ADDON {using AddonPtr=std::shared_ptr<CGameClient>;enum class AddonType{GAMEDLL};enum class OnlyEnabled{CHOICE_YES};}
using ADDON::OnlyEnabled;
struct AddonMgr {bool GetAddon(const std::string&, ADDON::AddonPtr& p, ADDON::AddonType, OnlyEnabled){p=CachedClient();return true;}
 bool GetAddon(const std::string&, ADDON::AddonPtr& p, OnlyEnabled){p=std::make_shared<CGameClient>();return true;}};
struct Messenger {int fullscreen=0;void PostMsg(int){++fullscreen;}};
struct CServiceBroker {static AddonMgr& GetAddonMgr(){static AddonMgr a;return a;}
 static int& GetDataCacheCore(){static int cache=0;return cache;} static void* GetPeripherals(){return nullptr;}
 static Messenger* GetAppMessenger(){static Messenger m;return &m;}};
// A savestate of another game client asks before the open continues.
static bool otherClientSave=false;
struct ISavestate {std::string GameClientID(){return otherClientSave?"game.other":"game.test";}};
struct CSavestateDatabase {static std::unique_ptr<ISavestate> AllocateSavestate(){return std::make_unique<ISavestate>();}
 bool GetSavestate(const std::string&, ISavestate&){return otherClientSave;}};
static std::function<bool()> clientDialog;
namespace GAME {struct CGameUtils {static bool FillInGameClient(CFileItem&,std::string&){
 Wait(SELECTION);return clientDialog?std::exchange(clientDialog,nullptr)():true;}};}
struct CRetroPlayer {
 Callback& m_callback;GameServices m_gameServices;CCriticalSection m_mutex;@MEMBERS@
 std::unique_ptr<CRPProcessInfo> m_processInfo;std::unique_ptr<CGUIGameMessenger> m_guiMessenger;
 std::unique_ptr<CRPRenderManager> m_renderManager;std::unique_ptr<CRPStreamManager> m_streamManager;
 std::unique_ptr<CRetroPlayerInput> m_input;std::unique_ptr<Dummy> m_playback;
 std::shared_ptr<CGameClient> m_gameClient;std::shared_ptr<CCheevos> m_cheevos;
 std::unique_ptr<CGUIPlaybackControl> m_playbackControl;std::unique_ptr<CRetroPlayerAutoSave> m_autoSave;
 explicit CRetroPlayer(Callback& c):m_callback(c){}
 ~CRetroPlayer(){CloseFile();}
 bool OpenFile(const CFileItem&,const CPlayerOptions&);bool OpenItem(const CFileItem&,const CPlayerOptions&);
 bool OpenCancelled();bool CloseFile(bool=false);void CloseItem();
 bool IsPlaying()const{return m_gameClient&&m_gameClient->playing;}
 void PrintGameInfo(const CFileItem&){} void RegisterWindowCallbacks(){}void UnregisterWindowCallbacks(){}
 void CreatePlayback(const std::string&){Wait(PLAYBACK);assert(m_gameClient);m_playback=std::make_unique<Dummy>();}
};
'''

MAIN = r'''
static void reset(){gameOpens=gameCloses=savestates=questions=0;otherClientSave=false;waitStep=-1;waitHook=nullptr;
 startedHook=nullptr;CachedClient()=std::make_shared<CGameClient>();CServiceBroker::GetAppMessenger()->fullscreen=0;}
int main(){
 // A newer item retires the player during each step of the open that can wait
 // in a dialog, and during the start of the game (STEPS). The close comes from
 // the dialog on the thread of the open, or from another thread. The close is
 // incomplete until the open returns; the open then releases the item: the
 // game client closes, and a game that never started reports no end.
 for(int thread=0;thread<2;++thread)
 for(int step=0;step<=STEPS;++step){reset();Callback cb;const bool started=step==STEPS;
  {CRetroPlayer player(cb);int closes=0;
   const auto close=[&]{bool done=true;
    if(thread){std::thread other([&]{done=player.CloseFile();});other.join();}else done=player.CloseFile();
    assert(!done);++closes;};
   // A savestate of another game client asks the question before the open continues.
   otherClientSave=true;
   if(started)startedHook=[&]{close();};else{waitStep=step;waitHook=[&]{close();close();};}
   assert(!player.OpenFile(CFileItem{},CPlayerOptions{}));
   assert(closes==(started?1:2)&&player.CloseFile());
   assert(cb.started==started&&cb.ended==started&&!player.IsPlaying()&&savestates==0);
   assert(!player.m_gameClient&&!player.m_processInfo&&!player.m_playback&&!player.m_input);
   assert(gameCloses==(step==SELECTION?0:1)&&CServiceBroker::GetAppMessenger()->fullscreen==started);
   // A cancelled open asks no question
   assert(questions==(step>=SAVESTATE_QUESTION));
  }
  // the destructor reports no second end
  assert(cb.ended==started);
 }
 // An open without a close plays; the close reports the end once.
 {reset();Callback cb;
  {CRetroPlayer player(cb);
   assert(player.OpenFile(CFileItem{},CPlayerOptions{})&&cb.started==1&&player.IsPlaying());
   assert(player.CloseFile()&&cb.ended==1&&!player.IsPlaying()&&gameCloses==1&&!player.m_gameClient);
   assert(player.CloseFile()&&cb.ended==1);
  }
  assert(cb.ended==1);
 }
 // A declined savestate question and a cancelled game client selection fail
 // the open; the game never started, and the close reports no end.
 for(int dialog=0;dialog<2;++dialog){reset();Callback cb;
  {CRetroPlayer player(cb);
   if(dialog==0)clientDialog=[]{return false;};else{otherClientSave=true;savestateDialog=[]{return false;};}
   assert(!player.OpenFile(CFileItem{},CPlayerOptions{})&&cb.started==0&&cb.ended==0);
  }
  assert(cb.ended==0);
 }
 // A player that never opened reports no end.
 {reset();Callback cb;{CRetroPlayer player(cb);}assert(cb.ended==0);}
}
'''

# The render manager of the games is shared, and the unregistration has no
# owner. The router opens the newer game after the close of the retired player
# completed: the newer game keeps the game client, the render callbacks and the
# render targets after the retired player ends.
RENDER_MANAGER = r'''
struct Dummy; struct CRetroPlayer;
using CGUIRenderTargetFactory=Dummy; using IRenderCallback=Dummy; using IGameCallback=CRetroPlayer;
struct CGUIRenderHandle {}; struct CGUIRenderTarget {};
struct CGUIGameRenderManager {
 CCriticalSection m_targetMutex,m_callbackMutex,m_gameCallbackMutex;
 CGUIRenderTargetFactory* m_factory=nullptr; IRenderCallback* m_callback=nullptr;
 IGameCallback* m_gameCallback=nullptr;
 std::map<CGUIRenderHandle*,std::shared_ptr<CGUIRenderTarget>> m_renderTargets;
 void RegisterPlayer(CGUIRenderTargetFactory*,IRenderCallback*,IGameCallback*);
 void UnregisterPlayer(); void UpdateRenderTargets(); bool IsPlayingGame();
 CGUIRenderTarget* CreateRenderTarget(CGUIRenderHandle*){return new CGUIRenderTarget;}
};
static CGUIGameRenderManager sharedRenderer;
'''

RENDER_MAIN = r'''
int main(){
 for(int step=0;step<STEPS;++step){
  Callback oldCb,newCb;savestates=0;CachedClient()=std::make_shared<CGameClient>();
  auto old=std::make_unique<CRetroPlayer>(oldCb);
  auto newer=std::make_unique<CRetroPlayer>(newCb);
  CGUIRenderHandle handle;
  sharedRenderer.m_renderTargets.emplace(&handle,nullptr);
  otherClientSave=step==SAVESTATE_QUESTION;waitStep=step;waitHook=[&]{assert(!old->CloseFile());};
  assert(!old->OpenFile(CFileItem{},CPlayerOptions{}));
  assert(old->CloseFile());
  otherClientSave=false;
  assert(newer->OpenFile(CFileItem{},CPlayerOptions{}));
  assert(sharedRenderer.m_gameCallback==newer.get()&&sharedRenderer.m_renderTargets.at(&handle));
  old.reset();
  assert(oldCb.started==0&&oldCb.ended==0&&savestates==0);
  assert(newer->IsPlaying()&&CachedClient()->loaded&&newCb.started==1&&newCb.ended==0);
  assert(sharedRenderer.IsPlayingGame()&&sharedRenderer.m_gameCallback==newer.get());
  assert(sharedRenderer.m_renderTargets.at(&handle));
  newer.reset();
  assert(newCb.ended==1&&savestates==1&&!sharedRenderer.IsPlayingGame());
  sharedRenderer.m_renderTargets.clear();
 }
 std::cout<<"RetroPlayer render callbacks: PASS (production registration; ASan/UBSan)\n";
}
'''


def compile_run(code):
    with tempfile.TemporaryDirectory(prefix='retroplayer-open-close-') as tmp:
        out = Path(tmp)
        (out / 'test.cpp').write_text(code)
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-Wno-misleading-indentation', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        '-fno-omit-frame-pointer', '-pthread', str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
        subprocess.run([str(out / 'test')], check=True, timeout=15)


def main():
    source = (ROOT / 'xbmc/cores/RetroPlayer/RetroPlayer.cpp').read_text()
    header = (ROOT / 'xbmc/cores/RetroPlayer/RetroPlayer.h').read_text()
    # The production state members of the open and the close.
    members = header[header.index('  // Synchronization parameters'):header.index('};\n} // namespace RETRO')]
    members = members.replace('  CCriticalSection m_mutex;\n', '')
    prelude = PRELUDE.replace('@MEMBERS@', members)
    player = ''.join(function(source, signature) for signature in (
        'bool CRetroPlayer::OpenFile(', 'bool CRetroPlayer::OpenItem(', 'bool CRetroPlayer::OpenCancelled()',
        'bool CRetroPlayer::CloseFile(', 'void CRetroPlayer::CloseItem()'))
    compile_run(prelude + player + MAIN)
    # The production registration of the window callbacks and the render manager.
    manager = (ROOT / 'xbmc/cores/RetroPlayer/guibridge/GUIGameRenderManager.cpp').read_text()
    render = prelude.replace('#include <atomic>', '#include <atomic>\n#include <map>', 1)
    render = render.replace('struct GameServices {', RENDER_MANAGER +
                            'struct GameServices {CGUIGameRenderManager& GameRenderManager(){return sharedRenderer;}', 1)
    render = render.replace('struct Dummy {', 'struct Dummy {Dummy* GetGUIRenderTargetFactory(){return this;}', 1)
    render = render.replace('void RegisterWindowCallbacks(){}void UnregisterWindowCallbacks(){}',
                            'void RegisterWindowCallbacks();void UnregisterWindowCallbacks();', 1)
    # The production constructor creates the playback (ResetPlayback), and the close saves the game.
    render = render.replace('explicit CRetroPlayer(Callback& c):m_callback(c){}',
                            'explicit CRetroPlayer(Callback& c):m_callback(c){m_playback=std::make_unique<Dummy>();}', 1)
    render = render.replace('bool AutosaveEnabled(){return false;}', 'bool AutosaveEnabled(){return true;}', 1)
    for signature in ('void CGUIGameRenderManager::RegisterPlayer(', 'void CGUIGameRenderManager::UnregisterPlayer()',
                      'void CGUIGameRenderManager::UpdateRenderTargets()', 'bool CGUIGameRenderManager::IsPlayingGame()'):
        render += function(manager, signature)
    for signature in ('void CRetroPlayer::RegisterWindowCallbacks()', 'void CRetroPlayer::UnregisterWindowCallbacks()'):
        render += function(source, signature)
    compile_run(render + player + RENDER_MAIN)
    print('RetroPlayer open/close: PASS (production OpenFile, CloseFile and render registration; ASan/UBSan)')


if __name__ == '__main__':
    sys.exit(main())
