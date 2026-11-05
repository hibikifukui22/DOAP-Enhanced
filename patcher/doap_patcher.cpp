// Dead or Alive Paradise 60 FPS patcher.
//
// A custom-drawn, resizable window in the style of the DOAX2 Recomp launcher: everything is laid
// out in a 1600 x 900 design space, scaled uniformly with the window and drawn with GDI+.
// Patching streams the clean ISO to a new file in one pass, XORing the embedded payload runs (see
// build_payload.py) and hashing source and output as it goes. The source is never written to.
#define UNICODE
#define NOMINMAX
#include <windows.h>
#include <objidl.h>
#include <bcrypt.h>
#include <commdlg.h>
#include <shellapi.h>
#include <shlwapi.h>
#include <winhttp.h>

#include <algorithm>
// gdiplus.h expects min/max, which NOMINMAX removes.
namespace Gdiplus {
using std::max;
using std::min;
}  // namespace Gdiplus
#include <gdiplus.h>

#include <array>
#include <cmath>
#include <fstream>
#include <sstream>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

using namespace Gdiplus;
namespace fs = std::filesystem;

constexpr const wchar_t* kVersion = L"v1.0.2";
constexpr const wchar_t* kAppName = L"Dead or Alive Paradise Game Patcher";
constexpr const wchar_t* kOutputName = L"Dead or Alive Paradise 60 FPS.iso";
constexpr float kDesignW = 1600.0f;
constexpr float kDesignH = 900.0f;
constexpr int kIdIcon = 1, kIdBackground = 101, kIdPayload = 102;
constexpr UINT_PTR kProgressTimer = 1;
constexpr UINT kPatchFinished = WM_APP + 1;

const Color kOcean(255, 30, 120, 214);
const Color kDeepBlue(255, 13, 79, 168);
const Color kAqua(255, 61, 181, 240);
const Color kAccentTop(255, 96, 205, 255);
const Color kAccentBottom(255, 18, 112, 232);
const Color kWhite(255, 255, 255, 255);

using Sha = std::array<uint8_t, 32>;

struct Run {
  uint64_t offset;
  uint32_t length;
  uint32_t group;  // Unused: the patcher always applies every run, high poly models included.
  const uint8_t* data;
};

// An earlier release's ISO and what turns it into the current one.
struct Upgrade {
  std::wstring name;  // "v1.0.1"
  Sha iso{}, from_runs{}, to_runs{};
  std::vector<Run> runs;
};

struct Payload {
  uint64_t iso_size = 0;
  Sha clean{}, normal{}, high_poly{}, source_runs{};
  std::vector<Run> runs;
  std::vector<Upgrade> upgrades;
};

enum class IsoKind { kUnknown, kClean, kUpgrade, kLatest };

struct IsoInfo {
  IsoKind kind = IsoKind::kUnknown;
  const Upgrade* upgrade = nullptr;
};

struct Hit {
  RectF rect;
  std::function<void()> action;
  int id;
};

struct InfoPopup {
  std::wstring title, text;
};

HWND g_window = nullptr;
std::unique_ptr<Bitmap> g_background;
std::vector<Hit> g_hits;
int g_hover = -1;
std::unique_ptr<InfoPopup> g_info;
Payload g_payload;

std::wstring g_source;
fs::path g_finished_output;
IsoKind g_source_kind = IsoKind::kUnknown;

// Shared with the patch thread.
std::mutex g_status_mutex;
std::wstring g_status = L"Choose your US ISO, clean or patched, or drop it on this window.";
bool g_status_error = false;
std::atomic<bool> g_busy{false};
std::atomic<float> g_progress{0.0f};
std::thread g_worker;

void SetStatus(const std::wstring& text, bool error = false) {
  std::lock_guard lock(g_status_mutex);
  g_status = text;
  g_status_error = error;
}

const void* LoadResourceData(int id, DWORD* size) {
  HRSRC resource = FindResourceW(nullptr, MAKEINTRESOURCEW(id), RT_RCDATA);
  if (!resource) return nullptr;
  HGLOBAL handle = LoadResource(nullptr, resource);
  *size = SizeofResource(nullptr, resource);
  return handle ? LockResource(handle) : nullptr;
}

std::unique_ptr<Bitmap> LoadImageResource(int id) {
  DWORD size = 0;
  const void* data = LoadResourceData(id, &size);
  if (!data || !size) return nullptr;
  IStream* stream = SHCreateMemStream(static_cast<const BYTE*>(data), size);
  if (!stream) return nullptr;
  std::unique_ptr<Bitmap> image(Bitmap::FromStream(stream));
  stream->Release();
  return image;
}

