/*
 *  Copyright (c) 2006 elupus (Joakim Plate)
 *  Copyright (C) 2006-2018 Team Kodi
 *  This file is part of Kodi - https://kodi.tv
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *  See LICENSES/README.md for more information.
 */
#include "UPnPPlayer.h"

#include "ServiceBroker.h"
#include "ThumbLoader.h"
#include "UPnP.h"
#include "UPnPInternal.h"
#include "cores/DataCacheCore.h"
#include "dialogs/GUIDialogBusy.h"
#include "input/actions/Action.h"
#include "input/actions/ActionIDs.h"
#include "messaging/helpers/DialogHelper.h"
#include "music/MusicThumbLoader.h"
#include "settings/Settings.h"
#include "settings/SettingsComponent.h"
#include "threads/Event.h"
#include "utils/StringUtils.h"
#include "utils/TimeUtils.h"
#include "utils/log.h"
#include "video/VideoThumbLoader.h"

#include <algorithm>
#include <mutex>
#include <vector>

#include <Platinum/Source/Devices/MediaRenderer/PltMediaController.h>
#include <Platinum/Source/Devices/MediaServer/PltDidl.h>
#include <Platinum/Source/Platinum/Platinum.h>

using namespace KODI::MESSAGING;

using KODI::MESSAGING::HELPERS::DialogResponse;
using namespace std::chrono_literals;

NPT_SET_LOCAL_LOGGER("xbmc.upnp.player")

namespace UPNP
{

class CUPnPPlayerController : public PLT_MediaControllerDelegate
{
public:
  CUPnPPlayerController(PLT_MediaController* control,
                        PLT_DeviceDataReference& device,
                        IPlayerCallback& callback)
    : m_control(control),
      m_transport(NULL),
      m_device(device),
      m_callback(callback),
      m_posinfo({}),
      m_logger(CServiceBroker::GetLogging().GetLogger("CUPnPPlayerController"))
  {
    m_device->FindServiceByType("urn:schemas-upnp-org:service:AVTransport:1", m_transport);
  }

  NPT_String GetTransportState() const
  {
    std::unique_lock<CCriticalSection> lock(m_section);
    return m_trainfo.cur_transport_state;
  }

  NPT_String GetTransportStatus() const
  {
    std::unique_lock<CCriticalSection> lock(m_section);
    return m_trainfo.cur_transport_status;
  }

  void OnGetMediaInfoResult(NPT_Result res,
                            PLT_DeviceDataReference& device,
                            PLT_MediaInfo* info,
                            void* userdata) override
  {
    if (NPT_FAILED(res) || info == NULL)
      m_logger->error("OnGetMediaInfoResult failed");
  }

  void SetTransportInfo(const PLT_TransportInfo& info)
  {
    std::unique_lock<CCriticalSection> lock(m_section);
    m_trainfo = info;
  }

  // Polls the position; returns true when the transport state is due as well
  bool UpdatePositionInfo()
  {
    if (m_postime == 0 || m_postime > CTimeUtils::GetFrameTime())
      return false;

    m_control->GetPositionInfo(m_device, m_instance, this);
    m_postime = 0;
    return true;
  }

  void OnGetPositionInfoResult(NPT_Result res,
                               PLT_DeviceDataReference& device,
                               PLT_PositionInfo* info,
                               void* userdata) override
  {
    std::unique_lock<CCriticalSection> lock(m_section);

    if (NPT_FAILED(res) || info == NULL)
    {
      m_logger->error("OnGetMediaInfoResult failed");
      m_posinfo = PLT_PositionInfo();
    }
    else
      m_posinfo = *info;
    m_postime = CTimeUtils::GetFrameTime() + 500;
    m_posevnt.Set();
  }

  ~CUPnPPlayerController() override = default;

  PLT_MediaController* m_control;
  PLT_Service* m_transport;
  PLT_DeviceDataReference m_device;
  NPT_UInt32 m_instance = 0;
  IPlayerCallback& m_callback;

