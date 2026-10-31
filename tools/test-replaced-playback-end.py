#!/usr/bin/env python3
"""Execute the production end and stop handlers while a newer open waits for the old player.

The end or stop notification of the replaced playback arrives after the newer
open (the end callback waits for the video database). The notification must
not continue the stack, the playlist or EPG autoplay, or close the player,
because each of those drops or replaces the newer open. Recording boundaries
stand in for PVR, announcements, GUI and media players.
"""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
autoplay = runpy.run_path(str(ROOT / 'tools/test-autoplay-fullscreen.py'))
function = autoplay['function']
PRELUDE = autoplay['PRELUDE'].replace(
    'struct CFileItem {',
    'struct CFileItem {struct Prop{bool v=false;bool asBoolean(bool)const{return v;}};'
    'bool epg=false;Prop GetProperty(const char*)const{return {epg};}')
# A backend open that processes messages (UPnP) runs the hook inside the open.
PRELUDE = PRELUDE.replace('struct IPlayer {', 'static std::function<void()> backendWait;\nstruct IPlayer {', 1)
PRELUDE = PRELUDE.replace('if(failOpen)return false;playing=true;return true;',
                          'if(failOpen)return false;if(backendWait){auto wait=std::move(backendWait);wait();}'
                          'playing=true;return true;', 1)
HARNESS = autoplay['HARNESS']
HARNESS = HARNESS[:HARNESS.index('static void ended(')]
HARNESS = HARNESS.replace(
    ' void SendThreadMessage(CGUIMessage&){}',
    ' void SendMessage(CGUIMessage&){}int threadMessages=0;void SendThreadMessage(CGUIMessage&){++threadMessages;}')
HARNESS = HARNESS.replace(
    'struct CApplicationStackHelper {int clears=0;void Clear(){++clears;}};',
    'struct CApplicationStackHelper {int clears=0;bool regular=false,next=false;void Clear(){++clears;}'
    'bool IsPlayingRegularStack(){return regular;}bool HasNextStackPartFileItem(){return next;}'
    'CFileItem SetNextStackPartCurrentFileItem(){CFileItem part;part.id=77;return part;}};')
HARNESS = HARNESS.replace(
    'struct CApplication;',
    'struct CApplication;\n'
    'constexpr int GUI_MSG_PLAYBACK_ENDED=8,GUI_MSG_PLAYBACK_STOPPED=9;\n'
    'namespace ANNOUNCEMENT {constexpr int Player=0;}\n'
    'struct CVariant {enum {VariantTypeObject};CVariant(int){}'
    'CVariant& operator[](const char*){return *this;}CVariant& operator=(bool){return *this;}};\n'
    'struct PVR {int ended=0,stopped=0;void OnPlaybackEnded(const CFileItem&){++ended;}'
    'void OnPlaybackStopped(const CFileItem&){++stopped;}};\n'
    'struct Announcements {int stops=0;template<class... A>void Announce(A&&...){++stops;}};')
HARNESS = HARNESS.replace(
    ' static inline Params params;',
    ' static inline Params params;static inline PVR pvr;static inline Announcements announcements;\n'
    ' static PVR& GetPVRManager(){return pvr;}static Announcements* GetAnnouncementManager(){return &announcements;}\n')
HARNESS = HARNESS.replace(
    'struct CGUIMessage {CGUIMessage(int,int,int,int,int){}};',
    'struct CGUIMessage {int id;CGUIMessage(int id,int,int,int=0,int=0):id(id){}int GetMessage()const{return id;}};')
HARNESS = HARNESS.replace('struct Party {', 'struct Party {bool IsEnabled()const{return false;}')
# The playlist state, its reset, its next index and its terminal handlers are production code.
PLAYLIST_PLAYER = HARNESS[HARNESS.index('struct CPlayListPlayer {'):HARNESS.index('struct CServiceBroker {')]
HARNESS = HARNESS.replace(PLAYLIST_PLAYER, r"""constexpr int GUI_MSG_PLAYLIST_CHANGED=10;
struct Cell {int value;Cell(int v=0):value(v){}int load()const{return value;}operator int()const{return value;}
 Cell& operator=(int v){value=v;return *this;}};
struct CUtil {static void ClearSubtitles(){}};
struct CPlayListPlayer {
 bool m_bIsDeferredPlayPending=false;void StopMessage(int);void MediaPlay(CFileItem*,int);
 Cell m_iCurrentPlayList=TYPE_VIDEO,m_iCurrentSong=0;bool m_bPlayedFirstFile=true,m_bPlaybackStarted=true;
 int selected=-1;CPlayList list;CApplication* app=nullptr;
 int GetNextItemIdx(int)const;const CPlayList& GetPlaylist(int)const{return list;}
 bool RepeatedOne(int)const{return false;}bool Repeated(int)const{return false;}
 void Reset();void ResetCurrentPlaylist();void SetCurrentPlaylist(int);int GetCurrentPlaylist(){return m_iCurrentPlayList;}
 bool Terminal(int);bool Play(int,const std::string&,bool);bool PlayNext(int offset=1,bool bAutoPlay=true);
};
""")
HARNESS = HARNESS.replace(
    'bool CPlayListPlayer::Play(int,const std::string& name,bool){',
    'bool CPlayListPlayer::Play(int index,const std::string& name,bool){\n'
    ' selected=index;m_iCurrentSong=index;m_bPlaybackStarted=false;')
HARNESS = HARNESS.replace(
    ' void Frame(){',
    ' struct {void Set(){}} m_playerEvent;int stackPlays=0,m_nextPlaylistItem=0;\n'
    ' void Intent(CFileItem item,bool bRestart,[[maybe_unused]] bool playlistItem);\n'
    ' std::function<void()> nested;void Accept(bool bRestart,bool playlistItem);\n'
    ' bool PlayFile(CFileItem item,const std::string& player,bool bRestart=false,bool playlistItem=false,uint64_t attempt=0);bool abandon=false;\n'
    ' bool StartPlayback(CFileItem item,const std::string& name,bool bRestart,bool playlistItem,uint64_t intent){\n'
    '  ++stackPlays;Intent(item,bRestart,playlistItem);if(nested){auto open=std::move(nested);open();}\n'
    '  if(abandon)return false;\n'
    '  const auto appPlayer=&player;@CANCELLED@\n'
    '  const bool ret=player.OpenFile(item,{},services.factory,name,callback,intent);@CANCELLED@Accept(bRestart,playlistItem);return ret;}\n'
    ' bool Ended();void EndOpen(uint64_t intent);\n void Frame(){')