bool LoadPayload() {
  DWORD size = 0;
  const auto* base = static_cast<const uint8_t*>(LoadResourceData(kIdPayload, &size));
  if (!base || size < 148 || std::memcmp(base, "DOAP", 4) != 0) return false;
  const uint8_t* at = base + 8;
  std::memcpy(&g_payload.iso_size, at, 8);
  at += 8;
  for (Sha* sha : {&g_payload.clean, &g_payload.normal, &g_payload.high_poly, &g_payload.source_runs}) {
    std::memcpy(sha->data(), at, 32);
    at += 32;
  }
  uint32_t count = 0;
  std::memcpy(&count, at, 4);
  at += 4;
  const uint8_t* data = at + size_t(count) * 16;
  for (uint32_t i = 0; i < count; ++i, at += 16) {
    Run run{};
    std::memcpy(&run.offset, at, 8);
    std::memcpy(&run.length, at + 8, 4);
    std::memcpy(&run.group, at + 12, 4);
    run.data = data;
    data += run.length;
    g_payload.runs.push_back(run);
  }
  const auto read_runs = [&](std::vector<Run>& runs, const uint8_t*& from) {
    uint32_t run_count = 0;
    std::memcpy(&run_count, from, 4);
    from += 4;
    const uint8_t* run_data = from + size_t(run_count) * 16;
    for (uint32_t i = 0; i < run_count; ++i, from += 16) {
      Run run{};
      std::memcpy(&run.offset, from, 8);
      std::memcpy(&run.length, from + 8, 4);
      std::memcpy(&run.group, from + 12, 4);
      run.data = run_data;
      run_data += run.length;
      runs.push_back(run);
    }
    from = run_data;
  };
  at = data;
  uint32_t upgrades = 0;
  std::memcpy(&upgrades, at, 4);
  at += 4;
  for (uint32_t i = 0; i < upgrades; ++i) {
    Upgrade upgrade;
    for (int k = 0; k < 16 && at[k]; ++k) upgrade.name += wchar_t(at[k]);
    at += 16;
    for (Sha* sha : {&upgrade.iso, &upgrade.from_runs, &upgrade.to_runs}) {
      std::memcpy(sha->data(), at, 32);
      at += 32;
    }
    read_runs(upgrade.runs, at);
    g_payload.upgrades.push_back(std::move(upgrade));
  }
  return at == base + size;
}

class Sha256 {
 public:
  Sha256() {
    BCryptOpenAlgorithmProvider(&algorithm_, BCRYPT_SHA256_ALGORITHM, nullptr, 0);
    BCryptCreateHash(algorithm_, &hash_, nullptr, 0, nullptr, 0, 0);
  }
  ~Sha256() {
    if (hash_) BCryptDestroyHash(hash_);
    if (algorithm_) BCryptCloseAlgorithmProvider(algorithm_, 0);
  }
  Sha256(const Sha256&) = delete;
  Sha256& operator=(const Sha256&) = delete;
  void Add(const void* data, size_t size) {
    BCryptHashData(hash_, static_cast<PUCHAR>(const_cast<void*>(data)), ULONG(size), 0);
  }
  Sha Finish() {
    Sha out{};
    BCryptFinishHash(hash_, out.data(), ULONG(out.size()), 0);
    return out;
  }

 private:
  BCRYPT_ALG_HANDLE algorithm_ = nullptr;
  BCRYPT_HASH_HANDLE hash_ = nullptr;
};