  unsigned int m_postime = 0;

  CEvent m_posevnt;
  PLT_PositionInfo m_posinfo;

  PLT_TransportInfo m_trainfo;

private:
  mutable CCriticalSection m_section;
  Logger m_logger;
};

// One renderer command. The command is the userdata of the request, so the
// answer of this command only reaches this command: a late answer of an earlier
// command, or of a command of another open, cannot answer this command.
class CUPnPCommand : public PLT_MediaControllerDelegate
{
public:
  CUPnPCommand() { CUPnP::RegisterUserdata(this); }
  ~CUPnPCommand() override { CUPnP::UnregisterUserdata(this); }

  void OnSetAVTransportURIResult(NPT_Result res, PLT_DeviceDataReference&, void*) override
  {
    Answer(res);
  }
  void OnSetNextAVTransportURIResult(NPT_Result res, PLT_DeviceDataReference&, void*) override
  {
    Answer(res);
  }
  void OnPlayResult(NPT_Result res, PLT_DeviceDataReference&, void*) override { Answer(res); }
  void OnStopResult(NPT_Result res, PLT_DeviceDataReference&, void*) override { Answer(res); }
  void OnGetTransportInfoResult(NPT_Result res,
                                PLT_DeviceDataReference&,
                                PLT_TransportInfo* info,
                                void*) override
  {
    if (NPT_SUCCEEDED(res) && info)
      m_trainfo = *info;
    else
    {
      m_trainfo.cur_speed = "0";
      m_trainfo.cur_transport_state = "STOPPED";
      m_trainfo.cur_transport_status = "ERROR_OCCURED";
    }
    Answer(res);
  }

  CEvent m_event;
  NPT_Result m_status = NPT_FAILURE;
  PLT_TransportInfo m_trainfo; // the answer of GetTransportInfo
  std::atomic<bool> m_answered{false};

private:
  void Answer(NPT_Result res)
  {
    m_status = res;
    m_event.Set();
    m_answered = true;
  }
};

// Platinum finds the command of an answer by the address of the command. A
// command lives until the renderer answers the command, also when the wait for
// the answer ends first: the address of a command with an answer to come is
// never the address of a newer command.
static std::shared_ptr<CUPnPCommand> NewCommand()
{
  static CCriticalSection section;
  // never destroyed: Platinum can answer a command until the process ends
  static auto* const commands = new std::vector<std::shared_ptr<CUPnPCommand>>();
  std::unique_lock<CCriticalSection> lock(section);
  commands->erase(std::remove_if(commands->begin(), commands->end(),
                                 [](const auto& command) { return command->m_answered.load(); }),
                  commands->end());
  return commands->emplace_back(std::make_shared<CUPnPCommand>());
}

CUPnPPlayer::CUPnPPlayer(IPlayerCallback& callback, const char* uuid)
  : IPlayer(callback),
    CThread("UPnPPlayer"),
    m_logger(CServiceBroker::GetLogging().GetLogger(StringUtils::Format("CUPnPPlayer[{}]", uuid)))
{
  m_control = CUPnP::GetInstance()->m_MediaController;

  PLT_DeviceDataReference device;
  if (NPT_SUCCEEDED(m_control->FindRenderer(uuid, device)))
  {
    m_delegate = std::make_unique<CUPnPPlayerController>(m_control, device, callback);
    CUPnP::RegisterUserdata(m_delegate.get());
  }
  else
    m_logger->error("couldn't find device as {}", uuid);
}

CUPnPPlayer::~CUPnPPlayer()
{
  CloseFile();
  CUPnP::UnregisterUserdata(m_delegate.get());
}

uint64_t CUPnPPlayer::AdvanceOpen()
{
  std::unique_lock<CCriticalSection> lock(m_waitSection);
  const uint64_t open = ++m_openSequence;
  // end the waits of the older open, also without an answer of the renderer
  for (CUPnPCommand* command : m_waiting)
    command->m_event.Set();
  return open;
}

int CUPnPPlayer::WaitOnEvent(CUPnPCommand& command, XbmcThreads::EndTime<>& timeout, uint64_t open)
{
  {
    std::unique_lock<CCriticalSection> lock(m_waitSection);
    if (open != m_openSequence)
      return NPT_FAILURE;
    m_waiting.push_back(&command);
  }
  const bool answered = command.m_event.Wait(0ms) || CGUIDialogBusy::WaitOnEvent(command.m_event);
  {
    std::unique_lock<CCriticalSection> lock(m_waitSection);
    m_waiting.erase(std::find(m_waiting.begin(), m_waiting.end(), &command));
  }
  if (!answered)
    return NPT_FAILURE;

  // The busy dialog processes messages: CloseFile or a newer open of this player
  // can run during the wait
  if (open != m_openSequence)
  {
    m_logger->debug("player closed or reopened while waiting for the renderer");
    return NPT_FAILURE;
  }

  return NPT_SUCCESS;
}

int CUPnPPlayer::PlayFile(const CFileItem& file,
                          const CPlayerOptions& options,
                          XbmcThreads::EndTime<>& timeout,
                          uint64_t open)
{
  CFileItem item(file);
  NPT_Reference<CThumbLoader> thumb_loader;
  NPT_Reference<PLT_MediaObject> obj;
  NPT_String path(file.GetPath().c_str());
  NPT_String tmp, resource;
  EMediaControllerQuirks quirks = EMEDIACONTROLLERQUIRKS_NONE;
  bool stopFirst = false;

  NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);

