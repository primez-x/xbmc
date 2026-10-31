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
HARNESS = autoplay['HARNESS']
HARNESS = HARNESS[:HARNESS.index('static void ended(')]
HARNESS = HARNESS.replace(
    ' void SendThreadMessage(CGUIMessage&){}',
    ' int threadMessages=0;void SendThreadMessage(CGUIMessage&){++threadMessages;}')
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
    ' bool Play(int,const std::string&,bool);',
    ' bool m_bPlaybackStarted=true;bool Stopped();\n bool Play(int,const std::string&,bool);')
HARNESS = HARNESS.replace(
    ' void Frame(){',
    ' struct {void Set(){}} m_playerEvent;int stackPlays=0;\n'
    ' bool PlayFile(CFileItem,const std::string&,bool){++stackPlays;return true;}\n'
    ' bool Ended();\n void Frame(){')
TESTS = r"""
bool CApplication::Ended(){switch(GUI_MSG_PLAYBACK_ENDED){@ENDED@}return false;}
bool CPlayListPlayer::Stopped(){switch(GUI_MSG_PLAYBACK_STOPPED){@PLAYLISTSTOPPED@}return false;}
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
 {CApplication app;begin(app);reset();app.Ended();
  assert(CServiceBroker::pvr.ended==1&&CServiceBroker::pvr.stopped==0);
  assert(app.player.HasPendingOpen()&&app.player.m_nextItem.pItem->id==9);}
 {CApplication app;begin(app);reset();app.stack.regular=app.stack.next=true;app.Ended();
  assert(app.stackPlays==1);}
 // The stop of the replaced playback does not stop the playlist under the newer open.
 {CApplication app;begin(app);auto old=app.player.m_pPlayer;replace(app);
  app.player.OnPlaybackStopped();
  CServiceBroker::playlist.Stopped();
  const int messages=CServiceBroker::gui.wm.threadMessages;
  if(messages)app.PlaylistStopped();
  assert(messages==0&&CServiceBroker::playlist.m_iCurrentPlayList!=TYPE_NONE);
  assert(app.player.HasPendingOpen());opensLatest(app,old);
 }
 // An explicit stop still ends the playlist and the pending open.
 {CApplication app;begin(app);auto old=app.player.m_pPlayer;replace(app);app.StopPlaying();
  assert(!app.player.HasPendingOpen());app.player.OnPlaybackStopped();
  assert(CServiceBroker::playlist.Stopped());
  assert(CServiceBroker::gui.wm.threadMessages==1&&CServiceBroker::playlist.m_iCurrentPlayList==TYPE_NONE);
  old->canClose=true;app.Frame();assert(app.player.created==0);
 }
 std::cout<<"PASS replaced playback end and stop keep the newer open; normal end, stack and explicit stop unchanged\n";
}
"""


def main():
    source = (ROOT / 'xbmc/application/ApplicationPlayer.cpp').read_text()
    signatures = ['std::shared_ptr<IPlayer> CApplicationPlayer::GetInternal()',
                  'void CApplicationPlayer::ContinueClose()', 'void CApplicationPlayer::CompleteCloseCompletions()',
                  'bool CApplicationPlayer::ClosePlayer(', 'void CApplicationPlayer::OnPlaybackStopped()',
                  'void CApplicationPlayer::OpenNext(', 'void CApplicationPlayer::ResetPlayer(',
                  'bool CApplicationPlayer::OpenFile(', 'bool CApplicationPlayer::HasPendingOpen() const',
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
    code = code.replace('@PLAYLISTSTOPPED@', playlist[playlist.index('  case GUI_MSG_PLAYBACK_STOPPED:'):
                                                      playlist.index('  case GUI_MSG_PLAYBACK_STARTED:')])
    with tempfile.TemporaryDirectory(prefix='replaced-playback-end-') as tmp:
        out = Path(tmp)
        (out / 'test.cpp').write_text(code)
        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
        subprocess.run([str(out / 'test')], check=True, timeout=15)


if __name__ == '__main__':
    main()
