// Update page, the same flow as the DOAX2 Recomp launcher. This is the only part of the patcher that
// goes online, and only while the page is open: it asks GitHub for the latest release. INSTALL UPDATE
// downloads the new patcher to %TEMP%\DOAP-Enhanced-update and checks it against SHA256SUMS.txt. A
// running exe cannot be overwritten, so it is renamed to *.update-old and deleted on the next start.
// Included inside doap_patcher.cpp's anonymous namespace.

constexpr const wchar_t* kUpdateUrl = L"https://api.github.com/repos/hibikifukui22/DOAP-Enhanced/releases/latest";
constexpr const wchar_t* kOldSuffix = L".update-old";
constexpr UINT kUpdateChanged = WM_APP + 2;  // posted by the update thread: repaint
constexpr UINT_PTR kUpdateTimer = 3;         // animates steps of unknown length

enum class Page { kMain, kUpdate };
Page g_page = Page::kMain;

enum UpdateStep { kStepCheck, kStepDownload, kStepVerify, kStepInstall, kStepCount };
enum class StepState { kWaiting, kRunning, kDone, kFailed };

struct UpdateProgress {
  StepState state[kStepCount] = {};
  float fraction[kStepCount] = {};  // below 0 while the length is unknown
  std::wstring detail[kStepCount];
  std::wstring message;
  bool error = false;
  bool busy = false;
  bool checked = false;    // a check has run on this visit of the page
  bool available = false;  // a newer release was found
  bool installed = false;
  std::wstring latest;     // "v1.0.2"
  std::wstring exe_url, sums_url, exe_name;
};

std::mutex g_update_mutex;
UpdateProgress g_update;

// Changes the update state from any thread and repaints the window.
void UpdateState(const std::function<void(UpdateProgress&)>& change) {
  {
    std::lock_guard lock(g_update_mutex);
    change(g_update);
  }
  PostMessageW(g_window, kUpdateChanged, 0, 0);
}

UpdateProgress UpdateSnapshot() {
  std::lock_guard lock(g_update_mutex);
  return g_update;
}

void StepRunning(int step, std::wstring detail, float fraction = -1) {
  UpdateState([&](UpdateProgress& u) {
    u.state[step] = StepState::kRunning;
    u.fraction[step] = fraction;
    u.detail[step] = std::move(detail);
  });
}

void StepDone(int step, std::wstring detail) {
  UpdateState([&](UpdateProgress& u) {
    u.state[step] = StepState::kDone;
    u.fraction[step] = 1;
    u.detail[step] = std::move(detail);
  });
}

void StepFailed(int step, std::wstring detail, std::wstring message) {
  UpdateState([&](UpdateProgress& u) {
    u.state[step] = StepState::kFailed;
    u.detail[step] = std::move(detail);
    u.message = std::move(message);
    u.error = true;
    u.busy = false;
  });
}

std::wstring Widen(const std::string& text) { return std::wstring(text.begin(), text.end()); }

// Only GitHub over HTTPS. DOAP_UPDATE_URL may point the check at a local test server instead.
bool AllowedUpdateUrl(const std::wstring& url) {
  for (const wchar_t* prefix : {L"https://api.github.com/", L"https://github.com/"})
    if (url.rfind(prefix, 0) == 0) return true;
  if (_wgetenv(L"DOAP_UPDATE_URL"))
    for (const wchar_t* prefix : {L"http://127.0.0.1:", L"http://localhost:"})
      if (url.rfind(prefix, 0) == 0) return true;
  return false;
}

struct HttpHandle {
  HINTERNET handle = nullptr;
  ~HttpHandle() {
    if (handle) WinHttpCloseHandle(handle);
  }
};

