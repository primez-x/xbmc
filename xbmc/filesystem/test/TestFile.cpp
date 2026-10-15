/*
 *  Copyright (C) 2005-2018 Team Kodi
 *  This file is part of Kodi - https://kodi.tv
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 *  See LICENSES/README.md for more information.
 */

#include "filesystem/File.h"
#include "test/TestUtils.h"

#include <errno.h>
#include <string>

#include <gtest/gtest.h>

#if defined(TARGET_LINUX)
#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
#endif

TEST(TestFile, Read)
{
  const std::string newLine = CXBMCTestUtils::Instance().getNewLineCharacters();
  const size_t size = 1616;
  const size_t lines = 25;
  size_t addPerLine = newLine.length() - 1;
  size_t realSize = size + lines * addPerLine;

  const std::string firstBuf  = "About" + newLine + "-----" + newLine + "XBMC is ";
  const std::string secondBuf = "an award-winning fre";
  const std::string thirdBuf  = "ent hub for digital ";
  const std::string fourthBuf  = "rs, XBMC is a non-pr";
  const std::string fifthBuf = "multimedia jukebox." + newLine;

  XFILE::CFile file;
  char buf[23] = {};

  size_t currentPos;
  ASSERT_TRUE(file.Open(
    XBMC_REF_FILE_PATH("/xbmc/filesystem/test/reffile.txt")));
  EXPECT_EQ(0, file.GetPosition());
  EXPECT_EQ(realSize, file.GetLength());
  EXPECT_EQ(firstBuf.length(), static_cast<size_t>(file.Read(buf, firstBuf.length())));
  file.Flush();
  currentPos = firstBuf.length();
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(0, memcmp(firstBuf.c_str(), buf, firstBuf.length()));
  EXPECT_EQ(secondBuf.length(), static_cast<size_t>(file.Read(buf, secondBuf.length())));
  currentPos += secondBuf.length();
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(0, memcmp(secondBuf.c_str(), buf, secondBuf.length()));
  currentPos = 100 + addPerLine * 3;
  EXPECT_EQ(currentPos, file.Seek(currentPos));
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(thirdBuf.length(), static_cast<size_t>(file.Read(buf, thirdBuf.length())));
  file.Flush();
  currentPos += thirdBuf.length();
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(0, memcmp(thirdBuf.c_str(), buf, thirdBuf.length()));
  currentPos += 100 + addPerLine * 1;
  EXPECT_EQ(currentPos, file.Seek(100 + addPerLine * 1, SEEK_CUR));
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(fourthBuf.length(), static_cast<size_t>(file.Read(buf, fourthBuf.length())));
  file.Flush();
  currentPos += fourthBuf.length();
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(0, memcmp(fourthBuf.c_str(), buf, fourthBuf.length()));
  currentPos = realSize - fifthBuf.length();
  EXPECT_EQ(currentPos, file.Seek(-(int64_t)fifthBuf.length(), SEEK_END));
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(fifthBuf.length(), static_cast<size_t>(file.Read(buf, fifthBuf.length())));
  file.Flush();
  currentPos += fifthBuf.length();
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(0, memcmp(fifthBuf.c_str(), buf, fifthBuf.length()));
  currentPos += 100;
  EXPECT_EQ(currentPos, file.Seek(100, SEEK_CUR));
  EXPECT_EQ(currentPos, file.GetPosition());
  currentPos = 0;
  EXPECT_EQ(currentPos, file.Seek(currentPos, SEEK_SET));
  EXPECT_EQ(firstBuf.length(), static_cast<size_t>(file.Read(buf, firstBuf.length())));
  file.Flush();
  currentPos += firstBuf.length();
  EXPECT_EQ(currentPos, file.GetPosition());
  EXPECT_EQ(0, memcmp(firstBuf.c_str(), buf, firstBuf.length()));
  EXPECT_EQ(0, file.Seek(0, SEEK_SET));
  EXPECT_EQ(-1, file.Seek(-100, SEEK_SET));
  file.Close();
}

TEST(TestFile, Write)
{
  XFILE::CFile *file;
  const char str[] = "TestFile.Write test string\n";
  char buf[30] = {};

  ASSERT_NE(nullptr, file = XBMC_CREATETEMPFILE(""));
  file->Close();
  ASSERT_TRUE(file->OpenForWrite(XBMC_TEMPFILEPATH(file), true));
  EXPECT_EQ((int)sizeof(str), file->Write(str, sizeof(str)));
  file->Flush();
  EXPECT_EQ((int64_t)sizeof(str), file->GetPosition());
  file->Close();
  ASSERT_TRUE(file->Open(XBMC_TEMPFILEPATH(file)));
  EXPECT_EQ(0, file->GetPosition());
  EXPECT_EQ((int64_t)sizeof(str), file->Seek(0, SEEK_END));
  EXPECT_EQ(0, file->Seek(0, SEEK_SET));
  EXPECT_EQ((int64_t)sizeof(str), file->GetLength());
  EXPECT_EQ(sizeof(str), static_cast<size_t>(file->Read(buf, sizeof(buf))));
  file->Flush();
  EXPECT_EQ((int64_t)sizeof(str), file->GetPosition());
  EXPECT_EQ(0, memcmp(str, buf, sizeof(str)));
  file->Close();
  EXPECT_TRUE(XBMC_DELETETEMPFILE(file));
}