PRELUDE = PRELUDE.replace('struct CApplicationPlayer {', 'struct CApplicationPlayer {void SetPlaySpeed(float){}')
TESTS = r"""
@PLAYLISTFUNCTIONS@
bool CApplication::PlayFile(CFileItem item,const std::string& player,bool bRestart,bool playlistItem,uint64_t attempt){@PLAYFILE@}
@ENDOPEN@
bool CApplication::Ended(){switch(GUI_MSG_PLAYBACK_ENDED){@ENDED@}return false;}
bool CPlayListPlayer::Terminal(int id){CGUIMessage message(id,0,0);switch(message.GetMessage()){@PLAYLISTTERMINAL@}return false;}
void CApplication::Intent(CFileItem item,bool bRestart,bool playlistItem){
 const auto appPlayer=&player;const auto stackHelper=&stack;@ENDPLAYLIST@@INTENT@}
// The open repeats the reset once accepted, after a dialog or a resolution.
void CApplication::Accept(bool bRestart,bool playlistItem){@ENDPLAYLIST@ endPlaylist();}
void CPlayListPlayer::MediaPlay(CFileItem* payload,int restart){
 auto& g_application=*app;struct {void* lpVoid;int param1,param2;} message{payload,restart,0};auto* pMsg=&message;
 @MEDIAPLAY@}
static void reset(){CServiceBroker::pvr={};CServiceBroker::announcements={};}
// The old playback has ended, and the player still closes when the user opens a newer file.
static void replace(CApplication& app){
 CFileItem latest;latest.id=42;
 assert(app.player.OpenFile(latest,{},app.services.factory,"old",app.callback));
 assert(app.player.HasPendingOpen()&&app.player.m_closingPlayer);
}
static void opensLatest(CApplication& app,const std::shared_ptr<IPlayer>& old){
 old->canClose=true;app.Frame();
 assert(app.player.created==1&&app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==42);
}
int main(){
 // The end of the replaced playback: no playlist, stack, EPG autoplay or close.
 for(int kind:{0,1,2}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;
  app.m_itemCurrentFile->epg=kind==1;app.stack.regular=app.stack.next=kind==2;
  replace(app);app.Ended();
  assert(app.player.HasPendingOpen()&&app.player.m_nextItem.pItem->id==42);
  assert(app.stackPlays==0&&CServiceBroker::pvr.ended==0&&CServiceBroker::pvr.stopped==1);
  assert(CServiceBroker::announcements.stops==1);
  opensLatest(app,old);
 }
 // A normal end still continues the playlist and the EPG autoplay.
 {CApplication app;begin(app);reset();app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
  assert(CServiceBroker::pvr.ended==1&&CServiceBroker::pvr.stopped==0);
  assert(app.player.HasPendingOpen()&&app.player.m_nextItem.pItem->id==9);
  assert(CServiceBroker::playlist.GetCurrentPlaylist()==TYPE_VIDEO&&CServiceBroker::gui.wm.threadMessages==0);}
 {CApplication app;begin(app);reset();app.stack.regular=app.stack.next=true;app.Ended();
  assert(app.stackPlays==1);}
 // The stop of the replaced playback does not stop the playlist under the newer open.
 {CApplication app;begin(app);auto old=app.player.m_pPlayer;CFileItem latest;latest.id=42;
  app.Intent(latest,true,false);replace(app);
  app.player.OnPlaybackStopped();
  CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_STOPPED);
  const int messages=CServiceBroker::gui.wm.threadMessages;
  if(messages)app.PlaylistStopped();
  assert(messages==0&&CServiceBroker::playlist.m_iCurrentPlayList!=TYPE_NONE);
  assert(app.player.HasPendingOpen());opensLatest(app,old);
 }
 // An explicit stop still ends the playlist and the pending open.
 {CApplication app;begin(app);auto old=app.player.m_pPlayer;replace(app);app.StopPlaying();
  assert(!app.player.HasPendingOpen());app.player.OnPlaybackStopped();
  assert(CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_STOPPED));
  assert(CServiceBroker::gui.wm.threadMessages==1&&CServiceBroker::playlist.m_iCurrentPlayList==TYPE_NONE);
  old->canClose=true;app.Frame();assert(app.player.created==0);
 }
 // A standalone open ends the playlist when the open starts. Neither the end or
 // stop of the replaced playback nor the end of the standalone file continues
 // the playlist, and the playlist stop does not drop the standalone open.
 for(bool oldEnded:{false,true}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;
  CFileItem latest;latest.id=42;app.Intent(latest,false,false);
  assert(CServiceBroker::playlist.GetCurrentPlaylist()==TYPE_NONE);
  replace(app);
  if(oldEnded){app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);}
  else{app.player.OnPlaybackStopped();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_STOPPED);}
  assert(CServiceBroker::gui.wm.threadMessages==0&&app.player.HasPendingOpen());
  opensLatest(app,old);
  app.player.m_pPlayer->playing=false;app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
  assert(CServiceBroker::playlist.selected==-1&&!app.player.HasPendingOpen());
 }
 // The playlist's own open and a restart keep the playlist and its position.
 for(bool restart:{false,true}){CApplication app;begin(app);CServiceBroker::playlist.m_iCurrentSong=1;
  CFileItem item;item.id=9;app.Intent(item,restart,!restart);
  assert(CServiceBroker::playlist.GetCurrentPlaylist()==TYPE_VIDEO&&CServiceBroker::playlist.m_iCurrentSong==1);
 }
 // A single-item media message ends the playlist. A stack part seek uses the
 // same message as a restart and keeps the playlist position.
 for(int restart:{0,1}){CApplication app;begin(app);CServiceBroker::playlist.m_iCurrentSong=1;
  auto* item=new CFileItem;item->id=50;CServiceBroker::playlist.MediaPlay(item,restart);
  assert(CServiceBroker::playlist.GetCurrentPlaylist()==(restart?TYPE_VIDEO:TYPE_NONE));
  assert(!restart||CServiceBroker::playlist.m_iCurrentSong==1);
  assert(app.stack.clears==(restart?0:1));
  assert(app.player.HasPendingOpen()&&app.player.m_nextItem.pItem->id==50);
 }
 // An explicit stop after the old playback reached the end, also after a newer
 // open: the late end does not continue the stack, the playlist or the EPG
 // autoplay, and ends the playlist like a stop.
 for(int kind:{0,1,2})for(bool replaced:{false,true}){CApplication app;begin(app);reset();
  auto old=app.player.m_pPlayer;
  app.m_itemCurrentFile->epg=kind==1;app.stack.regular=app.stack.next=kind==2;
  if(replaced)replace(app);
  app.StopPlaying();assert(!app.player.HasPendingOpen());
  app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
  assert(!app.player.HasPendingOpen()&&app.stackPlays==0&&CServiceBroker::playlist.selected==-1);
  assert(CServiceBroker::pvr.ended==0&&CServiceBroker::pvr.stopped==1);
  assert(CServiceBroker::playlist.GetCurrentPlaylist()==TYPE_NONE&&CServiceBroker::gui.wm.threadMessages==1);
  old->canClose=true;app.Frame();assert(app.player.created==0&&!app.player.HasPlayer());
 }
 // An explicit stop during the open of a playlist item ends the playlist.
 {CApplication app;begin(app);
  assert(CServiceBroker::playlist.PlayNext(1,false)&&app.player.HasPendingOpen());
  app.StopPlaying();app.player.OnPlaybackStopped();
  assert(CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_STOPPED));
  assert(CServiceBroker::playlist.GetCurrentPlaylist()==TYPE_NONE&&CServiceBroker::gui.wm.threadMessages==1);
 }
 // A newer open after the stop wins: the late end keeps the newer open and the playlist.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;
  app.StopPlaying();assert(CServiceBroker::playlist.PlayNext(1,false));
  app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
  assert(app.player.HasPendingOpen()&&app.player.m_nextItem.pItem->id==9&&CServiceBroker::pvr.stopped==1);
  assert(CServiceBroker::playlist.GetCurrentPlaylist()==TYPE_VIDEO&&CServiceBroker::gui.wm.threadMessages==0);
  old->canClose=true;app.Frame();assert(app.player.created==1&&app.player.m_pPlayer->lastItem==9);
 }
 // An earlier stop does not affect the end of a later playback.
 {CApplication app;begin(app);auto old=app.player.m_pPlayer;app.StopPlaying();app.player.OnPlaybackStopped();
  CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_STOPPED);old->canClose=true;app.Frame();
  CServiceBroker::playlist.SetCurrentPlaylist(TYPE_VIDEO);assert(CServiceBroker::playlist.Play(0,"old",false));
  assert(app.player.IsPlaying());app.player.m_pPlayer->playing=false;app.Ended();
  assert(CServiceBroker::playlist.selected==1&&app.player.HasPendingOpen());
 }
 // A playlist open nested in the resolution or a dialog of a standalone open
 // is newer and supersedes the standalone open: the playlist item and the
 // playlist stay.
 {CApplication app;begin(app);reset();auto& playlist=CServiceBroker::playlist;
  app.nested=[&]{playlist.SetCurrentPlaylist(TYPE_VIDEO);playlist.m_iCurrentSong=0;
   CFileItem nested;nested.id=30;assert(app.PlayFile(nested,"old",false,true));
   assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO);};
  CFileItem standalone;standalone.id=42;assert(!app.PlayFile(standalone,"old"));
  assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&app.player.m_nextItem.pItem->id==30);
 }
 // A stop after the end queued the next playlist item: no notification
 // remains, so the stop ends the playlist and leaves the visualisation.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;auto& playlist=CServiceBroker::playlist;
  playlist.SetCurrentPlaylist(TYPE_MUSIC);CServiceBroker::gui.wm.active=WINDOW_VISUALISATION;old->hasAudio=true;
  app.Ended();playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
  assert(app.player.HasPendingOpen()&&!app.player.m_waitForPlaybackStop);
  app.StopPlaying();assert(playlist.GetCurrentPlaylist()==TYPE_NONE);
  old->canClose=true;app.Frame();app.PlaybackCleanup();
  assert(!app.player.HasPlayer()&&!app.player.HasPendingOpen()&&app.player.created==0);
  assert(CServiceBroker::gui.wm.active==HOME);
 }
 // A stop, then a playlist item whose resolution processes the late end or stop
 // of the stopped playback: the newer open supersedes the stop, so the
 // playlist stays for the item and continues after the item.
 for(bool oldEnded:{false,true}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;
  auto& playlist=CServiceBroker::playlist;app.StopPlaying();
  app.nested=[&]{if(oldEnded)app.Ended();else app.player.OnPlaybackStopped();
   playlist.Terminal(oldEnded?GUI_MSG_PLAYBACK_ENDED:GUI_MSG_PLAYBACK_STOPPED);};
  playlist.m_iCurrentSong=0;playlist.m_bPlaybackStarted=false;
  CFileItem item;item.id=60;assert(app.PlayFile(item,"old",false,true));
  assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&CServiceBroker::gui.wm.threadMessages==0);
  assert(app.stackPlays==1&&CServiceBroker::pvr.ended==0&&app.player.m_nextItem.pItem->id==60);
  old->canClose=true;app.Frame();assert(app.player.m_pPlayer->lastItem==60);
  app.player.m_pPlayer->playing=false;app.Ended();
  assert(playlist.selected==1&&app.player.HasPendingOpen());
 }
 // A stop and its late end, then a playlist item that waits for the old player,
 // then a stop of that item: no notification remains, so the stop ends the
 // playlist and leaves the visualisation, through both stop routes.
 for(bool deferred:{false,true}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;
  auto& playlist=CServiceBroker::playlist;
  app.StopPlaying();app.Ended();playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);app.PlaylistStopped();
  playlist.SetCurrentPlaylist(TYPE_MUSIC);playlist.m_iCurrentSong=0;
  CServiceBroker::gui.wm.active=WINDOW_VISUALISATION;
  CFileItem item;item.id=60;assert(app.PlayFile(item,"old",false,true));
  assert(app.player.HasPendingOpen()&&!app.player.m_waitForPlaybackStop);
  playlist.m_bIsDeferredPlayPending=deferred;
  if(deferred)playlist.StopMessage(TYPE_NONE);else app.StopPlaying();
  assert(playlist.GetCurrentPlaylist()==TYPE_NONE);
  old->canClose=true;app.Frame();app.PlaybackCleanup();
  assert(CServiceBroker::gui.wm.active==HOME&&!app.player.HasPlayer()&&app.player.created==0);
 }
 // An open that does not reach the player, for example a cancelled Blu-ray
 // title selection, does not supersede the end of the playback that continues.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->playing=true;
  app.abandon=true;CFileItem title;title.id=70;assert(!app.PlayFile(title,"old",false,true));
  assert(!app.player.IsOpening()&&!app.player.HasPendingOpen());
  old->playing=false;app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
  assert(CServiceBroker::playlist.selected==1&&app.player.m_nextItem.pItem->id==9&&CServiceBroker::pvr.ended==1);
 }
 // The end of the previous playback during the resolution of the next playlist
 // item: the terminal cleanup waits for the open, so the item stays fullscreen.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;playlist.m_iCurrentSong=0;playlist.m_bPlaybackStarted=false;
  app.nested=[&]{app.Ended();playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);app.Frame();
   assert(app.player.IsOpening()&&gfx.fullscreen&&gfx.restores==0);};
  CFileItem next;next.id=60;assert(app.PlayFile(next,"old",false,true));
  app.Frame();assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==60);
  assert(gfx.fullscreen&&gfx.restores==0&&playlist.GetCurrentPlaylist()==TYPE_VIDEO);
 }
 // The same, but the item does not reach the player: the waiting cleanup runs.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->canClose=true;
  app.nested=[&]{app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);app.Frame();};
  app.abandon=true;CFileItem next;next.id=60;assert(!app.PlayFile(next,"old",false,true));
  app.Frame();assert(!app.player.HasPlayer()&&!gfx.fullscreen&&gfx.restores==1);
 }
 // The end of the previous playback during the resolution of an item that then
 // fails to open, or opens audio: the waiting cleanup leaves fullscreen video.
 for(int outcome:{0,1}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->canClose=true;
  app.StopPlaying();
  app.nested=[&]{app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
   app.Frame();assert(!app.player.HasPlayer()&&app.m_playbackCleanupDeferred);};
  CFileItem next;next.id=80;app.player.failCreate=outcome==0;next.video=outcome==0;
  app.PlayFile(next,"old",false,true);if(outcome==1)app.player.m_pPlayer->hasAudio=true;app.Frame();
  assert(!gfx.fullscreen&&gfx.restores==1&&!app.m_playbackCleanupDeferred);
 }
 // A nested playback that starts and ends during the resolution of a cancelled
 // open, also after a stop of the nested playback: the waiting cleanup runs.
 for(bool stop:{false,true}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;
  app.nested=[&]{CFileItem nested;nested.id=30;assert(app.PlayFile(nested,"old",false,false));
   if(stop)app.StopPlaying();
   app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);
   old->canClose=true;app.Frame();
   if(!stop){assert(app.player.IsPlaying());auto current=app.player.m_pPlayer;current->playing=false;
    current->canClose=true;app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);app.Frame();}
   assert(!app.player.HasPlayer()&&app.m_playbackCleanupDeferred);app.abandon=true;};
  CFileItem outer;outer.id=60;assert(!app.PlayFile(outer,"old",false,false));app.Frame();
  assert(!gfx.fullscreen&&gfx.restores==1&&!app.m_playbackCleanupDeferred);
 }
 // A nested video that still plays when the outer open is cancelled keeps the screen.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->canClose=true;app.StopPlaying();
  app.nested=[&]{app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);app.Frame();
   CFileItem nested;nested.id=30;assert(app.PlayFile(nested,"old",false,false));
   assert(app.player.IsPlaying()&&app.m_playbackCleanupDeferred);app.abandon=true;};
  CFileItem outer;outer.id=60;assert(!app.PlayFile(outer,"old",false,false));app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==30&&gfx.fullscreen&&gfx.restores==0);
 }
 // A stop during the resolution of an open cancels the open, with the end or
 // stop notification of the stopped playback before or after the cancel. The
 // stop ends the playlist, so the playlist does not skip to the next item.
 for(bool lateNotification:{false,true}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;
  auto& playlist=CServiceBroker::playlist;
  app.nested=[&]{app.StopPlaying();assert(playlist.GetCurrentPlaylist()==TYPE_NONE);
   if(!lateNotification){app.Ended();playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);old->canClose=true;app.Frame();}};
  CFileItem next;next.id=60;assert(!app.PlayFile(next,"old",false,true));
  assert(app.player.created==0&&!app.player.HasPendingOpen()&&!app.player.IsOpening());
  if(lateNotification){app.Ended();playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);}
  old->canClose=true;app.Frame();
  assert(!app.player.HasPlayer()&&!app.player.HasPendingOpen()&&app.player.created==0);
  assert(playlist.selected==-1&&playlist.GetCurrentPlaylist()==TYPE_NONE&&CServiceBroker::pvr.ended==0);
  assert(!gfx.fullscreen&&gfx.restores==1);
 }
 // The end of a playlist that was queued before an open, for example Next past
 // the last item, stops the old player but does not cancel the newer open.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->canClose=true;
  app.nested=[&]{app.PlaylistStopped();assert(app.player.m_closingPlayer);};
  CFileItem next;next.id=60;assert(app.PlayFile(next,"old",false,false));
  assert(app.player.HasPendingOpen()&&app.player.m_nextItem.pItem->id==60);
  app.player.OnPlaybackStopped();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==60&&gfx.fullscreen);
 }
 // The same during a newer playlist item, after the old end was acknowledged:
 // the playlist stays for the item, and the item continues the playlist.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;auto& playlist=CServiceBroker::playlist;
  playlist.m_iCurrentSong=0;playlist.m_bPlaybackStarted=false;
  app.nested=[&]{app.PlaylistStopped();
   assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&!app.player.StopRequested()&&app.player.m_closingPlayer);};
  CFileItem item;item.id=60;assert(app.PlayFile(item,"old",false,true));
  app.player.OnPlaybackStopped();playlist.Terminal(GUI_MSG_PLAYBACK_STOPPED);
  old->canClose=true;app.Frame();
  assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&app.player.m_pPlayer->lastItem==60);
  app.player.m_pPlayer->playing=false;app.Ended();
  assert(playlist.selected==1&&app.player.HasPendingOpen());
 }
 // During a deferred play, the screensaver stops its own video: that stop
 // cancels no open. A stop request through the same message does.
 for(bool deferred:{true,false}){CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;
  app.nested=[&]{playlist.m_bIsDeferredPlayPending=deferred;playlist.StopMessage(TYPE_NONE);
   playlist.m_bIsDeferredPlayPending=false;app.player.OnPlaybackStopped();app.Frame();};
  CFileItem next;next.id=60;assert(app.PlayFile(next,"old",false,false)==deferred);
  assert(deferred==(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==60));
 }
 // A stop without playback during a resolution cancels the open too. A stop
 // before an open does not cancel the open.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;app.StopPlaying();
  app.Ended();CServiceBroker::playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);old->canClose=true;app.Frame();
  assert(!app.player.HasPlayer());
  app.nested=[&]{app.StopPlaying();};CFileItem next;next.id=60;assert(!app.PlayFile(next,"old",false,false));
  assert(app.player.created==0&&!app.player.HasPlayer());
  CFileItem later;later.id=61;assert(app.PlayFile(later,"old",false,false));
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==61);
 }
 std::cout<<"PASS replaced playback end and stop keep the newer open; standalone opens end the playlist; a stop supersedes a late end; normal end, stack and explicit stop unchanged\n";
}
"""