// Fetches url into body or file. progress(received, total) runs while reading, total 0 when unknown.
// Redirects (GitHub download links) are followed.
bool HttpGet(const std::wstring& url, std::string* body, const fs::path* file,
             const std::function<void(uint64_t, uint64_t)>& progress, std::wstring& error) {
  if (!AllowedUpdateUrl(url)) {
    error = L"The update address was refused.";
    return false;
  }
  URL_COMPONENTS parts{};
  parts.dwStructSize = sizeof(parts);
  wchar_t host_buffer[256], path_buffer[2048];
  parts.lpszHostName = host_buffer;
  parts.dwHostNameLength = DWORD(std::size(host_buffer));
  parts.lpszUrlPath = path_buffer;
  parts.dwUrlPathLength = DWORD(std::size(path_buffer));
  if (!WinHttpCrackUrl(url.c_str(), 0, 0, &parts)) {
    error = L"The update address is not valid.";
    return false;
  }
  const std::wstring agent = std::wstring(L"DOAP-Enhanced-Patcher/") + kVersion;
  HttpHandle session{WinHttpOpen(agent.c_str(), WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, WINHTTP_NO_PROXY_NAME,
                                 WINHTTP_NO_PROXY_BYPASS, 0)};
  if (!session.handle) {
    error = L"Could not start the connection.";
    return false;
  }
  WinHttpSetTimeouts(session.handle, 15000, 15000, 30000, 60000);
  HttpHandle connection{WinHttpConnect(session.handle, host_buffer, parts.nPort, 0)};
  HttpHandle request{connection.handle
                         ? WinHttpOpenRequest(connection.handle, L"GET", path_buffer, nullptr, WINHTTP_NO_REFERER,
                                              WINHTTP_DEFAULT_ACCEPT_TYPES,
                                              parts.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0)
                         : nullptr};
  const wchar_t* headers = L"Accept: application/vnd.github+json, application/octet-stream\r\n";
  if (!request.handle || !WinHttpSendRequest(request.handle, headers, DWORD(-1), WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
      !WinHttpReceiveResponse(request.handle, nullptr)) {
    error = L"Could not reach GitHub. Check your internet connection.";
    return false;
  }
  DWORD status = 0, size = sizeof(status);
  WinHttpQueryHeaders(request.handle, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                      WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX);
  if (status != 200) {
    error = status == 404   ? L"No release was found on GitHub."
            : status == 403 ? L"GitHub is limiting requests right now. Try again in an hour."
                            : L"GitHub answered with error " + std::to_wstring(status) + L".";
    return false;
  }
  uint64_t total = 0;
  wchar_t length_text[32];
  size = sizeof(length_text);
  if (WinHttpQueryHeaders(request.handle, WINHTTP_QUERY_CONTENT_LENGTH, WINHTTP_HEADER_NAME_BY_INDEX, length_text,
                          &size, WINHTTP_NO_HEADER_INDEX))
    total = _wcstoui64(length_text, nullptr, 10);
  std::ofstream output;
  if (file) {
    output.open(*file, std::ios::binary | std::ios::trunc);
    if (!output) {
      error = L"Could not save the download in the Windows temp folder.";
      return false;
    }
  }
  std::vector<char> buffer(1 << 16);
  uint64_t received = 0;
  for (;;) {
    DWORD read = 0;
    if (!WinHttpReadData(request.handle, buffer.data(), DWORD(buffer.size()), &read)) {
      error = L"The download was interrupted. Check your internet connection.";
      return false;
    }
    if (read == 0) break;
    if (file)
      output.write(buffer.data(), read);
    else
      body->append(buffer.data(), read);
    received += read;
    if (progress) progress(received, total);
  }
  if (file && !output.good()) {
    error = L"Could not save the download in the Windows temp folder.";
    return false;
  }
  return true;
}

// The string value of "key" at or after 'from' in a JSON text.
std::string JsonString(const std::string& json, const std::string& key, size_t from, size_t* end = nullptr) {
  size_t at = json.find("\"" + key + "\"", from);
  if (at == std::string::npos) return {};
  at = json.find(':', at);
  if (at == std::string::npos) return {};
  at = json.find_first_not_of(" \t\r\n", at + 1);
  if (at == std::string::npos || json[at] != '"') return {};
  std::string value;
  for (++at; at < json.size() && json[at] != '"'; ++at) {
    if (json[at] == '\\' && at + 1 < json.size()) ++at;
    value += json[at];
  }
  if (end) *end = at;
  return value;
}

// "v1.0.2" -> {1, 0, 2}
std::vector<int> VersionNumbers(const std::wstring& tag) {
  std::vector<int> numbers;
  int value = -1;
  for (wchar_t c : tag) {
    if (c >= L'0' && c <= L'9') {
      value = (value < 0 ? 0 : value * 10) + (c - L'0');
    } else if (value >= 0) {
      numbers.push_back(value);
      value = -1;
    }
  }
  if (value >= 0) numbers.push_back(value);
  return numbers;
}

bool NewerVersion(const std::wstring& latest, const std::wstring& installed) {
  auto a = VersionNumbers(latest), b = VersionNumbers(installed);
  a.resize(std::max(a.size(), b.size()));
  b.resize(a.size());
  return a > b;
}

std::string Sha256Hex(const fs::path& path) {
  Sha256 sha;
  std::ifstream input(path, std::ios::binary);
  std::vector<char> buffer(1 << 20);
  while (input) {
    input.read(buffer.data(), std::streamsize(buffer.size()));
    if (input.gcount() > 0) sha.Add(buffer.data(), size_t(input.gcount()));
  }
  std::string hex;
  static const char* kHex = "0123456789abcdef";
  for (uint8_t byte : sha.Finish()) {
    hex += kHex[byte >> 4];
    hex += kHex[byte & 15];
  }
  return hex;
}

std::wstring Megabytes(uint64_t bytes) {
  wchar_t text[32];
  swprintf(text, 32, L"%.1f MB", double(bytes) / (1024.0 * 1024.0));
  return text;
}

fs::path ExecutablePath() {
  std::wstring path(32768, L'\0');
  path.resize(GetModuleFileNameW(nullptr, path.data(), DWORD(path.size())));
  return path;
}

fs::path UpdateTempFolder() {
  wchar_t temp[MAX_PATH + 1];
  const DWORD length = GetTempPathW(MAX_PATH + 1, temp);
  return fs::path(std::wstring(temp, length)) / L"DOAP-Enhanced-update";
}

void CheckForUpdate() {
  UpdateState([](UpdateProgress& u) {
    u = UpdateProgress{};
    u.busy = true;
    u.checked = true;
    u.message = L"Looking for a new version...";
  });
  StepRunning(kStepCheck, L"Contacting github.com");
  std::wstring url = kUpdateUrl;
  if (const wchar_t* test = _wgetenv(L"DOAP_UPDATE_URL")) url = test;
  std::string json;
  std::wstring error;
  if (!HttpGet(url, &json, nullptr, nullptr, error)) {
    StepFailed(kStepCheck, L"Check failed", error);
    return;
  }
  const std::wstring latest = Widen(JsonString(json, "tag_name", 0));
  std::wstring exe_url, sums_url, exe_name;
  for (size_t at = 0;;) {
    size_t end = 0;
    const std::string asset = JsonString(json, "browser_download_url", at, &end);
    if (asset.empty()) break;
    at = end;
    const std::string name = asset.substr(asset.find_last_of('/') + 1);
    if (name.rfind("DOAP-Enhanced-Patcher-", 0) == 0 && name.size() > 4 &&
        name.compare(name.size() - 4, 4, ".exe") == 0) {
      exe_url = Widen(asset);
      exe_name = Widen(name);
    } else if (name == "SHA256SUMS.txt") {
      sums_url = Widen(asset);
    }
  }
  if (latest.empty() || exe_url.empty() || sums_url.empty()) {
    StepFailed(kStepCheck, L"Check failed", L"The latest release on GitHub has no patcher download.");
    return;
  }
  StepDone(kStepCheck, L"Latest version: " + latest);
  const bool newer = NewerVersion(latest, kVersion);
  UpdateState([&](UpdateProgress& u) {
    u.busy = false;
    u.latest = latest;
    u.available = newer;
    u.exe_url = exe_url;
    u.sums_url = sums_url;
    u.exe_name = exe_name;
    u.message = newer ? latest + L" is available. Press INSTALL UPDATE to download and install it."
                      : std::wstring(L"You have the latest version.");
  });
}

void InstallUpdate() {
  const UpdateProgress start = UpdateSnapshot();
  UpdateState([](UpdateProgress& u) {
    for (int step = kStepDownload; step < kStepCount; ++step) {
      u.state[step] = StepState::kWaiting;
      u.fraction[step] = 0;
      u.detail[step].clear();
    }
    u.busy = true;
    u.error = false;
    u.message = L"Installing " + u.latest + L"...";
  });
  std::error_code fs_error;
  const fs::path temp = UpdateTempFolder();
  fs::remove_all(temp, fs_error);
  fs::create_directories(temp, fs_error);
  const fs::path download = temp / start.exe_name;
  const auto cleanup = [&] {
    std::error_code ignored;
    fs::remove_all(temp, ignored);
  };

  StepRunning(kStepDownload, L"Contacting github.com");
  std::string sums;
  std::wstring error;
  if (!HttpGet(start.sums_url, &sums, nullptr, nullptr, error)) {
    StepFailed(kStepDownload, L"Download failed", error);
    return cleanup();
  }
  uint64_t last_percent = 101;
  const bool fetched = HttpGet(start.exe_url, nullptr, &download, [&](uint64_t received, uint64_t total) {
    const uint64_t percent = total ? received * 100 / total : 0;
    if (percent == last_percent) return;
    last_percent = percent;
    StepRunning(kStepDownload, Megabytes(received), total ? float(received) / float(total) : -1.0f);
  }, error);
  if (!fetched) {
    StepFailed(kStepDownload, L"Download failed", error);
    return cleanup();
  }
  StepDone(kStepDownload, Megabytes(fs::file_size(download, fs_error)) + L" downloaded");

  StepRunning(kStepVerify, L"Checking the download");
  std::string expected;
  {
    std::istringstream lines(sums);
    std::string line;
    const std::string name(start.exe_name.begin(), start.exe_name.end());
    while (std::getline(lines, line)) {
      std::istringstream fields(line);
      std::string hash, file;
      fields >> hash >> file;
      if (file == name) expected = hash;
    }
  }
  std::transform(expected.begin(), expected.end(), expected.begin(), [](char c) { return char(tolower(c)); });
  if (expected.empty() || Sha256Hex(download) != expected) {
    StepFailed(kStepVerify, L"Checksum does not match",
               L"The download is damaged or incomplete, so nothing was changed. Try again.");
    return cleanup();
  }
  StepDone(kStepVerify, L"SHA-256 matches");

  StepRunning(kStepInstall, L"Replacing the patcher");
  const fs::path exe = ExecutablePath();
  const std::wstring aside = exe.wstring() + kOldSuffix;
  fs::remove(aside, fs_error);
  if (!MoveFileExW(exe.c_str(), aside.c_str(), MOVEFILE_REPLACE_EXISTING)) {
    StepFailed(kStepInstall, L"Install failed",
               L"The patcher could not be replaced. Check that its folder is not read-only.");
    return cleanup();
  }
  if (!MoveFileExW(download.c_str(), exe.c_str(), MOVEFILE_COPY_ALLOWED | MOVEFILE_REPLACE_EXISTING)) {
    MoveFileExW(aside.c_str(), exe.c_str(), MOVEFILE_REPLACE_EXISTING);
    StepFailed(kStepInstall, L"Install failed", L"The update could not be installed. Your version was restored.");
    return cleanup();
  }
  cleanup();
  StepDone(kStepInstall, L"Patcher updated");
  UpdateState([](UpdateProgress& u) {
    u.busy = false;
    u.installed = true;
    u.available = false;
    u.message = L"Updated to " + u.latest + L". Restart, then choose your ISO and press PATCH to get the new fixes.";
  });
}

void StartUpdateThread(void (*work)()) {
  {
    std::lock_guard lock(g_update_mutex);
    if (g_update.busy) return;
    g_update.busy = true;
  }
  SetTimer(g_window, kUpdateTimer, 40, nullptr);
  std::thread([work] {
    try {
      work();
    } catch (const std::exception& exception) {
      const std::string what = exception.what();
      UpdateState([&](UpdateProgress& u) {
        for (auto& state : u.state)
          if (state == StepState::kRunning) state = StepState::kFailed;
        u.message = L"The update stopped unexpectedly (" + Widen(what) + L").";
        u.error = true;
        u.busy = false;
      });
    }
  }).detach();
}

// Starts the updated patcher and closes this one.
void RestartPatcher() {
  STARTUPINFOW startup{};
  startup.cb = sizeof(startup);
  PROCESS_INFORMATION process{};
  const fs::path exe = ExecutablePath();
  std::wstring command = L"\"" + exe.wstring() + L"\"";
  if (CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, 0, nullptr, exe.parent_path().c_str(), &startup,
                     &process)) {
    CloseHandle(process.hProcess);
    CloseHandle(process.hThread);
    DestroyWindow(g_window);
  }
}