  if (file.IsVideoDb())
    thumb_loader = NPT_Reference<CThumbLoader>(new CVideoThumbLoader());
  else if (item.IsMusicDb())
    thumb_loader = NPT_Reference<CThumbLoader>(new CMusicThumbLoader());

  obj = BuildObject(item, path, false, thumb_loader, NULL, CUPnP::GetServer(), UPnPPlayer);
  if (obj.IsNull())
    goto failed;

  NPT_CHECK_LABEL_SEVERE(PLT_Didl::ToDidl(*obj, "", tmp), failed_todidl);
  tmp.Insert(didl_header, 0);
  tmp.Append(didl_footer);

  quirks = GetMediaControllerQuirks(m_delegate->m_device.AsPointer());
  if (quirks & EMEDIACONTROLLERQUIRKS_X_MKV)
  {
    for (NPT_Cardinal i = 0; i < obj->m_Resources.GetItemCount(); i++)
    {
      if (obj->m_Resources[i].m_ProtocolInfo.GetContentType().Compare("video/x-matroska") == 0)
      {
        m_logger->debug("PlayFile({}): applying video/x-mkv quirk", file.GetPath());
        NPT_String protocolInfo = obj->m_Resources[i].m_ProtocolInfo.ToString();
        protocolInfo.Replace(":video/x-matroska:", ":video/x-mkv:");
        obj->m_Resources[i].m_ProtocolInfo = PLT_ProtocolInfo(protocolInfo);
      }
    }
  }

  /* The resource uri's are stored in the Didl. We must choose the best resource
   * for the playback device */
  NPT_Cardinal res_index;
  NPT_CHECK_LABEL_SEVERE(m_control->FindBestResource(m_delegate->m_device, *obj, res_index),
                         failed_findbestresource);

  // get the transport info to evaluate the TransportState to be able to
  // determine whether we first need to call Stop()
  timeout.Set(timeout.GetInitialTimeoutValue());
  {
    const auto info = NewCommand();
    NPT_CHECK_LABEL_SEVERE(
        m_control->GetTransportInfo(m_delegate->m_device, m_delegate->m_instance, info.get()),
        failed_gettransportinfo);
    NPT_CHECK_LABEL_SEVERE(WaitOnEvent(*info, timeout, open), failed_gettransportinfo);
    PublishTransportInfo(*info);
    stopFirst = info->m_trainfo.cur_transport_state != "NO_MEDIA_PRESENT" &&
                info->m_trainfo.cur_transport_state != "STOPPED";
  }