TEST(TestFile, Exists)
{
  XFILE::CFile *file;

  ASSERT_NE(nullptr, file = XBMC_CREATETEMPFILE(""));
  file->Close();
  EXPECT_TRUE(XFILE::CFile::Exists(XBMC_TEMPFILEPATH(file)));
  EXPECT_FALSE(XFILE::CFile::Exists(""));
  EXPECT_TRUE(XBMC_DELETETEMPFILE(file));
}

TEST(TestFile, Stat)
{
  XFILE::CFile *file;
  struct __stat64 buffer;

  ASSERT_NE(nullptr, file = XBMC_CREATETEMPFILE(""));
  EXPECT_EQ(0, file->Stat(&buffer));
  file->Close();
  EXPECT_NE(0U, buffer.st_mode | _S_IFREG);
  EXPECT_EQ(-1, XFILE::CFile::Stat("", &buffer));
  EXPECT_EQ(ENOENT, errno);
  EXPECT_TRUE(XBMC_DELETETEMPFILE(file));
}

TEST(TestFile, Delete)
{
  XFILE::CFile *file;
  std::string path;

  ASSERT_NE(nullptr, file = XBMC_CREATETEMPFILE(""));
  file->Close();
  path = XBMC_TEMPFILEPATH(file);
  EXPECT_TRUE(XFILE::CFile::Exists(path));
  EXPECT_TRUE(XFILE::CFile::Delete(path));
  EXPECT_FALSE(XFILE::CFile::Exists(path));
  EXPECT_FALSE(XBMC_DELETETEMPFILE(file));
}

TEST(TestFile, Rename)
{
  XFILE::CFile *file1, *file2;
  std::string path1, path2;

  ASSERT_NE(nullptr, file1 = XBMC_CREATETEMPFILE(""));
  file1->Close();
  path1 = XBMC_TEMPFILEPATH(file1);
  ASSERT_NE(nullptr, file2 = XBMC_CREATETEMPFILE(""));
  file2->Close();
  path2 = XBMC_TEMPFILEPATH(file2);
  EXPECT_TRUE(XFILE::CFile::Delete(path1));
  EXPECT_FALSE(XFILE::CFile::Exists(path1));
  EXPECT_TRUE(XFILE::CFile::Exists(path2));
  EXPECT_TRUE(XFILE::CFile::Rename(path2, path1));
  EXPECT_TRUE(XFILE::CFile::Exists(path1));
  EXPECT_FALSE(XFILE::CFile::Exists(path2));
  EXPECT_TRUE(XFILE::CFile::Delete(path1));
  EXPECT_FALSE(XBMC_DELETETEMPFILE(file1));
  EXPECT_FALSE(XBMC_DELETETEMPFILE(file2));
}

TEST(TestFile, Copy)
{
  XFILE::CFile *file1, *file2;
  std::string path1, path2;

  ASSERT_NE(nullptr, file1 = XBMC_CREATETEMPFILE(""));
  file1->Close();
  path1 = XBMC_TEMPFILEPATH(file1);
  ASSERT_NE(nullptr, file2 = XBMC_CREATETEMPFILE(""));
  file2->Close();
  path2 = XBMC_TEMPFILEPATH(file2);
  EXPECT_TRUE(XFILE::CFile::Delete(path1));
  EXPECT_FALSE(XFILE::CFile::Exists(path1));
  EXPECT_TRUE(XFILE::CFile::Exists(path2));
  EXPECT_TRUE(XFILE::CFile::Copy(path2, path1));
  EXPECT_TRUE(XFILE::CFile::Exists(path1));
  EXPECT_TRUE(XFILE::CFile::Exists(path2));
  EXPECT_TRUE(XFILE::CFile::Delete(path1));
  EXPECT_TRUE(XFILE::CFile::Delete(path2));
  EXPECT_FALSE(XBMC_DELETETEMPFILE(file1));
  EXPECT_FALSE(XBMC_DELETETEMPFILE(file2));
}

TEST(TestFile, SetHidden)
{
  XFILE::CFile *file;

  ASSERT_NE(nullptr, file = XBMC_CREATETEMPFILE(""));
  file->Close();
  EXPECT_TRUE(XFILE::CFile::Exists(XBMC_TEMPFILEPATH(file)));
  bool result = XFILE::CFile::SetHidden(XBMC_TEMPFILEPATH(file), true);
#ifdef TARGET_WINDOWS
  EXPECT_TRUE(result);
#else
  EXPECT_FALSE(result);
#endif
  EXPECT_TRUE(XFILE::CFile::Exists(XBMC_TEMPFILEPATH(file)));
  EXPECT_TRUE(XBMC_DELETETEMPFILE(file));
}

