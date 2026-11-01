#!/usr/bin/env python3
"""RetroPlayer open and close with production methods and recording stubs.

A newer item can retire the game player while the open of the game waits in a
dialog (the game client selection with the emulator install, the savestate
question): the router closes the player, and the open of the newer item can
complete before the dialog returns. The open of the game then starts nothing.
The close reports the end of an item once, so the close in the destructor of
the retired player reports no late end that stops the newer item.
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
#include <utility>
using CCriticalSection = std::recursive_mutex;
constexpr int LOGINFO=0, LOGERROR=1, LOGDEBUG=2, TMSG_SWITCHTOFULLSCREEN=3;
struct CLog {template<class... A> static void Log(A&&...) {}};
struct CURL {static std::string GetRedacted(const std::string& s){return s;}};
struct StringUtils {template<class... A> static std::string Format(A&&...){return {};}};
struct Localize {std::string Get(int){return {};}} g_localizeStrings;
static std::function<bool()> savestateDialog;
struct CGUIDialogYesNo {template<class... A> static bool ShowAndGetInput(A&&...){
 return savestateDialog?std::exchange(savestateDialog,nullptr)():true;}};
struct GameTag {std::string GetGameClient()const{return "game.test";}};
struct CFileItem {std::string GetPath()const{return "game.rom";} GameTag* GetGameInfoTag(){static GameTag g;return &g;}};
struct CPlayerOptions {};
struct Callback {int started=0,ended=0;void OnPlayBackStarted(const CFileItem&){++started;}
 void OnAVStarted(const CFileItem&){} void OnPlayBackEnded(){++ended;}};
struct GameSettings {bool AutosaveEnabled(){return false;} std::string GetRAUsername(){return {};}
 std::string GetRAToken(){return {};}};
struct GameServices {::GameSettings& GameSettings(){static ::GameSettings s;return s;}};
struct Dummy {template<class... A> explicit Dummy(A&&...){} Dummy()=default;void ResetInfo(){}
 void SetDataCache(void*){} void SetVideoFps(float){} void StartAgentManager(){} void StopAgentManager(){}
 std::string CreateSavestate(bool){return {};} void EnableRichPresence(){} };
struct CRPProcessInfo: Dummy {static std::unique_ptr<CRPProcessInfo> CreateInstance(){return std::make_unique<CRPProcessInfo>();}};
using CGUIGameMessenger=Dummy;using CRPRenderManager=Dummy;using CRPStreamManager=Dummy;
using CRetroPlayerInput=Dummy;using CCheevos=Dummy;using CGUIPlaybackControl=Dummy;using CRetroPlayerAutoSave=Dummy;
static int gameOpens=0,gameCloses=0;
struct CGameClient {bool playing=false;bool Initialize(){return true;}
 template<class... A> bool OpenFile(A&&...){++gameOpens;playing=true;return true;}
 template<class... A> bool OpenStandalone(A&&...){++gameOpens;playing=true;return true;}
 std::string ID(){return "game.test";}std::string Name(){return "test";}float GetFrameRate(){return 60;}
 void CloseFile(){++gameCloses;playing=false;}void Unload(){}
};
namespace ADDON {using AddonPtr=std::shared_ptr<CGameClient>;enum class AddonType{GAMEDLL};enum class OnlyEnabled{CHOICE_YES};}
using ADDON::OnlyEnabled;
struct AddonMgr {bool GetAddon(const std::string&, ADDON::AddonPtr& p, ADDON::AddonType, OnlyEnabled){p=std::make_shared<CGameClient>();return true;}
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
 return clientDialog?std::exchange(clientDialog,nullptr)():true;}};}
struct CRetroPlayer {
 Callback& m_callback;GameServices m_gameServices;CCriticalSection m_mutex;@MEMBERS@
 std::unique_ptr<CRPProcessInfo> m_processInfo;std::unique_ptr<CGUIGameMessenger> m_guiMessenger;
 std::unique_ptr<CRPRenderManager> m_renderManager;std::unique_ptr<CRPStreamManager> m_streamManager;
 std::unique_ptr<CRetroPlayerInput> m_input;std::unique_ptr<Dummy> m_playback;
 std::shared_ptr<CGameClient> m_gameClient;std::shared_ptr<CCheevos> m_cheevos;
 std::unique_ptr<CGUIPlaybackControl> m_playbackControl;std::unique_ptr<CRetroPlayerAutoSave> m_autoSave;
 explicit CRetroPlayer(Callback& c):m_callback(c){}
 ~CRetroPlayer(){CloseFile();}
 bool OpenFile(const CFileItem&,const CPlayerOptions&);bool CloseFile(bool=false);
 bool IsPlaying()const{return m_gameClient&&m_gameClient->playing;}
 void PrintGameInfo(const CFileItem&){} void RegisterWindowCallbacks(){}void UnregisterWindowCallbacks(){}
 void CreatePlayback(const std::string&){assert(m_gameClient);m_playback=std::make_unique<Dummy>();}
};
'''

MAIN = r'''
static void reset(){gameOpens=gameCloses=0;otherClientSave=false;CServiceBroker::GetAppMessenger()->fullscreen=0;}
int main(){
 // A newer item retires the player during the game client selection (the
 // emulator install), and during the savestate question.
 for(int dialog=0;dialog<2;++dialog){reset();Callback cb;
  {CRetroPlayer player(cb);bool closed=false;
   const auto retire=[&]{assert(player.CloseFile());closed=true;return true;};
   if(dialog==0)clientDialog=retire;else{otherClientSave=true;savestateDialog=retire;}
   assert(!player.OpenFile(CFileItem{},CPlayerOptions{}));
   assert(closed&&cb.started==0&&cb.ended==1&&!player.IsPlaying());
   assert(CServiceBroker::GetAppMessenger()->fullscreen==0);
  }
  // the destructor of the retired player reports no second end
  assert(cb.ended==1);
 }
 // An open without a close plays; the close reports the end once.
 {reset();Callback cb;
  {CRetroPlayer player(cb);
   assert(player.OpenFile(CFileItem{},CPlayerOptions{})&&cb.started==1&&player.IsPlaying());
   assert(player.CloseFile()&&cb.ended==1&&!player.IsPlaying()&&gameCloses==1);
   assert(player.CloseFile()&&cb.ended==1);
  }
  assert(cb.ended==1);
 }
 // A declined savestate question and a cancelled game client selection fail
 // the open; the close of the player still reports the end, as upstream.
 for(int dialog=0;dialog<2;++dialog){reset();Callback cb;
  {CRetroPlayer player(cb);
   if(dialog==0)clientDialog=[]{return false;};else{otherClientSave=true;savestateDialog=[]{return false;};}
   assert(!player.OpenFile(CFileItem{},CPlayerOptions{})&&cb.started==0&&cb.ended==0);
  }
  assert(cb.ended==1);
 }
 // A player that never opened reports no end.
 {reset();Callback cb;{CRetroPlayer player(cb);}assert(cb.ended==0);}
 std::cout<<"RetroPlayer open/close: PASS (production OpenFile and CloseFile; ASan/UBSan)\n";
}
'''


def main():
    source = (ROOT / 'xbmc/cores/RetroPlayer/RetroPlayer.cpp').read_text()
    header = (ROOT / 'xbmc/cores/RetroPlayer/RetroPlayer.h').read_text()
    # The production state members of the open and the close.
    members = header[header.index('  // Synchronization parameters'):header.index('};\n} // namespace RETRO')]
    members = members.replace('  CCriticalSection m_mutex;\n', '')
    code = PRELUDE.replace('@MEMBERS@', members) + function(source, 'bool CRetroPlayer::OpenFile(') + \
        function(source, 'bool CRetroPlayer::CloseFile(') + MAIN
    with tempfile.TemporaryDirectory(prefix='retroplayer-open-close-') as tmp:
        out = Path(tmp)
        (out / 'test.cpp').write_text(code)
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-Wno-misleading-indentation', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
                        str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
        subprocess.run([str(out / 'test')], check=True, timeout=15)


if __name__ == '__main__':
    sys.exit(main())