  if (stopFirst)
  {
    timeout.Set(timeout.GetInitialTimeoutValue());
    const auto stop = NewCommand();
    NPT_CHECK_LABEL_SEVERE(
        m_control->Stop(m_delegate->m_device, m_delegate->m_instance, stop.get()), failed_stop);
    NPT_CHECK_LABEL_SEVERE(WaitOnEvent(*stop, timeout, open), failed_stop);
    NPT_CHECK_LABEL_SEVERE(stop->m_status, failed_stop);
  }

  timeout.Set(timeout.GetInitialTimeoutValue());
  {
    const auto uri = NewCommand();
    NPT_CHECK_LABEL_SEVERE(m_control->SetAVTransportURI(m_delegate->m_device,
                                                        m_delegate->m_instance,
                                                        obj->m_Resources[res_index].m_Uri,
                                                        (const char*)tmp, uri.get()),
                           failed_setavtransporturi);
    NPT_CHECK_LABEL_SEVERE(WaitOnEvent(*uri, timeout, open), failed_setavtransporturi);
    NPT_CHECK_LABEL_SEVERE(uri->m_status, failed_setavtransporturi);
  }

  timeout.Set(timeout.GetInitialTimeoutValue());
  {
    const auto play = NewCommand();
    NPT_CHECK_LABEL_SEVERE(
        m_control->Play(m_delegate->m_device, m_delegate->m_instance, "1", play.get()), failed_play);
    // the renderer plays for this player now, CloseFile must stop the renderer
    m_stopremote = true;
    NPT_CHECK_LABEL_SEVERE(WaitOnEvent(*play, timeout, open), failed_play);
    NPT_CHECK_LABEL_SEVERE(play->m_status, failed_play);
  }

  /* wait for PLAYING state */
  timeout.Set(timeout.GetInitialTimeoutValue());
  do
  {
    const auto info = NewCommand();
    NPT_CHECK_LABEL_SEVERE(
        m_control->GetTransportInfo(m_delegate->m_device, m_delegate->m_instance, info.get()),
        failed_waitplaying);
    NPT_CHECK_LABEL_SEVERE(WaitOnEvent(*info, timeout, open), failed_waitplaying);
    PublishTransportInfo(*info);

    const NPT_String& transportState = info->m_trainfo.cur_transport_state;
    if (transportState == "PLAYING" || transportState == "PAUSED_PLAYBACK")
    {
      break;
    }
    if (transportState == "STOPPED" && info->m_trainfo.cur_transport_status != "OK")
    {
      m_logger->error("OpenFile({}): remote player signalled error", file.GetPath());
      return NPT_FAILURE;
    }
  } while (!timeout.IsTimePast());

  if (options.starttime > 0)
  {
    /* many upnp units won't load file properly until after play (including xbmc) */
    NPT_CHECK_LABEL(m_control->Seek(m_delegate->m_device, m_delegate->m_instance, "REL_TIME",
                                    PLT_Didl::FormatTimeStamp((NPT_UInt32)options.starttime),
                                    m_delegate.get()),
                    failed_seek);
  }

  return NPT_SUCCESS;
failed_todidl:
  m_logger->error("PlayFile({}) failed to serialize item into DIDL-Lite", file.GetPath());
  return NPT_FAILURE;
failed_findbestresource:
  m_logger->error("PlayFile({}) failed to find a matching resource", file.GetPath());
  return NPT_FAILURE;
failed_gettransportinfo:
  m_logger->error("PlayFile({}): call to GetTransportInfo failed", file.GetPath());
  return NPT_FAILURE;
failed_stop:
  m_logger->error("PlayFile({}) failed to stop current playback", file.GetPath());
  return NPT_FAILURE;
failed_setavtransporturi:
  m_logger->error("PlayFile({}) failed to set the playback URI", file.GetPath());
  return NPT_FAILURE;
failed_play:
  m_logger->error("PlayFile({}) failed to start playback", file.GetPath());
  return NPT_FAILURE;
failed_waitplaying:
  m_logger->error("PlayFile({}) failed to wait for PLAYING state", file.GetPath());
  return NPT_FAILURE;
failed_seek:
  m_logger->error("PlayFile({}) failed to seek to start offset", file.GetPath());
  return NPT_FAILURE;
failed:
  m_logger->error("PlayFile({}) failed", file.GetPath());
  return NPT_FAILURE;
}