# The production playlist Play, with its failure handling, replaces the recording stand-in.
PLAY_TESTS = r"""
static void playlist(CApplication& app){
 auto& playlist=CServiceBroker::playlist;playlist.list.items[0]->id=40;playlist.list.items[1]->id=41;(void)app;
}
int main(){
 (void)&replace;(void)&opensLatest; // The cases of the first build use the helpers.
 // A stop during the resolution of a playlist item cancels the item: the item
 // is not unplayable, and the playlist does not continue.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;
  app.nested=[&]{app.StopPlaying();app.player.OnPlaybackStopped();app.Frame();};
  assert(!playlist.Play(0,"old"));
  assert(playlist.list.unplayable==0&&playlist.GetCurrentPlaylist()==TYPE_NONE&&!app.player.HasPlayer());
 }
 // The same, but the user restarts the item from the playlist during the
 // resolution: the restart plays, and the cancelled attempt changes nothing.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;
  app.nested=[&]{app.StopPlaying();app.player.OnPlaybackStopped();app.Frame();
   playlist.SetCurrentPlaylist(TYPE_VIDEO);assert(playlist.Play(0,"old"));};
  assert(!playlist.Play(0,"old"));
  assert(playlist.list.unplayable==0&&playlist.m_iCurrentSong.load()==0);
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==40);
 }
 // A failed item without a stop is still unplayable, and the playlist continues.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;app.StopPlaying();app.player.OnPlaybackStopped();app.Frame();
  playlist.SetCurrentPlaylist(TYPE_VIDEO);app.player.failCreate=true;
  assert(!playlist.Play(0,"old"));assert(playlist.list.unplayable==2);
 }
 // The end of A starts the next item B. A stop cancels B, and the user opens C
 // during B's resolution: the end of A does not close C.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  app.nested=[&]{app.StopPlaying();app.Frame();CFileItem c;c.id=50;assert(app.PlayFile(c,"old",false,false));
   assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==50);};
  old->playing=false;app.Ended();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==50&&!app.player.m_closingPlayer);
  // C has not reported the video stream yet, and keeps fullscreen.
  assert(!app.player.m_pPlayer->hasVideo&&gfx.fullscreen&&gfx.restores==0);
 }
 // After the retries, the failure dialog: a stop and a restart of the same item
 // during the dialog. The cancelled attempt does not end the newer playlist.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;playlistRetries=1;
  app.StopPlaying();app.player.OnPlaybackStopped();app.Frame();playlist.SetCurrentPlaylist(TYPE_VIDEO);
  app.nested=[&]{app.abandon=true;};
  failureDialog=[&]{app.StopPlaying();playlist.SetCurrentPlaylist(TYPE_VIDEO);assert(playlist.Play(0,"old"));};
  assert(!playlist.Play(0,"old"));playlistRetries=100;failureDialog={};
  assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&CServiceBroker::gui.wm.threadMessages==0);
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==40);
 }
 // A newer playlist attempt of the same item supersedes the failure handling of
 // the older attempt: a restart during the failure dialog, without a stop, and a
 // restart during the resolution of an attempt that then fails.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;playlistRetries=1;playlist.SetCurrentPlaylist(TYPE_VIDEO);
  app.nested=[&]{app.abandon=true;};
  failureDialog=[&]{assert(playlist.Play(0,"old"));};
  assert(!playlist.Play(0,"old"));playlistRetries=100;failureDialog={};
  app.player.OnPlaybackStopped();app.Frame();
  assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&CServiceBroker::gui.wm.threadMessages==0);
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==40&&!app.player.m_closingPlayer);
 }
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;playlist.SetCurrentPlaylist(TYPE_VIDEO);
  app.nested=[&]{assert(playlist.Play(0,"old"));app.abandon=true;};
  assert(!playlist.Play(0,"old"));app.player.OnPlaybackStopped();app.Frame();
  assert(playlist.list.unplayable==0&&playlist.GetCurrentPlaylist()==TYPE_VIDEO);
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==40&&!app.player.m_closingPlayer);
 }
 // The end of A during the resolution of item B; B fails, and the playlist
 // continues at C: the waiting cleanup does not leave fullscreen before C.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;
  app.nested=[&]{app.Ended();playlist.Terminal(GUI_MSG_PLAYBACK_ENDED);app.Frame();
   assert(app.m_playbackCleanupDeferred);app.abandon=true;};
  playlist.SetCurrentPlaylist(TYPE_VIDEO);assert(playlist.Play(0,"old"));app.Frame();
  assert(playlist.list.unplayable==1&&app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==41);
  assert(gfx.fullscreen&&gfx.restores==0);
 }
 // A restart of the current playback (TMSG_MEDIA_RESTART) during the failure
 // dialog is a newer open: the failed attempt keeps the playlist.
 {CApplication app;begin(app);reset();playlist(app);
  auto old=app.player.m_pPlayer;old->canClose=true;old->playing=true;old->hasVideo=true;
  auto& playlist=CServiceBroker::playlist;playlistRetries=1;playlist.SetCurrentPlaylist(TYPE_VIDEO);
  app.nested=[&]{app.abandon=true;};
  failureDialog=[&]{CFileItem current;current.id=30;assert(app.PlayFile(current,"old",true));};
  assert(!playlist.Play(0,"old"));playlistRetries=100;failureDialog={};
  assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&CServiceBroker::gui.wm.threadMessages==0);
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==30&&!app.player.m_closingPlayer);
 }
 // A newer open supersedes every older open still in progress, whatever
 // started the older open: the next item that the end of A started,
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  app.nested=[&]{CFileItem y;y.id=50;assert(app.PlayFile(y,"old",false,false));app.Frame();};
  app.Ended();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==50&&!app.player.m_closingPlayer);
  assert(CServiceBroker::playlist.list.unplayable==0);
 }
 // a playlist item that the user started,
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;playlist.SetCurrentPlaylist(TYPE_VIDEO);
  app.nested=[&]{CFileItem y;y.id=50;assert(app.PlayFile(y,"old",false,false));};
  assert(!playlist.Play(0,"old"));app.player.OnPlaybackStopped();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==50&&!app.player.m_closingPlayer);
  assert(playlist.list.unplayable==0&&CServiceBroker::gui.wm.threadMessages==0);
 }
 // and an open whose backend processes messages: a stop and a newer playlist
 // during the backend open keep the newer playlist and the newer player state.
 {CApplication app;begin(app);reset();playlist(app);
  app.player.m_pPlayer.reset();app.player.m_waitForPlaybackStop=false;
  auto& playlist=CServiceBroker::playlist;std::shared_ptr<IPlayer> newer;
  backendWait=[&]{app.player.m_pPlayer->canClose=true;app.StopPlaying();assert(!app.player.HasPlayer());
   playlist.SetCurrentPlaylist(TYPE_VIDEO);assert(playlist.Play(0,"new"));newer=app.player.m_pPlayer;
   newer->hasVideo=true;app.player.OnPlaybackStopped();};
  CFileItem x;x.id=99;assert(!app.PlayFile(x,"remote",false,false));
  assert(playlist.GetCurrentPlaylist()==TYPE_VIDEO&&playlist.list.size()==2);
  assert(app.player.m_pPlayer==newer&&newer->lastItem==40&&app.player.IsPlaying());
 }
 // Without a stop, the newer open replaces the backend player that still opens.
 {CApplication app;begin(app);reset();
  app.player.m_pPlayer.reset();app.player.m_waitForPlaybackStop=false;
  backendWait=[&]{app.player.m_pPlayer->canClose=true;CFileItem y;y.id=50;
   assert(app.PlayFile(y,"old",false,false)&&app.player.HasPendingOpen());};
  CFileItem x;x.id=99;assert(!app.PlayFile(x,"remote",false,false));
  assert(app.player.HasPendingOpen());app.player.OnPlaybackStopped();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==50);
 }
 // A newer request that fails before the player does not supersede: the
 // playlist item, during the resolution of which the request failed, plays.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;playlist.SetCurrentPlaylist(TYPE_VIDEO);
  app.nested=[&]{CFileItem y;y.id=50;app.nested=[&]{app.abandon=true;};assert(!app.PlayFile(y,"old",false,true));};
  assert(playlist.Play(0,"old"));app.player.OnPlaybackStopped();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==40&&playlist.list.unplayable==0);
 }
 // A standalone request ends the playlist when the request begins, like the
 // GUI callers before PlayFile, also when the request then fails.
 {CApplication app;begin(app);reset();playlist(app);auto old=app.player.m_pPlayer;old->canClose=true;
  auto& playlist=CServiceBroker::playlist;playlist.SetCurrentPlaylist(TYPE_VIDEO);
  app.nested=[&]{CFileItem y;y.id=50;app.nested=[&]{app.abandon=true;};assert(!app.PlayFile(y,"old",false,false));};
  assert(playlist.Play(0,"old"));app.player.OnPlaybackStopped();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==40&&playlist.GetCurrentPlaylist()==TYPE_NONE);
 }
 // A queued open keeps the intent of the request and owns the player state.
 {CApplication app;begin(app);reset();auto old=app.player.m_pPlayer;old->canClose=true;old->playing=false;
  CFileItem x;x.id=60;assert(app.PlayFile(x,"old",false,false)&&app.player.HasPendingOpen());
  app.player.OnPlaybackStopped();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==60&&app.player.m_waitForPlaybackStop);
 }
 // A queued open X that opens and ends during the resolution of a newer item Y:
 // Y supersedes the end of X, and Y plays.
 {CApplication app;begin(app);reset();playlist(app);auto& playlist=CServiceBroker::playlist;
  auto z=std::make_shared<CFileItem>();z->id=42;playlist.list.items.push_back(z);
  auto old=app.player.m_pPlayer;old->playing=false;
  assert(playlist.Play(0,"old")&&app.player.HasPendingOpen());app.player.OnPlaybackStopped();
  app.nested=[&]{old->canClose=true;app.Frame();assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==40);
   app.player.m_pPlayer->playing=false;app.player.m_pPlayer->canClose=true;app.Ended();
   assert(!app.player.HasPendingOpen());};
  assert(playlist.Play(1,"old"));app.player.OnPlaybackStopped();app.Frame();
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==41);
 }
 // A stop alone during a backend open: the open writes no player state.
 {CApplication app;begin(app);reset();
  app.player.m_pPlayer.reset();app.player.m_waitForPlaybackStop=false;
  backendWait=[&]{app.player.m_pPlayer->canClose=true;app.StopPlaying();assert(!app.player.HasPlayer());};
  CFileItem x;x.id=99;assert(!app.PlayFile(x,"remote",false,false));
  assert(!app.player.HasPlayer()&&!app.player.m_waitForPlaybackStop&&!app.player.m_openedVideo);
 }
 // A newer request that fails during a backend open: the backend open plays.
 {CApplication app;begin(app);reset();
  app.player.m_pPlayer.reset();app.player.m_waitForPlaybackStop=false;
  backendWait=[&]{CFileItem y;y.id=50;app.nested=[&]{app.abandon=true;};assert(!app.PlayFile(y,"old",false,false));};
  CFileItem x;x.id=99;assert(app.PlayFile(x,"remote",false,false));
  assert(app.player.IsPlaying()&&app.player.m_pPlayer->lastItem==99&&app.player.m_waitForPlaybackStop);
 }
 std::cout<<"PASS production playlist Play: a stop cancels an item without a failure\n";
}
"""