class File {
 public:
  File(const fs::path& path, bool write) {
    handle_ = CreateFileW(path.c_str(), write ? GENERIC_WRITE : GENERIC_READ, write ? 0 : FILE_SHARE_READ, nullptr,
                          write ? CREATE_ALWAYS : OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
  }
  ~File() { Close(); }
  File(const File&) = delete;
  File& operator=(const File&) = delete;
  bool ok() const { return handle_ != INVALID_HANDLE_VALUE; }
  void Close() {
    if (ok()) CloseHandle(handle_);
    handle_ = INVALID_HANDLE_VALUE;
  }
  uint64_t Size() const {
    LARGE_INTEGER size{};
    GetFileSizeEx(handle_, &size);
    return uint64_t(size.QuadPart);
  }
  bool Seek(uint64_t offset) {
    LARGE_INTEGER at;
    at.QuadPart = LONGLONG(offset);
    return SetFilePointerEx(handle_, at, nullptr, FILE_BEGIN) != 0;
  }
  bool Read(void* data, DWORD size) {
    DWORD done = 0;
    return ReadFile(handle_, data, size, &done, nullptr) && done == size;
  }
  bool Write(const void* data, DWORD size) {
    DWORD done = 0;
    return WriteFile(handle_, data, size, &done, nullptr) && done == size;
  }

 private:
  HANDLE handle_ = INVALID_HANDLE_VALUE;
};

fs::path OutputPath(const fs::path& source) { return source.parent_path() / kOutputName; }

// The hash of the source bytes under these runs. Matching it proves the XOR is safe, even on a dump
// that differs elsewhere (padding, for example).
bool RunsHash(File& source, const std::vector<Run>& runs, Sha& out) {
  Sha256 sha;
  std::vector<uint8_t> buffer;
  for (const Run& run : runs) {
    buffer.resize(run.length);
    if (!source.Seek(run.offset) || !source.Read(buffer.data(), run.length)) return false;
    sha.Add(buffer.data(), run.length);
  }
  out = sha.Finish();
  return true;
}

// Works out what the ISO is: clean, an earlier release, or already the current release.
IsoInfo DetectIso(File& source) {
  IsoInfo info;
  Sha sha{};
  if (RunsHash(source, g_payload.runs, sha) && sha == g_payload.source_runs) {
    info.kind = IsoKind::kClean;
    return info;
  }
  bool latest = false;
  for (const Upgrade& upgrade : g_payload.upgrades) {
    if (!RunsHash(source, upgrade.runs, sha)) continue;
    if (sha == upgrade.from_runs) {
      info.kind = IsoKind::kUpgrade;
      info.upgrade = &upgrade;
      return info;
    }
    latest = latest || sha == upgrade.to_runs;
  }
  if (latest) info.kind = IsoKind::kLatest;
  return info;
}

void Fail(const std::wstring& text) {
  SetStatus(text, true);
  g_progress = 0.0f;
}

void PatchWorker(fs::path source_path, fs::path output_path) {
  const fs::path temporary = output_path.wstring() + L".tmp";
  bool success = false;
  [&] {
    File source(source_path, false);
    if (!source.ok()) return Fail(L"The ISO could not be opened. Is it in use by another program?");
    if (source.Size() != g_payload.iso_size)
      return Fail(L"This ISO is the wrong size. It needs Dead or Alive Paradise (USA, ULUS-10521).");
    SetStatus(L"Checking the ISO...");
    const IsoInfo info = DetectIso(source);
    if (info.kind == IsoKind::kLatest) return SetStatus(std::wstring(L"This ISO is already on ") + kVersion + L".");
    if (info.kind == IsoKind::kUnknown)
      return Fail(L"This ISO isn't the clean USA release or a known patched version, so it can't be patched.");
    const std::vector<Run>& runs = info.kind == IsoKind::kUpgrade ? info.upgrade->runs : g_payload.runs;
    const Sha& known_source = info.kind == IsoKind::kUpgrade ? info.upgrade->iso : g_payload.clean;
    File output(temporary, true);
    if (!output.ok()) return Fail(L"The new ISO can't be written in that folder. Check it isn't read-only.");
    SetStatus(L"Writing the patched ISO...");
    source.Seek(0);
    Sha256 source_sha, output_sha;
    std::vector<uint8_t> buffer(8u << 20);
    for (uint64_t at = 0; at < g_payload.iso_size;) {
      const DWORD size = DWORD(std::min<uint64_t>(buffer.size(), g_payload.iso_size - at));
      if (!source.Read(buffer.data(), size)) return Fail(L"The ISO could not be read to the end.");
      source_sha.Add(buffer.data(), size);
      for (const Run& run : runs) {
        const uint64_t from = std::max(at, run.offset), to = std::min(at + size, run.offset + run.length);
        for (uint64_t i = from; i < to; ++i) buffer[size_t(i - at)] ^= run.data[i - run.offset];
      }
      output_sha.Add(buffer.data(), size);
      if (!output.Write(buffer.data(), size)) return Fail(L"Writing failed. Is the drive full?");
      at += size;
      g_progress = float(double(at) / double(g_payload.iso_size));
    }
    output.Close();
    source.Close();
    const bool canonical = source_sha.Finish() == known_source;
    if (output_sha.Finish() != g_payload.high_poly && canonical)
      return Fail(L"The finished ISO failed verification, so it was removed. Your original is untouched.");
    if (!MoveFileExW(temporary.c_str(), output_path.c_str(), MOVEFILE_REPLACE_EXISTING))
      return Fail(L"The finished ISO could not be renamed. Is the old one open in an emulator?");
    success = true;
    SetStatus(L"Done: " + output_path.filename().wstring() + L"  (set PPSSPP's CPU clock to 444 MHz or higher)");
  }();
  if (!success) {
    std::error_code ignored;
    fs::remove(temporary, ignored);
  }
  PostMessageW(g_window, kPatchFinished, success, 0);
}

void SelectSource(const std::wstring& path) {
  g_source = path;
  g_finished_output.clear();
  g_progress = 0.0f;
  g_source_kind = IsoKind::kUnknown;
  File source(path, false);
  if (!source.ok()) return SetStatus(L"The ISO could not be opened. Is it in use by another program?", true);
  if (source.Size() != g_payload.iso_size)
    return SetStatus(L"This ISO is the wrong size. It needs Dead or Alive Paradise (USA, ULUS-10521).", true);
  const IsoInfo info = DetectIso(source);
  g_source_kind = info.kind;
  switch (info.kind) {
    case IsoKind::kClean:
      return SetStatus(std::wstring(L"Clean ISO found. PATCH will install ") + kVersion + L".");
    case IsoKind::kUpgrade:
      return SetStatus(info.upgrade->name + L" ISO found. PATCH will update it to " + kVersion + L".");
    case IsoKind::kLatest:
      return SetStatus(std::wstring(L"This ISO is already on ") + kVersion + L". Nothing to do.");
    default:
      return SetStatus(L"This ISO isn't the clean USA release or a known patched version.", true);
  }
}

void Browse() {
  if (g_busy) return;
  wchar_t path[MAX_PATH * 4] = L"";
  OPENFILENAMEW dialog{};
  dialog.lStructSize = sizeof(dialog);
  dialog.hwndOwner = g_window;
  dialog.lpstrFilter = L"PSP disc image (*.iso)\0*.iso\0All files\0*.*\0";
  dialog.lpstrFile = path;
  dialog.nMaxFile = DWORD(std::size(path));
  dialog.lpstrTitle = L"Choose your Dead or Alive Paradise ISO";
  dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_HIDEREADONLY;
  if (GetOpenFileNameW(&dialog)) SelectSource(path);
}

void StartPatch() {
  if (g_busy) return;
  if (g_source.empty()) return Browse();
  if (g_source_kind == IsoKind::kLatest || g_source_kind == IsoKind::kUnknown) return;
  const fs::path source = g_source, output = OutputPath(source);
  std::error_code error;
  if (g_source_kind == IsoKind::kClean && fs::equivalent(source, output, error))
    return SetStatus(L"That clean ISO has the patched file's name. Rename it first.", true);
  if (fs::exists(output, error)) {
    const std::wstring question = output.filename().wstring() + L" already exists. Replace it?";
    if (MessageBoxW(g_window, question.c_str(), kAppName, MB_YESNO | MB_ICONQUESTION) != IDYES) return;
  }
  ULARGE_INTEGER free_space{};
  if (GetDiskFreeSpaceExW(source.parent_path().c_str(), &free_space, nullptr, nullptr) &&
      free_space.QuadPart < g_payload.iso_size + (16u << 20))
    return SetStatus(L"That drive needs about 900 MB free for the new ISO.", true);
  if (g_worker.joinable()) g_worker.join();
  g_busy = true;
  g_finished_output.clear();
  g_progress = 0.0f;
  SetTimer(g_window, kProgressTimer, 100, nullptr);
  g_worker = std::thread(PatchWorker, source, output);
}

void RoundedRect(GraphicsPath& path, const RectF& r, float radius) {
  const float d = std::min(radius * 2, std::min(r.Width, r.Height));
  path.Reset();
  path.AddArc(r.X, r.Y, d, d, 180, 90);
  path.AddArc(r.X + r.Width - d, r.Y, d, d, 270, 90);
  path.AddArc(r.X + r.Width - d, r.Y + r.Height - d, d, d, 0, 90);
  path.AddArc(r.X, r.Y + r.Height - d, d, d, 90, 90);
  path.CloseFigure();
}

Color WithAlpha(const Color& c, BYTE alpha) { return Color(alpha, c.GetR(), c.GetG(), c.GetB()); }

Color Lighter(const Color& c, float amount) {
  const auto mix = [amount](BYTE v) { return BYTE(v + (255 - v) * amount); };
  return Color(c.GetA(), mix(c.GetR()), mix(c.GetG()), mix(c.GetB()));
}

void DrawText(Graphics& g, const std::wstring& text, const RectF& rect, float size, bool bold, const Color& color,
              StringAlignment align = StringAlignmentCenter, StringAlignment line = StringAlignmentCenter,
              bool shadow = true, StringTrimming trimming = StringTrimmingEllipsisWord) {
  FontFamily family(L"Segoe UI");
  Font font(&family, size, bold ? FontStyleBold : FontStyleRegular, UnitPixel);
  StringFormat format;
  format.SetAlignment(align);
  format.SetLineAlignment(line);
  format.SetTrimming(trimming);
  if (shadow) {
    SolidBrush shade(Color(110, 0, 20, 50));
    RectF offset = rect;
    offset.Offset(1.5f, 2.0f);
    g.DrawString(text.c_str(), -1, &font, offset, &format, &shade);
  }
  SolidBrush brush(color);
  g.DrawString(text.c_str(), -1, &font, rect, &format, &brush);
}

int AddHit(const RectF& rect, std::function<void()> action) {
  const int id = int(g_hits.size());
  g_hits.push_back({rect, std::move(action), id});
  return id;
}

enum class ButtonStyle { kNormal, kAccent, kSelected, kUnselected, kDisabled };

void DrawButton(Graphics& g, const RectF& r, const std::wstring& label, float text_size, std::function<void()> action,
                ButtonStyle style = ButtonStyle::kNormal) {
  const bool disabled = style == ButtonStyle::kDisabled;
  const int id = disabled ? -2 : AddHit(r, std::move(action));
  const bool hover = id == g_hover;
  const bool accent = style == ButtonStyle::kAccent, selected = style == ButtonStyle::kSelected;
  Color top = accent ? kAccentTop : selected ? kAqua : kOcean;
  Color bottom = accent ? kAccentBottom : selected ? kOcean : kDeepBlue;
  if (style == ButtonStyle::kUnselected || disabled) {
    top = Color(215, 24, 58, 112);
    bottom = Color(215, 8, 36, 84);
  }
  if (hover) { top = Lighter(top, 0.25f); bottom = Lighter(bottom, 0.18f); }
  GraphicsPath path;
  RectF shadow = r;
  shadow.Offset(0, 4);
  RoundedRect(path, shadow, r.Height / 2);
  SolidBrush shadow_brush(Color(70, 0, 20, 60));
  g.FillPath(&shadow_brush, &path);
  RoundedRect(path, r, r.Height / 2);
  LinearGradientBrush fill(PointF(0, r.Y), PointF(0, r.Y + r.Height), top, bottom);
  fill.SetWrapMode(WrapModeTileFlipXY);
  g.FillPath(&fill, &path);
  // Glossy highlight on the upper half.
  RectF gloss(r.X + r.Height * 0.25f, r.Y + 3, r.Width - r.Height * 0.5f, r.Height * 0.42f);
  GraphicsPath gloss_path;
  RoundedRect(gloss_path, gloss, gloss.Height / 2);
  LinearGradientBrush gloss_brush(PointF(0, gloss.Y), PointF(0, gloss.Y + gloss.Height), Color(110, 255, 255, 255),
                                  Color(0, 255, 255, 255));
  gloss_brush.SetWrapMode(WrapModeTileFlipXY);
  g.FillPath(&gloss_brush, &gloss_path);
  Pen border(WithAlpha(kWhite, BYTE(selected || hover ? 255 : style == ButtonStyle::kUnselected || disabled ? 110 : 170)),
             selected ? 3.5f : 2.0f);
  g.DrawPath(&border, &path);
  DrawText(g, label, r, text_size, true,
           style == ButtonStyle::kUnselected || disabled ? Color(255, 190, 210, 235) : kWhite);
}

void DrawProgressBar(Graphics& g, const RectF& r, float fraction, bool failed) {
  GraphicsPath path;
  RoundedRect(path, r, r.Height / 2);
  SolidBrush track(Color(200, 4, 22, 54));
  g.FillPath(&track, &path);
  RectF fill_rect = r;
  fill_rect.Width = r.Width * std::clamp(failed ? 1.0f : fraction, 0.0f, 1.0f);
  if (fill_rect.Width >= 1) {
    GraphicsPath fill_path;
    RoundedRect(fill_path, fill_rect, r.Height / 2);
    LinearGradientBrush fill(PointF(0, r.Y), PointF(0, r.Y + r.Height),
                             failed ? Color(255, 240, 90, 120) : kAccentTop,
                             failed ? Color(255, 170, 30, 70) : kAccentBottom);
    fill.SetWrapMode(WrapModeTileFlipXY);
    g.FillPath(&fill, &fill_path);
  }
  Pen border(Color(150, 160, 210, 255), 1.5f);
  g.DrawPath(&border, &path);
}

// The open explanation popup: a large card over the dimmed page.
void DrawInfoPopupOverlay(Graphics& g) {
  if (!g_info) return;
  g_hits.clear();
  const RectF everywhere(-4000, -4000, 9600, 8900);
  AddHit(everywhere, [] { g_info.reset(); });
  SolidBrush dim(Color(150, 4, 18, 44));
  g.FillRectangle(&dim, everywhere);
  const RectF card(220, 100, 1160, 700);
  AddHit(card, [] {});
  GraphicsPath path;
  RoundedRect(path, card, 36);
  SolidBrush fill(Color(242, 8, 36, 84));
  g.FillPath(&fill, &path);
  Pen border(Color(220, 140, 210, 255), 3.0f);
  g.DrawPath(&border, &path);
  DrawText(g, g_info->title, RectF(card.X + 50, card.Y + 34, card.Width - 100, 60), 44, true, kWhite,
           StringAlignmentNear, StringAlignmentNear);
  Pen line(Color(110, 160, 210, 255), 2.0f);
  g.DrawLine(&line, card.X + 50, card.Y + 112, card.X + card.Width - 50, card.Y + 112);
  DrawText(g, g_info->text, RectF(card.X + 50, card.Y + 136, card.Width - 100, card.Height - 250), 28, false,
           Color(255, 225, 238, 255), StringAlignmentNear, StringAlignmentNear, false);
  DrawButton(g, RectF(card.X + (card.Width - 300) / 2, card.Y + card.Height - 104, 300, 74), L"CLOSE", 30,
             [] { g_info.reset(); });
}

void ShowInfo(const wchar_t* title, const wchar_t* text) {
  g_info = std::make_unique<InfoPopup>(InfoPopup{title, text});
}

#include "doap_update.h"

// Title top left; ISO row, status and progress above the bottom row of buttons, as on the DOAX2
// launcher's main page.
void PaintMain(Graphics& g) {
  DrawText(g, L"DEAD OR ALIVE", RectF(40, 22, 500, 52), 40, true, kWhite);
  DrawText(g, L"PARADISE", RectF(40, 62, 500, 112), 92, true, kWhite);
  DrawText(g, L"GAME PATCHER", RectF(40, 170, 500, 44), 31, true, Color(255, 170, 225, 255));
  DrawText(g, kVersion, RectF(1380, 14, 204, 38), 21, true, kWhite, StringAlignmentFar);

  const bool busy = g_busy;
  // Source ISO.
  DrawText(g, L"USA ISO (ULUS-10521), CLEAN OR PATCHED", RectF(60, 530, 900, 34), 24, true, kWhite, StringAlignmentNear);
  const RectF field(60, 568, 1230, 62);
  const int field_id = AddHit(field, [] { Browse(); });
  GraphicsPath path;
  RoundedRect(path, field, field.Height / 2);
  SolidBrush field_fill(Color(205, 8, 36, 84));
  g.FillPath(&field_fill, &path);
  Pen field_border(WithAlpha(Color(255, 120, 200, 255), BYTE(field_id == g_hover ? 255 : 150)), 2.5f);
  g.DrawPath(&field_border, &path);
  RectF field_text = field;
  field_text.Inflate(-30, 0);
  DrawText(g, g_source.empty() ? L"No ISO selected" : g_source, field_text, 25, false,
           g_source.empty() ? Color(255, 170, 195, 225) : kWhite, StringAlignmentNear, StringAlignmentCenter, false,
           StringTrimmingEllipsisPath);
  DrawButton(g, RectF(1310, 568, 230, 62), L"BROWSE", 27, [] { Browse(); },
             busy ? ButtonStyle::kDisabled : ButtonStyle::kNormal);

  // Status and progress.
  std::wstring status;
  bool error;
  {
    std::lock_guard lock(g_status_mutex);
    status = g_status;
    error = g_status_error;
  }
  DrawText(g, status, RectF(60, 640, 1480, 36), 24, true, error ? Color(255, 255, 170, 185) : kWhite);
  DrawProgressBar(g, RectF(60, 682, 1480, 20), g_progress, error);

  // Bottom row: PATCH in the middle with VERY IMPORTANT above it, one button either side.
  DrawButton(g, RectF(566, 716, 468, 54), L"VERY IMPORTANT", 28, [] {
    ShowInfo(L"Very important",
             L"High poly models are used everywhere in the game, including all the places that originally used lower "
             L"poly ones, so the game needs more emulated CPU power than usual.\n\n"
             L"In PPSSPP, set Settings > System > \"Change emulated PSP's CPU clock\" to 444 MHz or higher. At the "
             L"default clock, volleyball drops to 30 FPS and runs at half speed.");
  }, ButtonStyle::kSelected);
  DrawButton(g, RectF(566, 780, 468, 92), busy ? L"PATCHING..." : L"PATCH", busy ? 40.0f : 54.0f, [] { StartPatch(); },
             busy ? ButtonStyle::kDisabled : ButtonStyle::kAccent);
  DrawButton(g, RectF(60, 780, 470, 92), L"PATCH INFORMATION", 29, [] {
    ShowInfo(L"Patch information",
             L"Built for the PPSSPP emulator only. PSP hardware is unsupported and untested, so emulation is "
             L"recommended.\n\n"
             L"Uncapped to 60 FPS game wide.\n\n"
             L"Everywhere the game used lower poly models of the girls now uses the higher poly models from the "
             L"cutscenes, bringing it closer to DOAX2.\n\n"
             L"Minigames are fixed but still need testing.\n\n"
             L"Casino payouts are 10x, and the winnings shown on screen match what you receive.");
  });
  const bool can_open = !g_finished_output.empty();
  DrawButton(g, RectF(1070, 716, 470, 54), L"UPDATE", 28, [] { OpenUpdatePage(); },
             busy ? ButtonStyle::kDisabled : ButtonStyle::kNormal);
  DrawButton(g, RectF(1070, 780, 470, 92), L"SHOW PATCHED ISO", 29, [] {
    const std::wstring arguments = L"/select,\"" + g_finished_output.wstring() + L"\"";
    ShellExecuteW(nullptr, L"open", L"explorer.exe", arguments.c_str(), nullptr, SW_SHOWNORMAL);
  }, can_open ? ButtonStyle::kNormal : ButtonStyle::kDisabled);
}

// The design space is scaled uniformly to fit the window and centred; the background covers the
// whole window (cropped, not stretched).
struct Layout {
  float scale = 1;
  float x = 0;
  float y = 0;
};
Layout g_layout;
std::unique_ptr<Bitmap> g_background_cache;

void UpdateLayout(HWND window) {
  RECT client;
  GetClientRect(window, &client);
  const float width = float(client.right - client.left), height = float(client.bottom - client.top);
  g_layout.scale = std::max(0.05f, std::min(width / kDesignW, height / kDesignH));
  g_layout.x = (width - kDesignW * g_layout.scale) / 2;
  g_layout.y = (height - kDesignH * g_layout.scale) / 2;
  g_background_cache.reset();
}

void Paint(HWND window) {
  PAINTSTRUCT ps;
  HDC hdc = BeginPaint(window, &ps);
  RECT client;
  GetClientRect(window, &client);
  const int width = std::max(1, int(client.right - client.left));
  const int height = std::max(1, int(client.bottom - client.top));
  if (!g_background_cache || int(g_background_cache->GetWidth()) != width ||
      int(g_background_cache->GetHeight()) != height) {
    g_background_cache = std::make_unique<Bitmap>(width, height, PixelFormat32bppPARGB);
    Graphics bg(g_background_cache.get());
    bg.SetInterpolationMode(InterpolationModeHighQualityBicubic);
    bg.SetPixelOffsetMode(PixelOffsetModeHighQuality);
    if (g_background) {
      const float iw = float(g_background->GetWidth()), ih = float(g_background->GetHeight());
      const float cover = std::max(float(width) / iw, float(height) / ih);
      const float dw = iw * cover, dh = ih * cover;
      bg.DrawImage(g_background.get(), RectF((float(width) - dw) / 2, (float(height) - dh) / 2, dw, dh));
    } else {
      SolidBrush fill(kDeepBlue);
      bg.FillRectangle(&fill, 0, 0, width, height);
    }
  }
  Bitmap buffer(width, height, PixelFormat32bppPARGB);
  {
    Graphics g(&buffer);
    g.DrawImage(g_background_cache.get(), 0, 0, width, height);
    g.SetSmoothingMode(SmoothingModeAntiAlias);
    g.SetTextRenderingHint(TextRenderingHintAntiAliasGridFit);
    g.SetInterpolationMode(InterpolationModeHighQualityBicubic);
    // Darken the art behind the controls (full width).
    const float top = g_layout.y + 470 * g_layout.scale;
    LinearGradientBrush fade(PointF(0, top - 2), PointF(0, float(height) + 2), Color(0, 8, 36, 84),
                             Color(225, 8, 36, 84));
    fade.SetWrapMode(WrapModeTileFlipXY);
    g.FillRectangle(&fade, 0.0f, top, float(width), float(height) - top);
    g.TranslateTransform(g_layout.x, g_layout.y);
    g.ScaleTransform(g_layout.scale, g_layout.scale);
    g_hits.clear();
    if (g_page == Page::kUpdate) {
      PaintUpdate(g);
    } else {
      PaintMain(g);
      DrawInfoPopupOverlay(g);
    }
  }
  Graphics screen(hdc);
  screen.DrawImage(&buffer, 0, 0, width, height);
  EndPaint(window, &ps);
}

int HitTest(LPARAM lparam) {
  const PointF point((float(short(LOWORD(lparam))) - g_layout.x) / g_layout.scale,
                     (float(short(HIWORD(lparam))) - g_layout.y) / g_layout.scale);
  for (auto it = g_hits.rbegin(); it != g_hits.rend(); ++it)
    if (it->rect.Contains(point)) return it->id;
  return -1;
}

// Window size for a given client size.
SIZE FrameFor(int client_width, int client_height) {
  RECT rect{0, 0, client_width, client_height};
  AdjustWindowRectEx(&rect, WS_OVERLAPPEDWINDOW, FALSE, 0);
  return SIZE{rect.right - rect.left, rect.bottom - rect.top};
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
  switch (message) {
    case WM_SIZE:
      UpdateLayout(window);
      InvalidateRect(window, nullptr, FALSE);
      return 0;
    case WM_GETMINMAXINFO: {
      // Smallest window: half of the design size.
      const SIZE minimum = FrameFor(int(kDesignW / 2), int(kDesignH / 2));
      auto* info = reinterpret_cast<MINMAXINFO*>(lparam);
      info->ptMinTrackSize.x = minimum.cx;
      info->ptMinTrackSize.y = minimum.cy;
      return 0;
    }
    case WM_PAINT:
      Paint(window);
      return 0;
    case WM_ERASEBKGND:
      return 1;
    case WM_MOUSEMOVE: {
      const int hover = HitTest(lparam);
      if (hover != g_hover) {
        g_hover = hover;
        SetCursor(LoadCursorW(nullptr, hover >= 0 ? IDC_HAND : IDC_ARROW));
        InvalidateRect(window, nullptr, FALSE);
      }
      TRACKMOUSEEVENT track{sizeof(track), TME_LEAVE, window, 0};
      TrackMouseEvent(&track);
      return 0;
    }
    case WM_MOUSELEAVE:
      if (g_hover != -1) { g_hover = -1; InvalidateRect(window, nullptr, FALSE); }
      return 0;
    case WM_SETCURSOR:
      if (LOWORD(lparam) == HTCLIENT) {
        SetCursor(LoadCursorW(nullptr, g_hover >= 0 ? IDC_HAND : IDC_ARROW));
        return TRUE;
      }
      break;
    case WM_LBUTTONUP: {
      const int hit = HitTest(lparam);
      if (hit >= 0 && hit < int(g_hits.size())) {
        auto action = g_hits[size_t(hit)].action;
        if (action) action();
        g_hover = -1;
        InvalidateRect(window, nullptr, FALSE);
      }
      return 0;
    }
    case WM_KEYDOWN:
      if (wparam == VK_ESCAPE && g_info) {
        g_info.reset();
        InvalidateRect(window, nullptr, FALSE);
      } else if (wparam == VK_ESCAPE && g_page == Page::kUpdate && !UpdateSnapshot().busy) {
        g_page = Page::kMain;
        InvalidateRect(window, nullptr, FALSE);
      }
      return 0;
    case WM_DROPFILES: {
      wchar_t path[MAX_PATH * 4];
      const auto drop = reinterpret_cast<HDROP>(wparam);
      if (!g_busy && DragQueryFileW(drop, 0, path, UINT(std::size(path)))) {
        SelectSource(path);
        InvalidateRect(window, nullptr, FALSE);
      }
      DragFinish(drop);
      return 0;
    }
    case WM_TIMER:
      if (wparam == kProgressTimer || wparam == kUpdateTimer) InvalidateRect(window, nullptr, FALSE);
      return 0;
    case kUpdateChanged:
      if (!UpdateSnapshot().busy) KillTimer(window, kUpdateTimer);
      InvalidateRect(window, nullptr, FALSE);
      return 0;
    case kPatchFinished:
      KillTimer(window, kProgressTimer);
      if (g_worker.joinable()) g_worker.join();
      g_busy = false;
      if (wparam) g_finished_output = OutputPath(g_source);
      InvalidateRect(window, nullptr, FALSE);
      return 0;
    case WM_CLOSE:
      if (g_busy || UpdateSnapshot().busy) {
        MessageBoxW(window, L"Please wait until the current task has finished.", kAppName,
                    MB_OK | MB_ICONINFORMATION);
        return 0;
      }
      break;
    case WM_DESTROY:
      PostQuitMessage(0);
      return 0;
  }
  return DefWindowProcW(window, message, wparam, lparam);
}

void EnableDpiAwareness() {
  if (HMODULE user32 = GetModuleHandleW(L"user32.dll")) {
    using SetContext = BOOL(WINAPI*)(HANDLE);
    if (auto set = reinterpret_cast<SetContext>(GetProcAddress(user32, "SetProcessDpiAwarenessContext")))
      set(reinterpret_cast<HANDLE>(-4));  // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
  }
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR command_line, int show) {
  EnableDpiAwareness();
  ULONG_PTR gdiplus_token = 0;
  GdiplusStartupInput gdiplus_input;
  GdiplusStartup(&gdiplus_token, &gdiplus_input, nullptr);
  if (!LoadPayload()) {
    MessageBoxW(nullptr, L"The patch data inside this program is damaged.", kAppName, MB_OK | MB_ICONERROR);
    return 1;
  }
  g_background = LoadImageResource(kIdBackground);

  WNDCLASSEXW window_class{sizeof(window_class)};
  window_class.lpfnWndProc = WindowProcedure;
  window_class.hInstance = instance;
  window_class.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(kIdIcon));
  window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
  window_class.lpszClassName = L"DoapPatcherWindow";
  RegisterClassExW(&window_class);