// Removes the previous patcher and the temp folder of an interrupted update. Right after an update
// the old patcher may still be closing, so this retries for a while.
void CleanUpAfterUpdate() {
  std::thread([] {
    std::error_code error;
    fs::remove_all(UpdateTempFolder(), error);
    const std::wstring aside = ExecutablePath().wstring() + kOldSuffix;
    for (int attempt = 0; attempt < 20; ++attempt) {
      if (!fs::exists(aside, error) || fs::remove(aside, error)) return;
      Sleep(500);
    }
  }).detach();
}

void OpenUpdatePage() {
  g_page = Page::kUpdate;
  // Opening the page is what starts the check (the only time the patcher goes online).
  const UpdateProgress u = UpdateSnapshot();
  if (!u.busy && !u.installed) StartUpdateThread(CheckForUpdate);
}

void DrawStepBar(Graphics& g, const RectF& r, float fraction, StepState state) {
  GraphicsPath path;
  RoundedRect(path, r, r.Height / 2);
  SolidBrush track(Color(200, 4, 22, 54));
  g.FillPath(&track, &path);
  const bool failed = state == StepState::kFailed;
  RectF fill_rect = r;
  if (state == StepState::kRunning && fraction < 0) {
    const float phase = float(GetTickCount64() % 1400) / 1400.0f;
    fill_rect.Width = r.Width * 0.25f;
    fill_rect.X = r.X + (r.Width - fill_rect.Width) * (0.5f - 0.5f * std::cos(phase * 6.2831853f));
  } else {
    const float shown = state == StepState::kWaiting ? 0.0f : failed ? 1.0f : fraction;
    fill_rect.Width = r.Width * std::clamp(shown, 0.0f, 1.0f);
  }
  if (fill_rect.Width >= 1) {
    GraphicsPath fill_path;
    RoundedRect(fill_path, fill_rect, r.Height / 2);
    LinearGradientBrush fill(PointF(0, r.Y), PointF(0, r.Y + r.Height), failed ? Color(255, 240, 90, 120) : kAccentTop,
                             failed ? Color(255, 170, 30, 70) : kAccentBottom);
    fill.SetWrapMode(WrapModeTileFlipXY);
    g.FillPath(&fill, &fill_path);
  }
  Pen border(Color(150, 160, 210, 255), 1.5f);
  g.DrawPath(&border, &path);
}