def production_play(code, playlist, extract):
    """Swap the recording Play for the production Play and its failure handling."""
    code = code[:code.index('int main(){')]
    code = code.replace('#include <cassert>', '#include <cassert>\n#include <chrono>\n#include "' + str(ROOT / 'xbmc/utils/ScopeGuard.h') + '"')
    code = code.replace('struct CFileItem {', """struct Tag {};
struct CFileItem {
 void SetProperty(const char*,bool){}void ClearProperty(const char*){}void SetDetectorBinding(std::shared_ptr<int>){}
 bool IsVideoDb()const{return false;} bool HasVideoInfoTag()const{return false;}
 Tag tag;Tag* GetVideoInfoTag(){return &tag;}std::string GetDynPath()const{return "probe";}
 int GetStartOffset()const{return 0;}void SetStartOffset(int){}
""", 1)
    code = code.replace('struct SettingsComponent {', """static int playlistRetries=100;static std::function<void()> failureDialog;
struct CAdvancedSettings {int m_playlistRetries=playlistRetries,m_playlistTimeout=0;};
struct SettingsComponent {
 std::shared_ptr<CAdvancedSettings> GetAdvancedSettings(){return std::make_shared<CAdvancedSettings>();}
""", 1)
    old = 'struct CPlayList {int size()const{return 2;}int GetPlayable()const{return 2;}};'
    assert code.count(old) == 1
    code = code.replace(old, """using CFileItemPtr=std::shared_ptr<CFileItem>;
struct CPlayList {
 std::vector<CFileItemPtr> items{std::make_shared<CFileItem>(),std::make_shared<CFileItem>()};int unplayable=0;
 int size()const{return items.size();}int GetPlayable()const{return size()-unplayable;}
 bool Expand(int){return false;}CFileItemPtr TryGet(int n)const{return n>=0&&n<size()?items[n]:nullptr;}
 void SetPlayed(bool){}void SetUnPlayable(int){++unplayable;}void Clear(){items.clear();}
};""")
    # An abandoned resolution fails one item only
    assert code.count('  if(abandon)return false;') == 1
    code = code.replace('  if(abandon)return false;', '  if(std::exchange(abandon,false))return false;')
    code = code.replace('int selected=-1;', 'int m_iFailedSongs=0;std::chrono::steady_clock::time_point m_failedSongsStart;int selected=-1;')
    code = code.replace('const CPlayList& GetPlaylist(int)const{return list;}',
                        'const CPlayList& GetPlaylist(int)const{return list;} CPlayList& GetPlaylist(int){return list;}')
    signature = 'bool CPlayListPlayer::Play(int iSong,'
    production = extract(playlist, signature)
    declaration = production[:production.index('{')]
    optional = 'std::optional<bool>' in declaration
    code = code.replace('bool Play(int,const std::string&,bool);',
                        'bool Play(int,const std::string&,bool=false,bool=false' +
                        (',std::optional<bool> = std::nullopt' if optional else '') +
                        ');bool PlayPrevious(){return PlayNext(-1);}')
    production = production.replace('  const Id playlistId', '  auto& g_application=*app;\n  const Id playlistId', 1)
    stub = code[code.index('bool CPlayListPlayer::Play(int index,'):]
    stub = stub[:stub.index('\n}\n') + 3]
    return code.replace(stub, """constexpr int STARTOFFSET_RESUME=-1,LOGERROR=1,LOGDEBUG=2;
struct CLog {template<class... A>static void Log(A&&...){}};
struct CURL {CURL(const std::string&){} static std::string GetRedacted(const std::string& s){return s;}};
namespace XFILE {struct CVideoDatabaseFile {static Tag GetVideoTag(const CURL&){return {};}};}
namespace HELPERS {template<class... A>void ShowOKDialogText(A&&...){if(failureDialog){auto dialog=std::move(failureDialog);dialog();}}}
namespace DETECTOR_PLAYBACK {constexpr auto PROPERTY="playback";}
namespace DETECTOR_ADMISSION {constexpr auto ITEM_SKIP="skip";void CaptureItem(CFileItem&) {}}
""" + production) + PLAY_TESTS