bool CUPnPPlayer::OpenFile(const CFileItem& file, const CPlayerOptions& options)
{
  XbmcThreads::EndTime<> timeout(10s);
  const uint64_t open = AdvanceOpen();
  // An open of a playing player replaces the item without an end notification,
  // like VideoPlayer: the stop of the renderer below is not the end of the item.
  // A failed open reports the stop of the replaced item.
  const bool reopen = m_started.exchange(false);

  /* if no path we want to attach to a already playing player */
  if (file.GetPath() == "")
  {
    const auto info = NewCommand();
    NPT_CHECK_LABEL_SEVERE(
        m_control->GetTransportInfo(m_delegate->m_device, m_delegate->m_instance, info.get()), failed);

    NPT_CHECK_LABEL_SEVERE(WaitOnEvent(*info, timeout, open), failed);
    PublishTransportInfo(*info);

    /* make sure the attached player is actually playing */
    const NPT_String& transportState = info->m_trainfo.cur_transport_state;
    if (transportState != "PLAYING" && transportState != "PAUSED_PLAYBACK")
    {
      goto failed;
    }
  }
  else
    NPT_CHECK_LABEL_SEVERE(PlayFile(file, options, timeout, open), failed);

  // CloseFile can run on another thread after the last wait
  if (open != m_openSequence)
    goto failed;

  if (!IsRunning())
    Create();

  m_stopremote = true;
  // after the last publish of the open: the item starts with that state, see Process
  m_started = true;

  if (file.IsVideo())
  {
    m_hasVideo = true;
  }
  else if (file.IsAudio())
  {
    m_hasAudio = true;
  }

  m_callback.OnPlayBackStarted(file);
  m_callback.OnAVStarted(file);
  NPT_CHECK_LABEL_SEVERE(
      m_control->GetPositionInfo(m_delegate->m_device, m_delegate->m_instance, m_delegate.get()),
      failed);
  NPT_CHECK_LABEL_SEVERE(
      m_control->GetMediaInfo(m_delegate->m_device, m_delegate->m_instance, m_delegate.get()),
      failed);

  m_updateTimer.Set(0ms);

  return true;
failed:
  m_logger->error("OpenFile({}) failed to open file", file.GetPath());
  if (reopen && !m_started)
    m_callback.OnPlayBackStopped();
  return false;
}

bool CUPnPPlayer::QueueNextFile(const CFileItem& file)
{
  CFileItem item(file);
  NPT_Reference<CThumbLoader> thumb_loader;
  NPT_Reference<PLT_MediaObject> obj;
  NPT_String path(file.GetPath().c_str());
  NPT_String tmp;

  if (file.IsVideoDb())
    thumb_loader = NPT_Reference<CThumbLoader>(new CVideoThumbLoader());
  else if (item.IsMusicDb())
    thumb_loader = NPT_Reference<CThumbLoader>(new CMusicThumbLoader());

  obj = BuildObject(item, path, false, thumb_loader, NULL, CUPnP::GetServer(), UPnPPlayer);
  if (!obj.IsNull())
  {
    NPT_CHECK_LABEL_SEVERE(PLT_Didl::ToDidl(*obj, "", tmp), failed);
    tmp.Insert(didl_header, 0);
    tmp.Append(didl_footer);
  }

  {
    const auto next = NewCommand();
    NPT_CHECK_LABEL_WARNING(
        m_control->SetNextAVTransportURI(m_delegate->m_device, m_delegate->m_instance,
                                         file.GetPath().c_str(), (const char*)tmp, next.get()),
        failed);
    if (!next->m_event.Wait(10000ms))
      goto failed;
    NPT_CHECK_LABEL_WARNING(next->m_status, failed);
  }
  return true;

failed:
  m_logger->error("QueueNextFile({}) failed to queue file", file.GetPath());
  return false;
}