  // Opens at the design size, smaller when the screen can't fit it.
  const int screen_w = GetSystemMetrics(SM_CXSCREEN), screen_h = GetSystemMetrics(SM_CYSCREEN);
  const float fit = std::min({1.0f, float(screen_w) * 0.9f / kDesignW, float(screen_h) * 0.85f / kDesignH});
  const SIZE frame = FrameFor(int(kDesignW * fit), int(kDesignH * fit));
  g_window = CreateWindowExW(WS_EX_ACCEPTFILES, window_class.lpszClassName, kAppName, WS_OVERLAPPEDWINDOW,
                             (screen_w - frame.cx) / 2, (screen_h - frame.cy) / 2, frame.cx, frame.cy, nullptr,
                             nullptr, instance, nullptr);
  CleanUpAfterUpdate();
  if (command_line && *command_line) {
    // Also lets an ISO be dropped on the program's icon.
    std::wstring path = command_line;
    path.erase(std::remove(path.begin(), path.end(), L'"'), path.end());
    if (fs::exists(path)) SelectSource(path);
  }
  ShowWindow(g_window, show);
  UpdateWindow(g_window);
  MSG message;
  while (GetMessageW(&message, nullptr, 0, 0)) {
    TranslateMessage(&message);
    DispatchMessageW(&message);
  }
  if (g_worker.joinable()) g_worker.join();
  g_background.reset();
  g_background_cache.reset();
  GdiplusShutdown(gdiplus_token);
  return 0;
}