#if defined(TARGET_LINUX)
namespace
{
class CPausedHttpSource
{
public:
  explicit CPausedHttpSource(std::chrono::milliseconds pause) : m_pause(pause) {}

  ~CPausedHttpSource()
  {
    {
      std::lock_guard<std::mutex> lock(m_mutex);
      m_release = true;
      m_condition.notify_all();
    }
    if (m_thread.joinable())
      m_thread.join();
    if (m_listener >= 0)
      close(m_listener);
  }

  bool Start()
  {
    m_listener = socket(AF_INET, SOCK_STREAM, 0);
    if (m_listener < 0)
      return false;

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(m_listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
        listen(m_listener, 1) != 0)
      return false;

    socklen_t size = sizeof(address);
    if (getsockname(m_listener, reinterpret_cast<sockaddr*>(&address), &size) != 0)
      return false;
    m_port = ntohs(address.sin_port);
    m_thread = std::thread([this] { Serve(); });
    return true;
  }

  std::string URL() const { return "http://127.0.0.1:" + std::to_string(m_port) + "/paused"; }

  bool WaitForPause()
  {
    std::unique_lock<std::mutex> lock(m_mutex);
    return m_condition.wait_for(lock, std::chrono::seconds(2), [this] { return m_paused; });
  }

  void StartPause()
  {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_startPause = true;
    m_condition.notify_all();
  }

  bool IsPaused()
  {
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_paused;
  }

private:
  static bool Send(int client, const std::string& data)
  {
    size_t sent = 0;
    while (sent < data.size())
    {
      const ssize_t count = send(client, data.data() + sent, data.size() - sent, MSG_NOSIGNAL);
      if (count <= 0)
        return false;
      sent += count;
    }
    return true;
  }

  void Serve()
  {
    pollfd listener{m_listener, POLLIN, 0};
    if (poll(&listener, 1, 5000) <= 0)
      return;
    const int client = accept(m_listener, nullptr, nullptr);
    if (client < 0)
      return;

    std::string request;
    char buffer[1024];
    while (request.find("\r\n\r\n") == std::string::npos)
    {
      pollfd connection{client, POLLIN, 0};
      if (poll(&connection, 1, 2000) <= 0)
        break;
      const ssize_t count = recv(client, buffer, sizeof(buffer), 0);
      if (count <= 0)
        break;
      request.append(buffer, count);
    }

    if (Send(client, "HTTP/1.1 200 OK\r\nContent-Length: 8192\r\nConnection: close\r\n\r\n") &&
        Send(client, std::string(4096, 'a')))
    {
      {
        std::unique_lock<std::mutex> lock(m_mutex);
        m_paused = true;
        m_condition.notify_all();
        m_condition.wait(lock, [this] { return m_startPause || m_release; });
        if (!m_release)
          m_condition.wait_for(lock, m_pause, [this] { return m_release; });
        m_paused = false;
      }
      Send(client, std::string(4096, 'b'));
    }
    close(client);
  }

  const std::chrono::milliseconds m_pause;
  int m_listener = -1;
  uint16_t m_port = 0;
  std::thread m_thread;
  std::mutex m_mutex;
  std::condition_variable m_condition;
  bool m_release = false;
  bool m_startPause = false;
  bool m_paused = false;
};
} // namespace

TEST(TestFile, CloseCachedPausedHttpSource)
{
  CPausedHttpSource source(std::chrono::seconds(4));
  ASSERT_TRUE(source.Start());
  XFILE::CFile file;
  ASSERT_TRUE(file.Open(source.URL(), XFILE::READ_CACHED | XFILE::READ_NO_BUFFER));
  ASSERT_TRUE(source.WaitForPause());
  source.StartPause();
  char prefix[128];
  ASSERT_EQ(static_cast<ssize_t>(sizeof(prefix)), file.Read(prefix, sizeof(prefix)));
  EXPECT_EQ(std::string(sizeof(prefix), 'a'), std::string(prefix, sizeof(prefix)));

  // Allow the cache producer to consume the prefix and wait for the next body bytes.
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  const auto start = std::chrono::steady_clock::now();
  file.Close();
  const auto elapsed = std::chrono::steady_clock::now() - start;
  EXPECT_TRUE(source.IsPaused());
  EXPECT_LT(elapsed, std::chrono::seconds(3));
}

TEST(TestFile, ReadCachedPausedHttpSource)
{
  CPausedHttpSource source(std::chrono::seconds(1));
  ASSERT_TRUE(source.Start());
  XFILE::CFile file;
  ASSERT_TRUE(file.Open(source.URL(), XFILE::READ_CACHED | XFILE::READ_NO_BUFFER));
  ASSERT_TRUE(source.WaitForPause());
  source.StartPause();
  std::string body(8192, '\0');
  ASSERT_EQ(static_cast<ssize_t>(body.size()), file.Read(body.data(), body.size()));
  EXPECT_EQ(std::string(4096, 'a') + std::string(4096, 'b'), body);
  char byte;
  EXPECT_EQ(0, file.Read(&byte, 1));
  file.Close();
}
#endif