// Installed and latest version, the four steps with progress bars, and the button for the next step.
void PaintUpdate(Graphics& g) {
  const UpdateProgress u = UpdateSnapshot();
  DrawText(g, L"UPDATE", RectF(300, 46, 1000, 56), 42, true, kWhite);
  const RectF panel(150, 130, 1300, 650);
  GraphicsPath panel_path;
  RoundedRect(panel_path, panel, 30);
  SolidBrush panel_fill(Color(226, 8, 36, 84));
  g.FillPath(&panel_fill, &panel_path);
  Pen panel_border(Color(170, 140, 210, 255), 2.5f);
  g.DrawPath(&panel_border, &panel_path);
  DrawText(g, std::wstring(L"Installed version:  ") + kVersion, RectF(190, 146, 560, 46), 26, true, kWhite,
           StringAlignmentNear);
  DrawText(g, L"Latest version:  " + (u.latest.empty() ? std::wstring(u.busy ? L"checking..." : L"-") : u.latest),
           RectF(850, 146, 560, 46), 26, true, kWhite, StringAlignmentFar);
  const Color headline = u.error ? Color(255, 255, 160, 180) : u.available || u.installed ? kAccentTop : kWhite;
  DrawText(g, u.message, RectF(190, 198, 1220, 52), 25, true, headline, StringAlignmentNear);
  static const wchar_t* kStepNames[kStepCount] = {L"Check for updates", L"Download", L"Verify download", L"Install"};
  for (int step = 0; step < kStepCount; ++step) {
    const float y = 268.0f + 92.0f * float(step);
    if (step) {
      Pen line(Color(70, 160, 210, 255), 1.5f);
      g.DrawLine(&line, 186.0f, y - 12, 1414.0f, y - 12);
    }
    const StepState state = u.state[step];
    const RectF badge(192, y + 8, 48, 48);
    SolidBrush badge_fill(state == StepState::kDone      ? kOcean
                          : state == StepState::kFailed  ? Color(255, 170, 30, 70)
                          : state == StepState::kRunning ? kAqua
                                                         : Color(215, 24, 58, 112));
    g.FillEllipse(&badge_fill, badge);
    Pen badge_border(WithAlpha(kWhite, 200), 2.0f);
    g.DrawEllipse(&badge_border, badge);
    DrawText(g, state == StepState::kDone ? L"✓" : state == StepState::kFailed ? L"✕" : std::to_wstring(step + 1),
             badge, 24, true, kWhite, StringAlignmentCenter, StringAlignmentCenter, false);
    DrawText(g, kStepNames[step], RectF(258, y, 300, 64), 25, true,
             state == StepState::kWaiting ? Color(255, 160, 185, 215) : kWhite, StringAlignmentNear);
    DrawStepBar(g, RectF(570, y + 10, 600, 24), u.fraction[step], state);
    DrawText(g, u.detail[step], RectF(570, y + 38, 600, 28), 18, false, Color(255, 205, 228, 250), StringAlignmentNear,
             StringAlignmentCenter, false);
    if (state == StepState::kRunning && u.fraction[step] >= 0)
      DrawText(g, std::to_wstring(int(u.fraction[step] * 100 + 0.5f)) + L"%", RectF(1184, y + 4, 226, 36), 22, true,
               kWhite, StringAlignmentFar);
  }
  DrawText(g,
           L"Opening this page contacts github.com to look for a new version; nothing else is sent. Updating replaces "
           L"this patcher only. Your ISOs are not changed.",
           RectF(190, 636, 1220, 130), 20, false, Color(255, 205, 228, 250), StringAlignmentNear, StringAlignmentCenter,
           false);
  if (u.busy) {
    DrawText(g, L"Please wait...", RectF(460, 800, 990, 76), 24, true, kWhite, StringAlignmentFar);
    return;  // no Back while an update runs
  }
  DrawButton(g, RectF(150, 800, 290, 76), L"BACK", 33, [] { g_page = Page::kMain; });
  if (u.installed) {
    DrawButton(g, RectF(1010, 800, 440, 76), L"RESTART PATCHER", 30, RestartPatcher, ButtonStyle::kAccent);
  } else if (u.available) {
    DrawButton(g, RectF(1010, 800, 440, 76), L"INSTALL UPDATE", 30, [] { StartUpdateThread(InstallUpdate); },
               ButtonStyle::kAccent);
  } else if (u.checked) {
    DrawButton(g, RectF(1010, 800, 440, 76), L"CHECK AGAIN", 30, [] { StartUpdateThread(CheckForUpdate); });
  }
}