def compile_run(code, label):
    with tempfile.TemporaryDirectory(prefix='replaced-playback-end-') as tmp:
        out = Path(tmp)
        (out / 'test.cpp').write_text(code)
        if os.environ.get('DUMP'): Path(os.environ['DUMP'] + label).write_text(code)
        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
        subprocess.run([str(out / 'test')], check=True, timeout=15)


def main():
    source = (ROOT / 'xbmc/application/ApplicationPlayer.cpp').read_text()
    signatures = ['std::shared_ptr<IPlayer> CApplicationPlayer::GetInternal()',
                  'void CApplicationPlayer::ContinueClose()', 'void CApplicationPlayer::CompleteCloseCompletions()',
                  'bool CApplicationPlayer::ClosePlayer(', 'void CApplicationPlayer::OnPlaybackStopped()',
                  'void CApplicationPlayer::OpenNext(', 'void CApplicationPlayer::ResetPlayer(',
                  'bool CApplicationPlayer::OpenFile(', 'bool CApplicationPlayer::HasPendingOpen() const', 'uint64_t CApplicationPlayer::BeginOpen()',
                  'void CApplicationPlayer::EndOpen(', 'void CApplicationPlayer::Supersede(',
                  'bool CApplicationPlayer::IsPlayingOpenedVideo() const',
                  'bool CApplicationPlayer::OpenRequested() const', 'bool CApplicationPlayer::StopRequested() const',
                  'bool CApplicationPlayer::HasPendingVideoOpen() const',
                  'bool CApplicationPlayer::PreparePlaybackCleanup()',
                  'bool CApplicationPlayer::PlaybackCleanupCompleted() const']
    application = (ROOT / 'xbmc/application/Application.cpp').read_text()
    playlist = (ROOT / 'xbmc/PlayListPlayer.cpp').read_text()
    frame = function(application, 'void CApplication::FrameMove(')
    code = PRELUDE + '\n'.join(function(source, s) for s in signatures) + HARNESS + TESTS
    code = code.replace('@CONTINUATION@', frame[frame.index('  const bool openingVideo ='):
                                                 frame.index('  // this will go away')])
    code = code.replace('@CLEANUP@', function(application, 'void CApplication::PlaybackCleanup()'))
    code = code.replace('@STOP@', function(application, 'void CApplication::StopPlaying('))
    stop = function(playlist, '  case TMSG_MEDIA_STOP:')
    code = code.replace('@MEDIASTOP@', stop[stop.index('{') + 1:-1])
    stop = application[application.index('  case GUI_MSG_PLAYLISTPLAYER_STOPPED:'):
                       application.index('  case GUI_MSG_PLAYBACK_AVSTARTED:')]
    code = code.replace('@PLAYLISTSTOP@', stop[stop.index(':') + 1:])
    code = code.replace('@PLAYNEXT@', function(playlist, 'bool CPlayListPlayer::PlayNext('))
    code = code.replace('@ENDED@', application[application.index('  case GUI_MSG_PLAYBACK_ENDED:'):
                                               application.index('  case GUI_MSG_PLAYLISTPLAYER_STOPPED:')])
    terminal = min(playlist.find(case) for case in ('  case GUI_MSG_PLAYBACK_ENDED:', '  case GUI_MSG_PLAYBACK_STOPPED:')
                   if case in playlist)
    code = code.replace('@PLAYLISTTERMINAL@', playlist[terminal:playlist.index('  case GUI_MSG_PLAYBACK_STARTED:')])
    code = code.replace('@PLAYLISTFUNCTIONS@', '\n'.join(function(playlist, s) for s in (
        'int CPlayListPlayer::GetNextItemIdx(int offset) const', 'void CPlayListPlayer::Reset()',
        'void CPlayListPlayer::ResetCurrentPlaylist()',
        'void CPlayListPlayer::SetCurrentPlaylist(')))
    wrapper = function(application, 'bool CApplication::PlayFile(')
    code = code.replace('@PLAYFILE@', wrapper[wrapper.index('{') + 1:wrapper.rindex('}')])
    code = code.replace('@ENDOPEN@', function(application, 'void CApplication::EndOpen('))
    play = function(application, 'bool CApplication::StartPlayback(')
    intent = play.index('  if (!bRestart)\n  {')
    code = code.replace('@INTENT@', play[intent:play.index('\n  }\n', intent) + 4])
    lambda_ = play.index('  const auto endPlaylist = [&]')
    code = code.replace('@ENDPLAYLIST@', play[lambda_:play.index('  };\n', lambda_) + 5])
    # The accepted open and the accepted stack both repeat the reset.
    accept = play.index('  appPlayer->OpenFile(item, options,')
    # A stop or a newer open during a resolution, a dialog or a backend open
    # supersedes the open: the check comes after the last dialog and before the
    # accepted open and the accepted stack, and again after the open.
    cancelled = '  if (appPlayer->OpenSuperseded(intent))\n    return false;\n'
    code = code.replace('@CANCELLED@', cancelled)
    assert play.rindex(cancelled, 0, accept) > play.index('  // --- END ---'), 'open accepted after a stop'
    stack_open = play.index('    if (!PlayStack(item, bRestart, intent))')
    assert play.rindex(cancelled, 0, stack_open) > play.index('if (dir && !dir->Resolve(item))'), 'stack accepted after a stop'
    assert '  endPlaylist();' in play[accept:], 'accepted open does not end the playlist'
    assert play.index(cancelled, accept) < play.index('  endPlaylist();', accept), 'backend open not superseded'
    # The open checks again after the Blu-ray menu, before the open discards the
    # notifications of the current playback.
    menu = play.index('CGUIDialogSimpleMenu::ShowPlaySelection(item)')
    assert play.index(cancelled, menu) < play.index('RemoveThreadMessageByMessageIds(', menu), 'menu not superseded'
    # One request owns one intent: the playlist attempt and the stack part.
    assert 'if (attempt)\n    return StartPlayback(std::move(item), player, bRestart, playlistItem, attempt);' in wrapper
    assert 'PlayStack(item, bRestart, intent)' in play
    stack = function(application, 'bool CApplication::PlayStack(')
    assert 'return StartPlayback(std::move(selectedStackPart), "", true, false, intent);' in stack
    assert 'g_application.PlayFile(*item, player, bAutoPlay, true, attempt);' in playlist or \
        'g_application.PlayFile(request, player, bAutoPlay, true, attempt);' in playlist
    stack = play[play.index('  if (item.IsStack())'):]
    assert stack.index('  endPlaylist();') < stack.index('  return true;'), 'accepted stack does not end the playlist'
    # The open registers before the resolution and the dialogs that process messages.
    assert wrapper.index('appPlayer->BeginOpen();') < wrapper.rindex('StartPlayback('), 'open registers late'
    # A trailer is a new open: the playlist and the stack of the movie end.
    trailer = application[application.index('  case GUI_MSG_PLAY_TRAILER:'):]
    trailer = trailer[:trailer.index('    break;')]
    assert 'PostMsg(TMSG_MEDIA_PLAY, 0, 0,' in trailer and 'PostMsg(TMSG_MEDIA_PLAY, 1,' not in trailer
    media = playlist.index('    // first check if we were called from the PlayFile() function')
    code = code.replace('@MEDIAPLAY@', playlist[media:playlist.index('      return;\n    }\n', media) + 20])
    # A request that resolves before PlayFile registers its open first, and a
    # stop during the resolution cancels the request.
    media = function(application, 'bool CApplication::PlayMedia(')
    assert 'appPlayer->BeginOpen();' in media and 'EndOpen(intent);' in media, 'PlayMedia without an open'
    start = function(application, 'bool CApplication::StartMedia(')
    resolved = start.index('GetResolvedPluginResult(item)')
    assert start.index('if (appPlayer->OpenSuperseded(intent))', resolved) < start.index('if (item.IsSmartPlayList())')
    for anchor, handoff in (('GetRecursiveListing(', 'ProcessAndStartPlaylist(smartpl'),
                            ('CGUIDialogBusy::Wait(&getPlaylist', 'ProcessAndStartPlaylist(item.GetPath()')):
        assert start.index('OpenSuperseded(intent)', start.index(anchor)) < start.index(handoff), anchor
    single = playlist[playlist.index('        if (list->Size() == 1 && !(*list)[0]->IsPlayList())'):]
    single = single[:single.index('        }\n        else\n')]
    assert single.index('appPlayer->BeginOpen();') < single.index('GetResolvedPluginResult(*item)')
    assert single.index('!appPlayer->OpenSuperseded(intent)') < single.index('Play(item, pMsg->strParam);')
    assert single.rindex('g_application.EndOpen(intent);') > single.index('PlayMedia(*item')
    compile_run(code, '')
    compile_run(production_play(code, playlist, function), '-play')


if __name__ == '__main__':
    main()
