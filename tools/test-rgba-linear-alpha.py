#!/usr/bin/env python3
"""Check production RGBA preparation against the original linear-alpha arithmetic.

Executes the complete PrepareImage and upload/retirement constructor bodies from
the existing resource fixture, with real image/token types and recording GL.
Checks every 8-bit channel/alpha pair, mixed packed images and bypass routes.
This is host CPU verification, not Mali pixels or Kodi/device integration.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
resource = runpy.run_path(str(ROOT / 'tools/test-overlay-resource-inputs.py'))


def harness(premultiply):
    code = resource['harness']()
    assert code.endswith(resource['TESTS'])
    code = code[:-len(resource['TESTS'])]
    code = code.replace('#define USE_PREMULTIPLIED_ALPHA 1',
                        f'#define USE_PREMULTIPLIED_ALPHA {premultiply}')
    # Observe accessor admission without altering its arithmetic or lifetime.
    signature = 'const std::array<std::array<uint8_t, 256>, 256>& GetLinearPremultiplyTable()\n{'
    assert code.count(signature) == 1
    code = code.replace(signature, 'int tableRequests = 0;\n' + signature + '\n  ++tableRequests;')
    return code + TESTS


TESTS = r'''
static bool sameTarget(const RenderTargetToken& left, const RenderTargetToken& right)
{
  return left.identity == right.identity && left.generation == right.generation;
}
// Independent spelling of the pre-M1 channel arithmetic, without a lookup.
static int originalChannel(int channel, int alpha)
{
  const float af = alpha / 255.0f;
  const float linear = std::pow(channel / 255.0f, 2.2f) * af;
  if (linear <= 0.0f) return 0;
  if (linear >= 1.0f) return 255;
  return static_cast<int>(std::pow(linear, 1.0f / 2.2f) * 255.0f + 0.5f);
}
static uint32_t originalPixel(uint32_t pixel, bool linear)
{
  const int a = (pixel >> PIXEL_ASHIFT) & 255;
  const auto channel = [a, linear](int c) {
    return linear ? originalChannel(c, a) : (c * a + 127) / 255;
  };
  return (uint32_t(a) << PIXEL_ASHIFT) |
         (uint32_t(channel((pixel >> PIXEL_RSHIFT) & 255)) << PIXEL_RSHIFT) |
         (uint32_t(channel((pixel >> PIXEL_GSHIFT) & 255)) << PIXEL_GSHIFT) |
         (uint32_t(channel((pixel >> PIXEL_BSHIFT) & 255)) << PIXEL_BSHIFT);
}
static CDVDOverlayImage image(int width, int height, const std::vector<uint32_t>& pixels)
{
  CDVDOverlayImage o;
  o.width = width; o.height = height; o.linesize = width * 4;
  o.source_width = 1920; o.source_height = 1080;
  o.pixels.resize(pixels.size() * sizeof(uint32_t));
  if (!pixels.empty()) std::memcpy(o.pixels.data(), pixels.data(), o.pixels.size());
  return o;
}
static void checkImage(const std::vector<uint32_t>& pixels, int width, int height)
{
  auto o = image(width, height, pixels);
  const auto target = renderSystem.CaptureRenderTarget();
  const int before = tableRequests;
  const auto prepared = COverlayTextureGLES::PrepareImage(o, false, target);
  std::vector<uint32_t> expected;
  for (uint32_t p : pixels)
    expected.push_back(USE_PREMULTIPLIED_ALPHA ? originalPixel(p, true) : p);
  assert(prepared.pixels == expected && "linear packed image bytes");
  assert(prepared.stride == width * 4 && sameTarget(prepared.target, target));
  assert(!prepared.rawPqMenu && !prepared.plainPmaMenu);
  assert(prepared.premultiplied == bool(USE_PREMULTIPLIED_ALPHA));
  assert(tableRequests == before + USE_PREMULTIPLIED_ALPHA);
  // Upload consumes the owned prepared bytes after producer content changes.
  o.pixels.assign(o.pixels.size(), 0);
  CRect source(0, 0, 1920, 1080);
  const int name = generated + 1;
  {
    COverlayTextureGLES texture(o, source, prepared);
    assert(texture.IsValid() && uploaded == expected && "prepared upload bytes");
  }
  assert(renderSystem.resources->TakeRetired() == std::vector<uint32_t>{uint32_t(name)});
}
int main()
{
  const auto target = renderSystem.CaptureRenderTarget();
  const std::vector<uint32_t> colors{0x80ffffff, 0xff123456, 0x40abcdef, 0x00123456};
  // Raw-PQ and plain menu rows remain stride-aware, including dirty rectangles.
  auto padded = image(2, 2, {colors[0], colors[1], 0xdeadbeef,
                             colors[2], colors[3], 0xdeadbeef});
  padded.linesize = 12;
  std::vector<uint32_t> plain;
  for (uint32_t p : colors) plain.push_back(originalPixel(p, false));
  for (bool raw : {false, true}) for (bool plainMenu : {false, true})
  {
    if (!raw && !plainMenu) continue;
    auto prepared = COverlayTextureGLES::PrepareImage(padded, raw, target, plainMenu);
    if (raw || USE_PREMULTIPLIED_ALPHA)
      assert(prepared.pixels == plain && prepared.stride == 8 && "plain and raw rows");
    else
      assert(prepared.pixels == std::vector<uint32_t>({colors[0], colors[1], 0xdeadbeef,
                   colors[2], colors[3], 0xdeadbeef}) && prepared.stride == 12);
    assert(prepared.premultiplied == bool(raw || USE_PREMULTIPLIED_ALPHA));
    assert(prepared.rawPqMenu == raw && prepared.plainPmaMenu == plainMenu);
    assert(sameTarget(prepared.target, target) && generated == 0 && uploads == 0);
  }
  // Indexed HDMV/PGS retains palette-scale math and the raw menu palette choice.
  CDVDOverlayImage indexed;
  indexed.width = indexed.height = 2; indexed.linesize = 3;
  indexed.pixels = {0, 1, 255, 1, 0, 255};
  indexed.palette = {colors[0], colors[1]};
  for (bool separatePalette : {false, true})
  {
    indexed.pqMenuPalette = separatePalette ? std::vector<uint32_t>{colors[2], colors[3]} :
                                             std::vector<uint32_t>{};
    for (bool raw : {false, true}) for (bool plainMenu : {false, true})
    {
      const auto& palette = raw && separatePalette ? indexed.pqMenuPalette : indexed.palette;
      std::vector<uint32_t> expected;
      for (int i : {0, 1, 1, 0})
        expected.push_back(raw || USE_PREMULTIPLIED_ALPHA ?
                            originalPixel(palette[i], !raw && !plainMenu) : palette[i]);
      auto prepared = COverlayTextureGLES::PrepareImage(indexed, raw, target, plainMenu);
      assert(prepared.pixels == expected && prepared.stride == 8 && "indexed bypass bytes");
      assert(sameTarget(prepared.target, target) && generated == 0 && uploads == 0);
    }
  }
  assert(tableRequests == 0 && "bypass must not request the linear lookup");
  // Exercise all channel/alpha pairs through the complete eligible image path.
  std::vector<uint32_t> exhaustive;
  for (uint32_t a = 0; a < 256; ++a) for (uint32_t c = 0; c < 256; ++c)
    exhaustive.push_back((a << 24) | (c << 16) | ((255 - c) << 8) | ((73 * c + 17 * a) & 255));
  checkImage(exhaustive, 256, 256);
  uint32_t seed = 1729;
  for (const auto& size : std::vector<std::pair<int, int>>{{1, 1}, {1, 17}, {19, 1}, {37, 23}})
  {
    std::vector<uint32_t> mixed(size.first * size.second);
    for (auto& pixel : mixed) { seed = seed * 1664525u + 1013904223u; pixel = seed; }
    checkImage(mixed, size.first, size.second);
  }
  // Empty preparation keeps metadata and never manufactures a pixel.
  auto empty = image(0, 0, {});
  auto result = COverlayTextureGLES::PrepareImage(empty, false, target);
  assert(result.pixels.empty() && result.stride == 0 && sameTarget(result.target, target));
  if (USE_PREMULTIPLIED_ALPHA)
  {
    const auto& first = GetLinearPremultiplyTable();
    assert(sizeof(first) == 65536 && &first == &GetLinearPremultiplyTable());
    for (int a = 0; a < 256; ++a) for (int c = 0; c < 256; ++c)
      assert(first[a][c] == originalChannel(c, a) && "lookup channel equivalence");
  }
}
'''


def run(out, controls):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'PlatformDefs.h').write_text('#pragma once\n#define PIXEL_ASHIFT 24\n'
                                       '#define PIXEL_RSHIFT 16\n#define PIXEL_GSHIFT 8\n'
                                       '#define PIXEL_BSHIFT 0\n')
    positive = harness(1)
    jobs = [('premultiplied', positive, None), ('unpremultiplied', harness(0), None)]
    if controls:
        for name, old, new, assertion in [
            ('plain-transfer', 'LinearToSrgb8(SrgbToLinear(static_cast<int>(channel)) * af)',
             'static_cast<int>(channel * af + 0.5f)', 'linear packed image bytes'),
            ('wrong-alpha-row', 'const auto& channels = table[a];',
             'const auto& channels = table[255 - a];', 'linear packed image bytes'),
            ('swapped-channels', 'channels[(src[i] >> PIXEL_RSHIFT) & 0xff]',
             'channels[(src[i] >> PIXEL_BSHIFT) & 0xff]', 'linear packed image bytes'),
            ('eager-bypass-lookup', 'image.rawPqMenu = rawPqMenu;',
             'image.rawPqMenu = rawPqMenu; (void)OVERLAY::GetLinearPremultiplyTable();',
             'bypass must not request the linear lookup'),
        ]:
            assert positive.count(old) == 1, name
            jobs.append((name, positive.replace(old, new), assertion))
    reports = []
    for name, code, assertion in jobs:
        source = out / f'{name}.cpp'
        source.write_text(code)
        binary = out / name
        argv = [os.environ.get('CXX', 'g++'), '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                '-I', str(out), '-I', str(ROOT / 'xbmc'), str(source), '-o', str(binary)]
        compiled = subprocess.run(argv, capture_output=True, text=True)
        (out / f'{name}-compile.log').write_text(compiled.stdout + compiled.stderr)
        assert compiled.returncode == 0, f'{name} did not compile: {compiled.stderr}'
        executed = subprocess.run([str(binary)], capture_output=True, text=True)
        (out / f'{name}-run.log').write_text(executed.stdout + executed.stderr)
        if assertion is None:
            assert executed.returncode == 0, f'{name} failed: {executed.stderr}'
        else:
            assert executed.returncode == -6 and assertion in executed.stderr, \
                f'{name} did not fail its named assertion: {executed.stderr}'
        reports.append({'name': name, 'compile_argv': argv, 'compile_exit': compiled.returncode,
                        'run_argv': [str(binary)], 'run_exit': executed.returncode,
                        'expected_assertion': assertion})
    (out / 'results.json').write_text(json.dumps(reports, indent=2) + '\n')
    print(f'RGBA linear alpha: PASS (2 production image variants; {len(jobs)-2} runtime controls; ASan/UBSan)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--negative-controls', action='store_true')
    args = parser.parse_args()
    if args.output:
        run(args.output, args.negative_controls)
    else:
        with tempfile.TemporaryDirectory(prefix='rgba-linear-alpha-') as folder:
            run(Path(folder), args.negative_controls)