bool CUPnPPlayer::CloseFile(bool reopen)
{
  // stops an OpenFile that waits for the renderer
  AdvanceOpen();
  NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);
  // stop the renderer once, a newer player can use the renderer after the stop.
  // After a failed stop the renderer still plays for this player.
  if (m_stopremote)
  {
    const auto stop = NewCommand();
    NPT_CHECK_LABEL(m_control->Stop(m_delegate->m_device, m_delegate->m_instance, stop.get()), failed);
    if (!stop->m_event.Wait(10000ms))
      goto failed;
    NPT_CHECK_LABEL(stop->m_status, failed);
    m_stopremote = false;
  }

  if (m_started.exchange(false))
    m_callback.OnPlayBackStopped();
  StopThread(true);
  CServiceBroker::GetDataCacheCore().Reset();
  return true;
failed:
  m_logger->error("CloseFile - unable to stop playback");
  CServiceBroker::GetDataCacheCore().Reset();
  return false;
}

void CUPnPPlayer::Pause()
{
  if (IsPaused())
  {
    NPT_CHECK_LABEL(
        m_control->Play(m_delegate->m_device, m_delegate->m_instance, "1", m_delegate.get()),
        failed);
    CDataCacheCore::GetInstance().SetSpeed(1.0, 1.0);
    m_callback.OnPlayBackResumed();
  }
  else
  {
    NPT_CHECK_LABEL(
        m_control->Pause(m_delegate->m_device, m_delegate->m_instance, m_delegate.get()), failed);
    CDataCacheCore::GetInstance().SetSpeed(1.0, 0.0);
    m_callback.OnPlayBackPaused();
  }

  return;
failed:
  m_logger->error("CloseFile - unable to pause/unpause playback");
}

void CUPnPPlayer::SeekTime(int64_t ms)
{
  NPT_CHECK_LABEL(m_control->Seek(m_delegate->m_device, m_delegate->m_instance, "REL_TIME",
                                  PLT_Didl::FormatTimeStamp((NPT_UInt32)(ms / 1000)),
                                  m_delegate.get()),
                  failed);

  CDataCacheCore::GetInstance().SeekFinished(0);
  return;
failed:
  m_logger->error("SeekTime - unable to seek playback");
}

float CUPnPPlayer::GetPercentage()
{
  int64_t tot = GetTotalTime();
  if (tot)
    return 100.0f * GetTime() / tot;
  else
    return 0.0f;
}

void CUPnPPlayer::SeekPercentage(float percent)
{
  int64_t tot = GetTotalTime();
  if (tot)
    SeekTime((int64_t)(tot * percent / 100));
}

void CUPnPPlayer::Seek(bool bPlus, bool bLargeStep, bool bChapterOverride)
{
}

void CUPnPPlayer::PublishTransportInfo(const CUPnPCommand& info)
{
  std::unique_lock<CCriticalSection> lock(m_stateSection);
  m_delegate->SetTransportInfo(info.m_trainfo);
  ++m_stateEpoch;
}

void CUPnPPlayer::PollTransportInfo(bool send)
{
  std::unique_lock<CCriticalSection> lock(m_stateSection);
  if (m_poll && m_poll->m_answered)
  {
    // the answer of a poll sent before the last state of an open is older
    if (m_pollEpoch == m_stateEpoch)
      m_delegate->SetTransportInfo(m_poll->m_trainfo);
    m_poll.reset();
  }
  if (send && !m_poll)
  {
    m_poll = NewCommand();
    m_pollEpoch = m_stateEpoch;
    m_control->GetTransportInfo(m_delegate->m_device, m_delegate->m_instance, m_poll.get());
  }
}

void CUPnPPlayer::Process()
{
  while (!m_bStop)
  {
    NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);
    PollTransportInfo(m_delegate->UpdatePositionInfo());

    if (m_started)
    {
      // Update player times
      CDataCacheCore& dataCacheCore = CDataCacheCore::GetInstance();
      if (m_updateTimer.IsTimePast())
      {
        dataCacheCore.SetPlayTimes(0, GetTime(), 0, GetTotalTime());
        m_updateTimer.Set(500ms);
      }

      // Player may be paused or resumed from the target player, state needs to be synchronized to data cache core.
      if (IsPaused() && dataCacheCore.GetSpeed() > 0.0f)
      {
        m_callback.OnPlayBackPaused();
        dataCacheCore.SetSpeed(1.0, 0.0);
      }
      else if (!IsPaused() && dataCacheCore.GetSpeed() == 0.0f)
      {
        m_callback.OnPlayBackResumed();
        dataCacheCore.SetSpeed(1.0, 1.0);
      }

      // An open of this player can end the item first, without an end
      // notification. The open publishes the state and starts the item under the
      // state lock, so the state read and the claim of the item are one step.
      bool ended;
      {
        std::unique_lock<CCriticalSection> lock(m_stateSection);
        ended = m_delegate->GetTransportState() == "STOPPED" && m_started.exchange(false);
      }
      if (ended)
      {
        m_logger->info("Transport state flagged as STOPPED. Triggering OnPlayBackEnded.");
        m_callback.OnPlayBackEnded();
      }
    }
  }
failed:
  CloseFile();
}

bool CUPnPPlayer::IsPlaying() const
{
  NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);
  return m_delegate->GetTransportState() != "STOPPED";
failed:
  return false;
}

bool CUPnPPlayer::IsPaused() const
{
  NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);
  return m_delegate->GetTransportState() == "PAUSED_PLAYBACK";
failed:
  return false;
}

void CUPnPPlayer::SetVolume(float volume)
{
  if (!CServiceBroker::GetSettingsComponent()->GetSettings()->GetBool(
          CSettings::SETTING_SERVICES_UPNPPLAYERVOLUMESYNC))
  {
    return;
  }

  NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);
  NPT_CHECK_LABEL(m_control->SetVolume(m_delegate->m_device, m_delegate->m_instance, "Master",
                                       (int)(volume * 100), m_delegate.get()),
                  failed);
  return;
failed:
  m_logger->error("- unable to set volume");
}

int64_t CUPnPPlayer::GetTime()
{
  NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);
  return m_delegate->m_posinfo.rel_time.ToMillis();
failed:
  return 0;
}

int64_t CUPnPPlayer::GetTotalTime()
{
  NPT_CHECK_POINTER_LABEL_SEVERE(m_delegate, failed);
  return m_delegate->m_posinfo.track_duration.ToMillis();
failed:
  return 0;
};

bool CUPnPPlayer::OnAction(const CAction& action)
{
  switch (action.GetID())
  {
    case ACTION_STOP:
      if (IsPlaying())
      {
        //stop on remote system
        m_stopremote = HELPERS::ShowYesNoDialogText(37022, 37023) == DialogResponse::CHOICE_YES;

        return false; /* let normal code handle the action */
      }
      [[fallthrough]];
    default:
      return false;
  }
}

void CUPnPPlayer::SetSpeed(float speed)
{
  if (IsPaused() && speed == 1.0f)
  {
    NPT_CHECK_LABEL(
        m_control->Play(m_delegate->m_device, m_delegate->m_instance, "1", m_delegate.get()),
        failed);
    m_callback.OnPlayBackResumed();
    CDataCacheCore::GetInstance().SetSpeed(1.0, 1.0);
  }
  return;
failed:
  m_logger->error("- unable to set speed");
}

void CUPnPPlayer::OnExit()
{
  if (m_started.exchange(false))
    m_callback.OnPlayBackEnded();
}

} /* namespace UPNP */
